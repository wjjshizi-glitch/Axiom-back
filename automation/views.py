import html
import json
import requests
from datetime import timedelta
from django.conf import settings
from django.db.models import Count, Sum
from django.db.models.deletion import ProtectedError
from django.db.models.functions import TruncDate
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .models import Project, Environment, EnvironmentSystem, EnvironmentSession, Endpoint, TestCase, Suite, TestRun, AuditLog
from .serializers import (ProjectSerializer, EnvironmentSerializer, EndpointSerializer, CaseSerializer,
                          SuiteSerializer, RunSerializer, RunDetailSerializer, AuditSerializer)
from .security import RBACPermission, EnvironmentPermission, environment_scope, audit, project_scope, encrypt, decrypt
from .scanner import scan_cases
from .environment_auth import (
    attach_sessions, store_token, store_login_variables, auth_status, system_for,
    session_for)
from .runner import run_http, json_path, redact, leaf_values


def account_variables(user, env):
    if env.scope == 'private':
        return {**env.variables}
    private = Environment.objects.filter(
        scope='private', owner=user,
        public_environment=env).only('variables').first()
    return {**(private.variables if private else {})}


def execute_linked_login(user, env, system, credentials=None):
    endpoint = system.login_endpoint
    if not endpoint or endpoint.system_key != system.system_key:
        raise ValidationError('当前系统未正确关联登录接口')
    if not system.login_extract:
        raise ValidationError('请先配置登录响应变量提取规则')
    stored_credentials = decrypt(system.credentials)
    data = dict(EnvironmentSerializer(env).data)
    data['systems_by_key'][system.system_key]['headers'] = {}
    case = {
        'endpoint_data': dict(EndpointSerializer(endpoint).data),
        'request_overrides': {'use_auth': False},
        'assertions': [],
        'extract': {},
    }
    try:
        with requests.Session() as session:
            session.trust_env = False
            result = run_http(case, data, {
                **account_variables(user, env),
                **stored_credentials, **(credentials or {})}, session)
        if not 200 <= result['response']['status'] < 300:
            raise ValidationError('关联登录接口返回失败，请检查登录参数')
        extracted = {
            name: json_path(result['response']['body'], path)
            for name, path in system.login_extract.items()}
        store_login_variables(user, env, system, extracted)
        return extracted
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(f'登录接口执行或响应变量提取失败：{exc}')


def ensure_linked_login(user, env, system_key, force=False):
    system = system_for(env, system_key)
    if not system or not system.login_endpoint_id:
        return
    if force or not session_for(user, env, system_key):
        execute_linked_login(user, env, system)


class AuditedViewSet(viewsets.ModelViewSet):
    permission_classes = [RBACPermission]

    def perform_create(self, serializer):
        obj = serializer.save()
        audit(self.request.user, 'create', obj._meta.model_name, obj.pk)

    def perform_update(self, serializer):
        obj = serializer.save()
        audit(self.request.user, 'update', obj._meta.model_name, obj.pk)

    def perform_destroy(self, instance):
        oid = instance.pk
        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError('该记录已有执行历史，不能删除')
        audit(self.request.user, 'delete', instance._meta.model_name, oid)


class ProjectViewSet(AuditedViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    search_fields = ['name', 'description']
    permission_map = {'activate': 'automation.view_project'}

    def get_queryset(self):
        return project_scope(self.request.user)

    def perform_create(self, serializer):
        obj = serializer.save(owner=self.request.user)
        if not self.request.user.current_project_id:
            self.request.user.current_project = obj
            self.request.user.current_environment = None
            self.request.user.save(update_fields=[
                'current_project', 'current_environment'])
        audit(self.request.user, 'create', 'project', obj.pk)

    def perform_destroy(self, instance):
        if not self.request.user.is_superuser and instance.owner_id != self.request.user.id:
            raise PermissionDenied('只有项目所有者可以删除项目')
        related = {
            '环境': instance.environments.exists(),
            '接口': instance.endpoints.exists(),
            '用例': instance.cases.exists(),
            '套件': instance.suites.exists(),
            '执行记录': instance.runs.exists(),
        }
        names = [name for name, exists in related.items() if exists]
        if names:
            raise ValidationError(
                f'项目存在关联数据（{"、".join(names)}），不能删除')
        was_current = self.request.user.current_project_id == instance.pk
        super().perform_destroy(instance)
        if was_current:
            self.request.user.current_project = None
            self.request.user.current_environment = None
            self.request.user.save(update_fields=[
                'current_project', 'current_environment'])

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        project = self.get_object()
        request.user.current_project = project
        if (request.user.current_environment_id and
                request.user.current_environment.project_id != project.pk):
            request.user.current_environment = None
        request.user.save(update_fields=[
            'current_project', 'current_environment'])
        audit(request.user, 'activate', 'project', project.pk)
        return Response({'current_project': project.pk, 'name': project.name})


class EnvironmentViewSet(AuditedViewSet):
    queryset = Environment.objects.all()
    serializer_class = EnvironmentSerializer
    search_fields = ['name', 'code']
    permission_classes = [EnvironmentPermission]
    filterset_fields = ['scope']

    def get_queryset(self):
        queryset = Environment.objects.filter(scope='public')
        if self.request.user.current_project_id:
            queryset = queryset.filter(
                project_id=self.request.user.current_project_id)
        else:
            queryset = queryset.none()
        return queryset.select_related(
            'owner', 'public_environment').prefetch_related(
            'systems', 'public_environment__systems')

    def perform_create(self, serializer):
        obj = serializer.save(owner=self.request.user)
        audit(self.request.user, 'create', 'environment', obj.pk)

    @action(detail=False, methods=['get'])
    def current(self, request):
        env = self.get_queryset().filter(pk=request.user.current_environment_id).first()
        if not env and request.user.current_environment_id:
            previous = Environment.objects.filter(
                pk=request.user.current_environment_id,
                scope='private').select_related('public_environment').first()
            env = previous.public_environment if previous else None
            if env:
                request.user.current_environment = env
                request.user.save(update_fields=['current_environment'])
        return Response({'environment': self.get_serializer(env).data if env else None})

    @action(detail=True, methods=['put', 'patch'], url_path='personal-variables')
    def personal_variables(self, request, pk=None):
        env = self.get_object()
        variables = request.data.get('variables')
        if not isinstance(variables, dict):
            raise ValidationError({'variables': '环境变量必须是 JSON 对象'})
        private = Environment.objects.filter(
            scope='private', owner=request.user,
            public_environment=env).first()
        if not private:
            private = Environment.objects.filter(
                scope='private', owner=request.user, code=env.code,
                public_environment__isnull=True).first()
        if private:
            private.public_environment = env
            private.name = env.name
            private.code = env.code
            private.variables = variables
            private.save(update_fields=[
                'public_environment', 'name', 'code', 'variables', 'updated_at'])
        else:
            private = Environment.objects.create(
                scope='private', owner=request.user, public_environment=env,
                project=env.project, name=env.name, code=env.code,
                variables=variables)
        audit(request.user, 'update_personal_variables', 'environment', env.pk)
        return Response({'environment': env.pk, 'variables': private.variables})

    @action(detail=True, methods=['post'], url_path='login-system')
    def login_system(self, request, pk=None):
        env = self.get_object()
        system_key = request.data.get('system_key')
        system = system_for(env, system_key)
        if not system:
            raise ValidationError('请选择当前环境中的系统')
        credentials = request.data.get('credentials', {})
        if not isinstance(credentials, dict):
            raise ValidationError('登录参数必须是 JSON 对象')
        stored_credentials = decrypt(system.credentials)
        if env.scope == 'private' and env.public_environment_id:
            private_system = env.systems.filter(system_key=system_key).first()
            if private_system:
                stored_credentials.update(decrypt(private_system.credentials))
        if system.login_endpoint_id:
            execute_linked_login(request.user, env, system, credentials)
            audit(request.user, 'execute_linked_login', 'environment', env.pk)
            return Response(auth_status(request.user, env))
        config = system.auth_config
        if not config:
            raise ValidationError('请先在环境系统中关联登录接口')
        data = {'systems_by_key': {system.system_key: {
            'base_url': system.base_url, 'verify_ssl': system.verify_ssl,
            'auth_config': system.auth_config}}, 'headers': {
                **(env.public_environment.headers if env.public_environment_id else {}),
                **env.headers}}
        data['headers'] = config.get('headers', {})
        case = {'endpoint_data': {'method': 'POST', 'path': config['path'],
            'system_key': system.system_key, 'body_type': config.get('body_type', 'json'),
            'body': config.get('body', {}), 'use_auth': False},
            'assertions': [], 'extract': {}}
        EnvironmentSession.objects.filter(
            owner=request.user, environment=env, system_key=system.system_key).delete()
        try:
            with requests.Session() as session:
                session.trust_env = False
                result = run_http(
                    case, data, {
                        **account_variables(request.user, env),
                        **stored_credentials, **credentials}, session)
            if not 200 <= result['response']['status'] < 300:
                raise ValidationError('被测系统拒绝登录，请检查登录参数')
            token = json_path(result['response']['body'], config['token_path'])
            store_token(request.user, env, system, token)
        except ValidationError:
            raise
        except Exception:
            raise ValidationError('登录或 Token 提取失败，请检查地址、参数及提取规则')
        audit(request.user, 'login_system', 'environment', env.pk)
        return Response(auth_status(request.user, env))

    @action(detail=True, methods=['post'], url_path='logout-system')
    def logout_system(self, request, pk=None):
        env = self.get_object()
        system_key = request.data.get('system_key')
        EnvironmentSession.objects.filter(
            owner=request.user, environment=env, system_key=system_key).delete()
        return Response(auth_status(request.user, env))

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        obj = self.get_object()
        request.user.current_environment = obj
        request.user.save(update_fields=['current_environment'])
        audit(request.user, 'activate', 'environment', obj.pk)
        return Response({'current_environment': obj.pk, 'name': obj.name})


class ProjectScopedViewSet(AuditedViewSet):
    filterset_fields = ['project']

    def get_queryset(self):
        queryset = self.queryset.filter(
            project__in=project_scope(self.request.user))
        if self.request.user.current_project_id:
            return queryset.filter(
                project_id=self.request.user.current_project_id)
        return queryset.none()


class EndpointViewSet(ProjectScopedViewSet):
    queryset = Endpoint.objects.select_related('project')
    serializer_class = EndpointSerializer
    search_fields = ['name', 'path', 'system_key']
    filterset_fields = ['project', 'method', 'system_key']
    permission_map = {'debug': 'automation.execute_testrun'}

    @action(detail=True, methods=['post'])
    def debug(self, request, pk=None):
        endpoint = self.get_object()
        env = environment_scope(request.user).filter(
            pk=request.data.get('environment') or request.user.current_environment_id).first()
        if not env:
            raise ValidationError('请先选择可访问的环境')
        overrides = request.data.get('overrides', {})
        if not isinstance(overrides, dict) or set(overrides) - {'headers', 'query', 'body', 'use_auth'}:
            raise ValidationError('调试参数只支持 headers、query、body、use_auth')
        for field in ['headers', 'query']:
            if field in overrides and not isinstance(overrides[field], dict):
                raise ValidationError(field + ' 必须是 JSON 对象')
        if 'use_auth' in overrides and type(overrides['use_auth']) is not bool:
            raise ValidationError('use_auth 必须是布尔值')
        use_auth = overrides.get('use_auth', endpoint.use_auth)
        if use_auth:
            ensure_linked_login(request.user, env, endpoint.system_key)
        data = dict(EnvironmentSerializer(env).data)
        data['secrets'] = decrypt(env.credentials)
        attach_sessions(data, request.user, env)
        variables = {
            **account_variables(request.user, env),
            **data.get('login_variables', {}),
            **data['secrets'].get('variables', {})}
        case = {'endpoint_data': dict(EndpointSerializer(endpoint).data), 'request_overrides': overrides}
        try:
            with requests.Session() as session:
                session.trust_env = False
                result = run_http(case, data, variables, session)
        except Exception as exc:
            raise ValidationError(redact(str(exc), leaf_values(data['secrets'])))
        if result['response']['status'] == 401:
            EnvironmentSession.objects.filter(
                owner=request.user, environment=env,
                system_key=endpoint.system_key).delete()
            system = system_for(env, endpoint.system_key)
            if use_auth and system and system.login_endpoint_id:
                ensure_linked_login(
                    request.user, env, endpoint.system_key, force=True)
                data = dict(EnvironmentSerializer(env).data)
                data['secrets'] = decrypt(env.credentials)
                attach_sessions(data, request.user, env)
                variables = {
                    **account_variables(request.user, env),
                    **data.get('login_variables', {}),
                    **data['secrets'].get('variables', {})}
                try:
                    with requests.Session() as session:
                        session.trust_env = False
                        result = run_http(case, data, variables, session)
                except Exception as exc:
                    raise ValidationError(redact(
                        str(exc), leaf_values(data['secrets'])))
                result['authentication_refreshed'] = True
            else:
                result['authentication_expired'] = True
        audit(request.user, 'debug', 'endpoint', endpoint.pk)
        return Response(redact(result, leaf_values(data['secrets'])))


class CaseViewSet(ProjectScopedViewSet):
    queryset = TestCase.objects.select_related('project', 'endpoint')
    serializer_class = CaseSerializer
    search_fields = ['name', 'author', 'node_id']
    filterset_fields = ['project', 'priority', 'kind', 'enabled']
    permission_map = {'scan': 'automation.scan_testcase'}

    @action(detail=False, methods=['post'])
    def scan(self, request):
        project = project_scope(request.user).filter(
            pk=request.user.current_project_id).first()
        if not project:
            raise ValidationError('项目不存在或无权访问')
        try:
            result = scan_cases(project)
        except ValueError as exc:
            raise ValidationError(str(exc))
        audit(request.user, 'scan', 'testcase', project.pk)
        return Response(result)


class SuiteViewSet(ProjectScopedViewSet):
    queryset = Suite.objects.all()
    serializer_class = SuiteSerializer
    search_fields = ['name', 'description']


class AuditViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.all()
    serializer_class = AuditSerializer
    permission_classes = [RBACPermission]
    permission_map = {'list': 'accounts.view_audit', 'retrieve': 'accounts.view_audit'}
    search_fields = ['actor_name', 'action', 'resource']


class RunViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TestRun.objects.select_related('project', 'environment', 'created_by')
    serializer_class = RunSerializer
    permission_classes = [RBACPermission]
    permission_map = {'create': 'automation.execute_testrun', 'cancel': 'automation.cancel_testrun',
                      'report': 'automation.view_testrun', 'destroy': 'automation.delete_testrun'}
    filterset_fields = ['project', 'status']
    search_fields = ['name']

    def get_queryset(self):
        return self.queryset.filter(project__in=project_scope(self.request.user))

    def get_serializer_class(self):
        return RunDetailSerializer if self.action == 'retrieve' else RunSerializer

    def create(self, request):
        data = request.data
        requested_project = data.get('project') or request.user.current_project_id
        if requested_project != request.user.current_project_id:
            raise ValidationError('只能操作当前项目，请先切换项目')
        project = project_scope(request.user).filter(
            pk=request.user.current_project_id).first()
        if not project:
            raise ValidationError('项目不存在或无权访问')
        envs = environment_scope(request.user)
        env = envs.filter(pk=data.get('environment') or request.user.current_environment_id).first()
        if not env:
            raise ValidationError('请选择有权使用的测试环境')
        stop, ids = False, data.get('case_ids', [])
        if data.get('suite'):
            suite = Suite.objects.filter(pk=data['suite'], project=project).first()
            if not suite:
                raise ValidationError('测试套件不存在')
            ids, stop = suite.case_ids, suite.stop_on_failure
        if not isinstance(ids, list) or not ids or len(ids) > 200 or any(type(i) is not int for i in ids):
            raise ValidationError('请选择 1 至 200 条用例')
        cases = {c.id: c for c in TestCase.objects.filter(id__in=ids, project=project, enabled=True).select_related('endpoint')}
        if len(cases) != len(ids):
            raise ValidationError('包含停用、重复或无权访问的用例')
        for case in cases.values():
            if case.endpoint and case.endpoint.use_auth:
                ensure_linked_login(
                    request.user, env, case.endpoint.system_key)
        if TestRun.objects.filter(created_by=request.user, status__in=['queued', 'running']).count() >= 5:
            raise ValidationError('最多同时排队 5 个任务')
        serialized = []
        for cid in ids:
            case = cases[cid]
            item = dict(CaseSerializer(case).data)
            item['endpoint_data'] = dict(EndpointSerializer(case.endpoint).data) if case.endpoint else None
            serialized.append(item)
        env_data = dict(EnvironmentSerializer(env).data)
        env_data['variables'] = account_variables(request.user, env)
        env_data['secrets'] = decrypt(env.credentials)
        source = env.public_environment if env.public_environment_id else env
        private_systems = {item.system_key: item for item in env.systems.all()}
        env_data['system_credentials'] = {}
        for system in source.systems.all():
            credentials = decrypt(system.credentials)
            if env.public_environment_id and system.system_key in private_systems:
                credentials.update(decrypt(private_systems[system.system_key].credentials))
            env_data['system_credentials'][system.system_key] = credentials
        attach_sessions(env_data, request.user, env)
        env_data['variables'].update(env_data.get('login_variables', {}))
        legacy_endpoints = {}
        for item in Endpoint.objects.filter(project=project):
            legacy_endpoints.setdefault(item.system_key, []).append({
                'name': item.name, 'key': item.function_name or item.name, 'url': item.path,
                'method': item.method, 'req_format': item.body_type.upper(), 'note': item.description})
        env_data['legacy_endpoints'] = legacy_endpoints
        env_data['api_catalog'] = list(EndpointSerializer(
            Endpoint.objects.filter(project=project).order_by('id'), many=True).data)
        snapshot = {'environment': env_data, 'cases': serialized, 'stop_on_failure': stop,
                    'username': request.user.username}
        run = TestRun.objects.create(project=project, environment=env, created_by=request.user,
            name=str(data.get('name') or f'{project.name} · 测试执行')[:200], total=len(ids), snapshot=encrypt(snapshot))
        audit(request.user, 'execute', 'testrun', run.pk)
        return Response(RunSerializer(run).data, status=201)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        run = self.get_object()
        if run.status not in ['queued', 'running']:
            raise ValidationError('任务已经结束')
        TestRun.objects.filter(pk=run.pk).update(cancel_requested=True)
        TestRun.objects.filter(pk=run.pk, status='queued').update(status='cancelled', finished_at=timezone.now())
        audit(request.user, 'cancel', 'testrun', run.pk)
        return Response({'detail': '已请求取消'})

    def destroy(self, request, *args, **kwargs):
        run = self.get_object()
        if run.status in ['queued', 'running']:
            raise ValidationError('请先取消任务')
        audit(request.user, 'delete', 'testrun', run.pk)
        run.delete()
        return Response(status=204)

    @action(detail=True, methods=['get'])
    def report(self, request, pk=None):
        run = self.get_object()
        rows = ''.join('<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in [
            r.name, r.status, round(r.duration_ms, 2), r.error,
            json.dumps(r.assertions, ensure_ascii=False)]) + '</tr>' for r in run.results.all())
        content = f"""<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">
        <title>Axiom 测试报告</title><style>body{{font:15px system-ui;padding:40px;color:#243047}}
        table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:12px;text-align:left}}
        pre{{white-space:pre-wrap}}</style><h1>{html.escape(run.name)}</h1>
        <p>状态：{run.status} · 总数：{run.total} · 通过：{run.passed} · 失败：{run.failed}</p>
        <table><tr><th>用例</th><th>状态</th><th>耗时 ms</th><th>错误</th><th>断言</th></tr>{rows}</table>
        <h2>日志</h2><pre>{html.escape(run.log)}</pre></html>"""
        response = HttpResponse(content, content_type='text/html; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="axiom-report-{run.pk}.html"'
        response['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'"
        return response


@api_view(['GET'])
def dashboard(request):
    projects = project_scope(request.user)
    if request.user.current_project_id:
        projects = projects.filter(pk=request.user.current_project_id)
    else:
        projects = projects.none()
    runs = TestRun.objects.filter(project__in=projects)
    if not request.user.has_perm('automation.view_testrun'):
        raise PermissionDenied()
    totals = runs.aggregate(passed=Sum('passed'), failed=Sum('failed'))
    trend = list(runs.filter(created_at__gte=timezone.now() - timedelta(days=7))
        .annotate(day=TruncDate('created_at')).values('day')
        .annotate(total=Count('id'), passed=Sum('passed'), failed=Sum('failed')).order_by('day'))
    return Response({'projects': projects.count(),
        'cases': TestCase.objects.filter(project__in=projects).count(),
        'runs': runs.count(), 'active_runs': runs.filter(status__in=['queued', 'running']).count(),
        'passed': totals['passed'] or 0, 'failed': totals['failed'] or 0,
        'trend': trend, 'recent_runs': RunSerializer(runs[:6], many=True).data})


@api_view(['GET'])
@permission_classes([AllowAny])
def health(request):
    return Response({'status': 'ok', 'service': 'axiom-api'})


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def demo_echo(request):
    return Response({'code': 0, 'message': 'success', 'data': {
        'method': request.method, 'query': request.query_params.dict(),
        'body': request.data, 'device': {'id': 'light-001', 'online': True}}})
