import tempfile
from pathlib import Path
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase as DjangoTestCase, override_settings
from django.core.management import call_command
from rest_framework.test import APIClient
from .models import Project, Environment, EnvironmentSystem, Endpoint, TestCase, TestRun
from .security import encrypt, decrypt
from .runner import render, json_path, validate_url, run_http
from .scanner import scan_cases
from integrations.api_client import AxiomApiClient

User = get_user_model()
PASSWORD = 'Test-Local-78!strong'


class PlatformTests(DjangoTestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin', password=PASSWORD)
        self.user = User.objects.create_user('tester', password=PASSWORD)
        self.other = User.objects.create_user('other', password=PASSWORD)
        self.role = Group.objects.create(name='tester')
        self.role.permissions.set(Permission.objects.filter(content_type__app_label='automation'))
        self.user.groups.add(self.role)
        self.project = Project.objects.create(name='P', owner=self.user)
        self.foreign = Project.objects.create(name='Private', owner=self.other)
        self.user.current_project = self.project
        self.user.save(update_fields=['current_project'])
        self.other.current_project = self.foreign
        self.other.save(update_fields=['current_project'])
        self.env = Environment.objects.create(owner=self.user, project=self.project,
            name='Local', code='local',
            credentials=encrypt({'connections': {'redis':[{
                'name':'cache','host':'127.0.0.1','port':6379,'password':'secret-12345'}]}}))
        EnvironmentSystem.objects.create(environment=self.env, system_key='axiom_api',
            name='Axiom API', base_url='http://127.0.0.1:8000')
        self.endpoint = Endpoint.objects.create(project=self.project, name='Health',
            function_name='health_check', system_key='axiom_api', path='/api/v1/health/')
        self.case = TestCase.objects.create(project=self.project, name='Health', endpoint=self.endpoint)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_login_and_password_revoke(self):
        client = APIClient()
        res = client.post('/api/v1/auth/login/', {'username':'tester','password':PASSWORD})
        self.assertEqual(res.status_code, 200)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + res.data['access'])
        self.assertEqual(client.get('/api/v1/auth/me/').status_code, 200)
        self.assertEqual(client.post('/api/v1/auth/password/', {
            'old_password':PASSWORD, 'new_password':'New-Password-45!long'}).status_code, 200)
        self.assertEqual(client.get('/api/v1/auth/me/').status_code, 401)

    def test_project_and_environment_isolation(self):
        self.assertEqual(self.client.get('/api/v1/projects/').data['count'], 1)
        self.assertEqual(self.client.get(f'/api/v1/projects/{self.foreign.pk}/').status_code, 404)
        env = Environment.objects.create(
            owner=self.other, project=self.foreign, name='Other', code='test')
        self.assertEqual(self.client.post(f'/api/v1/environments/{env.pk}/activate/').status_code, 404)

    def test_project_switch_scopes_resources_and_blocks_related_delete(self):
        second = Project.objects.create(name='Second', owner=self.user)
        response = self.client.get('/api/v1/endpoints/')
        self.assertEqual(response.data['count'], 1)
        switched = self.client.post(f'/api/v1/projects/{second.pk}/activate/')
        self.assertEqual(switched.status_code, 200, switched.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.current_project_id, second.pk)
        self.assertEqual(self.client.get('/api/v1/endpoints/').data['count'], 0)
        self.assertEqual(
            self.client.delete(f'/api/v1/projects/{self.project.pk}/').status_code,
            400)
        self.assertEqual(self.client.post('/api/v1/endpoints/', {
            'project':self.foreign.pk,'name':'bad','path':'/'}).status_code, 400)

    def test_rbac_and_no_user_escalation(self):
        self.assertEqual(self.client.get('/api/v1/users/').status_code, 403)
        self.assertEqual(self.client.patch('/api/v1/auth/me/', {'is_superuser':True}, format='json').status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post('/api/v1/users/', {
            'username':'weak','password':'123'}, format='json').status_code, 400)

    def test_secrets_encrypted_and_not_returned(self):
        res = self.client.get(f'/api/v1/environments/{self.env.pk}/')
        self.assertNotIn('credentials', res.data)
        self.assertNotIn('secrets', res.data)
        self.assertNotIn('secret-12345', self.env.credentials)
        self.assertEqual(decrypt(self.env.credentials)['connections']['redis'][0]['password'], 'secret-12345')

    def test_run_snapshot_immutable_and_cancel(self):
        res = self.client.post('/api/v1/runs/', {
            'project':self.project.pk,'environment':self.env.pk,'case_ids':[self.case.pk]}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        self.endpoint.path = '/changed'
        self.endpoint.save()
        run = TestRun.objects.get(pk=res.data['id'])
        self.assertEqual(decrypt(run.snapshot)['cases'][0]['endpoint_data']['path'], '/api/v1/health/')
        self.assertEqual(decrypt(run.snapshot)['environment']['api_catalog'][0]['function_name'], 'health_check')
        self.assertNotIn('snapshot', res.data)
        self.assertEqual(self.client.post(f'/api/v1/runs/{run.pk}/cancel/').status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.status, 'cancelled')

    def test_cross_project_suite_and_run_rejected(self):
        case = TestCase.objects.create(project=self.foreign, name='private')
        self.assertEqual(self.client.post('/api/v1/suites/', {
            'name':'invalid','project':self.project.pk,'case_ids':[case.pk]}, format='json').status_code, 400)
        self.assertEqual(self.client.post('/api/v1/runs/', {
            'project':self.project.pk,'environment':self.env.pk,'case_ids':[case.pk]}, format='json').status_code, 400)

    def test_scan_preserves_class_identity_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'test_sample.py').write_text(
                'class TestA:\n def test_same(self): pass\nclass TestB:\n def test_same(self): pass\ndef test_free(): pass\n')
            with override_settings(PYTEST_ROOT=Path(directory)):
                self.assertEqual(scan_cases(self.project)['added'], 3)
                self.assertEqual(scan_cases(self.project)['updated'], 3)

    def test_pytest_worker_and_report(self):
        scan_cases(self.project)
        ids = list(TestCase.objects.filter(project=self.project,kind='pytest').values_list('id',flat=True))
        res = self.client.post('/api/v1/runs/', {
            'project':self.project.pk,'environment':self.env.pk,'case_ids':ids}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        call_command('runworker', once=True)
        run = TestRun.objects.get(pk=res.data['id'])
        self.assertEqual(run.status, 'passed', list(run.results.values('error','log')))
        self.assertEqual(run.passed, 2)
        self.assertEqual(self.client.get(f'/api/v1/runs/{run.pk}/report/').status_code, 200)

    def test_http_variables_assertions_and_extraction(self):
        import requests
        from unittest.mock import MagicMock
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"data":{"id":42}}'
        response.iter_content = lambda _: [response._content]
        session = MagicMock()
        session.request.return_value.__enter__.return_value = response
        variables = {'name':'demo'}
        case = {'endpoint_data':{'path':'/echo','method':'POST','system_key':'api',
            'body_type':'json','body':{'name':'{{name}}'}}, 'assertions':[
                {'source':'json','path':'$.data.id','operator':'eq','expected':42}], 'extract':{'id':'$.data.id'}}
        result = run_http(case, {'systems_by_key':{'api':{
            'base_url':'http://127.0.0.1:8000','verify_ssl':True,'auth_config':{}}},
            'headers':{}}, variables, session)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(variables['id'], 42)
        self.assertEqual(session.request.call_args.kwargs['json'], {'name':'demo'})
        self.assertFalse(session.request.call_args.kwargs['allow_redirects'])

    def test_outbound_hosts_and_variables(self):
        with self.assertRaises(ValueError):
            validate_url('http://169.254.169.254/latest/meta-data/')
        with self.assertRaises(ValueError):
            validate_url('file:///etc/passwd')
        with self.assertRaises(ValueError):
            render('{{missing}}', {})
        self.assertEqual(render('{{id}}', {'id':42}), 42)
        self.assertEqual(json_path({'data':[{'x':3}]}, '$.data[0].x'), 3)

    def test_role_manager_cannot_grant_higher_permissions(self):
        perm = Permission.objects.get(codename='manage_roles')
        self.user.user_permissions.add(perm)
        admin_role = Group.objects.create(name='admin-only')
        admin_role.permissions.set(Permission.objects.all())
        self.assertEqual(self.client.patch(f'/api/v1/roles/{admin_role.pk}/',
            {'name':'takeover'}, format='json').status_code, 403)
        self.assertEqual(self.client.post('/api/v1/roles/', {'name':'escalated',
            'permissions':[Permission.objects.get(codename='manage_users').pk]}, format='json').status_code, 403)
        self.client.force_authenticate(self.admin)
        public_permission = Permission.objects.get(codename='manage_public_environment')
        self.assertEqual(self.client.post('/api/v1/roles/', {'name':'public-admin',
            'permissions':[public_permission.pk]}, format='json').status_code,400)
        visible = self.client.get('/api/v1/roles/permissions/').data
        self.assertNotIn(public_permission.pk, [item['id'] for item in visible])
        endpoint_permission = next(item for item in visible if item['code'] == 'automation.view_endpoint')
        self.assertEqual(endpoint_permission['page_path'],'/endpoints')
        self.assertEqual(endpoint_permission['page_name'],'接口管理')
        self.assertEqual(endpoint_permission['action_name'],'查看')

    def test_endpoint_callable_method_validation(self):
        self.assertEqual(self.client.post('/api/v1/endpoints/', {
            'project':self.project.pk,'name':'Device','function_name':'get_device_detail',
            'system_key':'axiom_api',
            'path':'/devices/{device_id}'},format='json').status_code,201)
        self.assertEqual(self.client.post('/api/v1/endpoints/', {
            'project':self.project.pk,'name':'Duplicate','function_name':'get_device_detail',
            'system_key':'axiom_api',
            'path':'/other'},format='json').status_code,400)
        self.assertEqual(self.client.post('/api/v1/endpoints/', {
            'project':self.project.pk,'name':'Bad','function_name':'Get-Device',
            'system_key':'axiom_api',
            'path':'/bad'},format='json').status_code,400)

    def test_dynamic_code_client_calls_saved_method(self):
        from unittest.mock import MagicMock
        import requests
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"ok":true}'
        client = AxiomApiClient({'systems_by_key':{'api':{
            'base_url':'http://api.example.test','verify_ssl':True,'auth_config':{}}},
            'variables':{},'secrets':{},'headers':{},'auth_tokens':{},
            'api_catalog':[{'function_name':'health_check','system_key':'api','path':'/health',
                'method':'GET','headers':{},'query':{},'body':{},'body_type':'json','use_auth':False}]})
        client.session.request = MagicMock(return_value=response)
        result = client.health_check(query={'verbose':True}).assert_success()
        self.assertEqual(result.data, {'ok':True})
        self.assertEqual(client.session.request.call_args.args[:2], ('GET','http://api.example.test/health'))
        self.assertEqual(client.session.request.call_args.kwargs['params'], {'verbose':True})
