import os
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from automation.models import Project, Environment, EnvironmentSystem, Endpoint, TestCase, Suite
from automation.scanner import scan_cases


class Command(BaseCommand):
    help = 'Initialize roles and optional demo project. Existing passwords are preserved.'

    def add_arguments(self, parser):
        parser.add_argument('--demo', action='store_true')

    def handle(self, *args, **options):
        permissions = Permission.objects.filter(content_type__app_label__in=['accounts', 'automation'])
        admin, _ = Group.objects.get_or_create(name='平台管理员')
        admin.permissions.set(permissions.exclude(codename='manage_public_environment'))
        tester, _ = Group.objects.get_or_create(name='测试工程师')
        tester.permissions.set(permissions.filter(content_type__app_label='automation').exclude(
            content_type__model__in=['auditlog', 'caseresult']).exclude(codename='manage_public_environment'))
        viewer, _ = Group.objects.get_or_create(name='只读观察员')
        viewer.permissions.set(permissions.filter(codename__startswith='view_').exclude(
            content_type__model__in=['user', 'auditlog']))
        User = get_user_model()
        username = os.getenv('AXIOM_ADMIN_USER', 'admin')
        user = User.objects.filter(username=username).first()
        if not user:
            password = os.getenv('AXIOM_ADMIN_PASSWORD')
            if not password:
                raise CommandError('Set AXIOM_ADMIN_PASSWORD to create the initial admin.')
            from django.contrib.auth.password_validation import validate_password
            validate_password(password, User(username=username))
            user = User.objects.create_superuser(username=username, password=password,
                                                  display_name='系统管理员')
        if options['demo']:
            project, _ = Project.objects.get_or_create(name='智能家居 API', owner=user,
                defaults={'description': '环境、接口与设备场景的自动化回归测试'})
            public_env, _ = Environment.objects.get_or_create(
                project=project, scope='public', code='local',
                defaults={'owner': user, 'name': '本地演示环境',
                          'variables': {'device_id': 'light-001'}})
            EnvironmentSystem.objects.get_or_create(environment=public_env, system_key='axiom_api',
                defaults={'name':'Axiom API','base_url':'http://127.0.0.1:8000'})
            env = Environment.objects.filter(
                owner=user, scope='private', code='local').first()
            if env:
                if not env.public_environment_id:
                    env.public_environment = public_env
                    env.save(update_fields=['public_environment'])
            else:
                env = Environment.objects.create(
                    owner=user, project=project, scope='private',
                    public_environment=public_env,
                    name=public_env.name, code=public_env.code)
            user.current_environment = env
            user.current_project = project
            user.save(update_fields=['current_environment', 'current_project'])
            ids = []
            for name, path in [('服务健康检查', '/api/v1/health/'), ('设备响应校验', '/api/v1/demo/echo/')]:
                method_name = 'health_check' if '健康' in name else 'device_echo'
                endpoint, _ = Endpoint.objects.get_or_create(project=project, name=name,
                    defaults={'function_name': method_name, 'path': path, 'method': 'GET', 'system_key': 'axiom_api'})
                if not endpoint.function_name:
                    endpoint.function_name = method_name
                    endpoint.save(update_fields=['function_name'])
                case, _ = TestCase.objects.get_or_create(project=project, name=name,
                    defaults={'endpoint': endpoint, 'priority': 'P0', 'author': 'Axiom',
                              'assertions': [{'source': 'status', 'operator': 'eq', 'expected': 200}]})
                ids.append(case.id)
            Suite.objects.get_or_create(project=project, name='核心接口冒烟测试',
                defaults={'case_ids': ids, 'description': '验证服务健康与设备基础响应'})
            scan_cases(project)
        self.stdout.write(self.style.SUCCESS('Roles and project initialized. Existing passwords unchanged.'))
