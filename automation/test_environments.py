from datetime import timedelta
from django.utils import timezone
from django.test import TestCase
from django.contrib.auth.models import Permission
from rest_framework.test import APIClient
from . import tests as platform_tests
from .models import Environment, EnvironmentSystem, EnvironmentSession, Endpoint, TestRun
from .security import encrypt, decrypt
from .environment_auth import auth_status
from .runner import run_http
from .serializers import EnvironmentSerializer


class EnvironmentAccessTests(TestCase):
    def setUp(self):
        platform_tests.PlatformTests.setUp(self)
        self.public = Environment.objects.create(
            owner=self.admin, project=self.project, scope='public',
            name='Shared', code='test')
        self.public_system = EnvironmentSystem.objects.create(
            environment=self.public, system_key='axiom_api',
            name='Axiom API', base_url='http://127.0.0.1:8000')
        self.project.members.add(self.other)
        self.admin.current_project = self.project
        self.admin.save(update_fields=['current_project'])
        self.other.current_project = self.project
        self.other.save(update_fields=['current_project'])
        self.role.permissions.remove(*self.role.permissions.filter(codename='manage_public_environment'))

    def test_public_readable_without_role_and_current_is_account_bound(self):
        self.client.force_authenticate(self.other)
        response = self.client.get('/api/v1/environments/?scope=public')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertFalse(response.data['results'][0]['can_edit'])
        self.assertEqual(self.client.post(f'/api/v1/environments/{self.public.pk}/activate/').status_code, 200)
        self.assertEqual(self.client.get('/api/v1/environments/current/').data['environment']['id'], self.public.pk)
        self.client.force_authenticate(self.user)
        self.assertIsNone(self.client.get('/api/v1/environments/current/').data['environment'])
        anonymous = APIClient()
        self.assertEqual(anonymous.get('/api/v1/environments/').status_code, 401)

    def test_private_invisible_even_to_other_admin(self):
        self.client.force_authenticate(self.admin)
        for action in ['', 'activate/', 'login-system/']:
            url = f'/api/v1/environments/{self.env.pk}/' + action
            response = self.client.get(url) if not action else self.client.post(url)
            self.assertEqual(response.status_code, 404)

    def test_public_maintenance_and_private_owner_enforced(self):
        response = self.client.patch(f'/api/v1/environments/{self.public.pk}/', {'name':'changed'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post('/api/v1/environments/', {
            'scope':'public','name':'new','code':'new'}, format='json').status_code, 403)
        variables = self.client.put(
            f'/api/v1/environments/{self.public.pk}/personal-variables/',
            {'variables': {'account_token': 'mine'}}, format='json')
        self.assertEqual(variables.status_code, 200, variables.data)
        mine = self.client.get('/api/v1/environments/').data['results'][0]
        self.assertEqual(mine['personal_variables'], {'account_token': 'mine'})
        self.client.force_authenticate(self.other)
        other = self.client.get('/api/v1/environments/').data['results'][0]
        self.assertEqual(other['personal_variables'], {})
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f'/api/v1/environments/{self.public.pk}/', {'name':'changed'})
        self.assertEqual(response.status_code, 200)

    def test_public_snapshot_does_not_include_personal_locations(self):
        Environment.objects.create(owner=self.admin, project=self.project,
            scope='private',name='hidden',code='hidden',
            credentials=encrypt({'connections':{'redis':[{
                'name':'hidden','host':'localhost','port':6379,'password':'secret-location'}]}}))
        res = self.client.post('/api/v1/runs/', {'project':self.project.pk,
            'environment':self.public.pk,'case_ids':[self.case.pk]}, format='json')
        self.assertEqual(res.status_code,201,res.data)
        snapshot = decrypt(TestRun.objects.get(pk=res.data['id']).snapshot)
        self.assertEqual(len(snapshot['environment']['systems']),1)
        self.assertNotIn('secret-location',str(snapshot))

    def test_duplicate_public_keys_and_address_validation(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post('/api/v1/environments/', {
            'name':'Duplicate','scope':'public','code':'test'}, format='json')
        self.assertEqual(response.status_code,400)
        response = self.client.patch(f'/api/v1/environments/{self.public.pk}/',
            {'systems':[{'system_key':'bad key','name':'Bad','base_url':'ftp://invalid'}]},format='json')
        self.assertEqual(response.status_code,400)

    def test_missing_address_key_never_falls_back(self):
        with self.assertRaisesRegex(ValueError,'missing'):
            run_http({'endpoint_data':{'system_key':'missing','method':'GET','path':'/','body_type':'json'}},
                {'systems_by_key':{},'headers':{}}, {}, None)

    def test_same_endpoint_uses_matching_system_in_each_environment(self):
        production = Environment.objects.create(
            owner=self.admin, project=self.project, scope='public',
            name='生产环境', code='production')
        EnvironmentSystem.objects.create(
            environment=production, system_key='axiom_api',
            name='Axiom API', base_url='https://prod.example.test')
        test_snapshot = EnvironmentSerializer(self.public).data
        prod_snapshot = EnvironmentSerializer(production).data
        self.assertEqual(
            test_snapshot['systems_by_key']['axiom_api']['base_url'],
            'http://127.0.0.1:8000')
        self.assertEqual(
            prod_snapshot['systems_by_key']['axiom_api']['base_url'],
            'https://prod.example.test')

    def test_endpoint_and_case_use_current_project_and_default_system(self):
        endpoint = self.client.post('/api/v1/endpoints/', {
            'name':'自动关联接口', 'function_name':'auto_linked',
            'method':'GET', 'path':'/api/v1/health/',
            'body_type':'json'}, format='json')
        self.assertEqual(endpoint.status_code, 201, endpoint.data)
        self.assertEqual(endpoint.data['project'], self.project.pk)
        self.assertEqual(endpoint.data['system_key'], 'axiom_api')
        case = self.client.post('/api/v1/cases/', {
            'name':'自动关联用例', 'endpoint':endpoint.data['id'],
            'kind':'http', 'priority':'P1'}, format='json')
        self.assertEqual(case.status_code, 201, case.data)
        self.assertEqual(case.data['project'], self.project.pk)

    def test_environment_api_encrypts_and_preserves_infrastructure_credentials(self):
        self.client.force_authenticate(self.admin)
        payload = {
            'scope':'public', 'name':'集成测试环境', 'code':'integration',
            'systems':[{
                'system_key':'user_server','name':'用户中心',
                'base_url':'https://user.test.example.com','verify_ssl':True,
                'credentials':{'client_id':'test-client','client_secret':'system-secret'},
                'auth_config':{}
            }],
            'connections':{
                'mysql':[{'name':'main','host':'mysql.test','port':3306,
                          'username':'tester','password':'mysql-secret','database':'app'}],
                'postgresql':[{'name':'report','host':'pg.test','port':5432,
                               'username':'reporter','password':'pg-secret','database':'report'}],
                'redis':[{'name':'cache','host':'redis.test','port':6379,
                          'password':'redis-secret','database':'2'}],
                'mqtt':[{'name':'broker','host':'mqtt.test','port':1883,
                         'username':'device','password':'mqtt-secret',
                         'extra':{'client_id':'runner','token':'nested-secret'}}]
            }
        }
        created = self.client.post('/api/v1/environments/', payload, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        text = str(created.data)
        for secret in ['system-secret','mysql-secret','pg-secret','redis-secret',
                       'mqtt-secret','nested-secret']:
            self.assertNotIn(secret, text)
        self.assertTrue(created.data['systems'][0]['has_credentials'])
        self.assertTrue(created.data['connection_summary']['mysql'][0]['has_credentials'])
        self.assertNotIn('token', str(created.data['connection_summary']['mqtt'][0]))
        env = Environment.objects.get(pk=created.data['id'])
        private = decrypt(env.credentials)
        self.assertEqual(private['connections']['mysql'][0]['password'],'mysql-secret')
        system = env.systems.get(system_key='user_server')
        self.assertEqual(decrypt(system.credentials)['client_secret'],'system-secret')

        payload['systems'][0].pop('credentials')
        for rows in payload['connections'].values():
            rows[0]['password'] = ''
        updated = self.client.patch(
            f'/api/v1/environments/{env.pk}/', payload, format='json')
        self.assertEqual(updated.status_code, 200, updated.data)
        env.refresh_from_db()
        self.assertEqual(
            decrypt(env.credentials)['connections']['postgresql'][0]['password'],
            'pg-secret')
        system.refresh_from_db()
        self.assertEqual(decrypt(system.credentials)['client_secret'],'system-secret')

        run = self.client.post('/api/v1/runs/', {
            'project':self.project.pk, 'environment':env.pk,
            'case_ids':[self.case.pk]}, format='json')
        self.assertEqual(run.status_code, 201, run.data)
        snapshot = decrypt(TestRun.objects.get(pk=run.data['id']).snapshot)
        self.assertEqual(
            snapshot['environment']['secrets']['connections']['mqtt'][0]['password'],
            'mqtt-secret')
        self.assertEqual(
            snapshot['environment']['system_credentials']['user_server']['client_secret'],
            'system-secret')
        self.assertNotIn('mqtt-secret', str(run.data))

    def test_login_token_injection_debug_and_account_isolation(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append(data)
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.end_headers()
                self.wfile.write(b'{"data":{"token":"account-token-123456"}}')
            def do_GET(self):
                received.append(self.headers.get('Authorization'))
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true,"token":"account-token-123456"}')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            self.public_system.base_url = f'http://127.0.0.1:{server.server_port}'
            login_endpoint = Endpoint.objects.create(
                project=self.project, name='登录', function_name='linked_login',
                system_key='axiom_api', method='POST', path='/login',
                body={'username':'{{username}}','password':'{{password}}'},
                use_auth=False)
            self.public_system.auth_config = {}
            self.public_system.login_endpoint = login_endpoint
            self.public_system.login_extract = {'access_token':'$.data.token'}
            self.public_system.headers = {
                'Authorization':'Bearer {{access_token}}'}
            self.public_system.save()
            variables = self.client.put(
                f'/api/v1/environments/{self.public.pk}/personal-variables/',
                {'variables': {'username':'test-account',
                               'password':'private-password'}}, format='json')
            self.assertEqual(variables.status_code, 200, variables.data)
            debug = self.client.post(f'/api/v1/endpoints/{self.endpoint.pk}/debug/',
                {'environment':self.public.pk},format='json')
            self.assertEqual(debug.status_code,200,debug.data)
            self.assertEqual(received[0]['username'],'test-account')
            token = EnvironmentSession.objects.get(
                owner=self.user,environment=self.public,system_key='axiom_api')
            self.assertNotIn('account-token',token.token)
            self.assertEqual(received[-1],'Bearer account-token-123456')
            self.assertNotIn('account-token-123456',str(debug.data))
            without_auth = self.client.post(
                f'/api/v1/endpoints/{self.endpoint.pk}/debug/',
                {'environment':self.public.pk,
                 'overrides':{'use_auth':False}}, format='json')
            self.assertEqual(without_auth.status_code, 200, without_auth.data)
            self.assertIsNone(received[-1])
            self.assertFalse(auth_status(self.other,self.public)['axiom_api']['authenticated'])
            request_count = len(received)
            token.expires_at = timezone.now() - timedelta(seconds=1)
            token.save()
            debug = self.client.post(f'/api/v1/endpoints/{self.endpoint.pk}/debug/',
                {'environment':self.public.pk},format='json')
            self.assertEqual(debug.status_code,200,debug.data)
            self.assertEqual(len(received), request_count + 2)
            self.assertEqual(received[-1],'Bearer account-token-123456')
        finally:
            server.shutdown(); server.server_close(); thread.join()
