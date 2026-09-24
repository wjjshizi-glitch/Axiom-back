import re
from urllib.parse import urlparse
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from .models import Project, Environment, EnvironmentSystem, Endpoint, TestCase, Suite, TestRun, CaseResult, AuditLog
from .security import encrypt, decrypt, project_scope, can_manage_environment
from .environment_auth import auth_status
from .infrastructure import validate_systems, validate_connections


class ProjectSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source='owner.username', read_only=True)
    case_count = serializers.IntegerField(source='cases.count', read_only=True)
    environment_count = serializers.SerializerMethodField()
    endpoint_count = serializers.IntegerField(source='endpoints.count', read_only=True)
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = ['id', 'name', 'description', 'owner', 'owner_name', 'members',
                  'case_count', 'environment_count', 'endpoint_count',
                  'is_current', 'created_at']
        read_only_fields = ['owner']

    def get_is_current(self, obj):
        request = self.context.get('request')
        return bool(request and request.user.current_project_id == obj.pk)

    def get_environment_count(self, obj):
        return obj.environments.filter(scope='public').count()

    def validate_members(self, members):
        user = self.context['request'].user
        if self.instance and self.instance.owner_id != user.id and not user.is_superuser:
            raise PermissionDenied('只有项目所有者可以管理成员')
        return members


class EnvironmentSystemSerializer(serializers.ModelSerializer):
    credentials = serializers.JSONField(write_only=True, required=False)
    has_credentials = serializers.SerializerMethodField()
    login_endpoint_name = serializers.CharField(
        source='login_endpoint.name', read_only=True)
    login_parameters = serializers.SerializerMethodField()

    class Meta:
        model = EnvironmentSystem
        fields = ['id', 'system_key', 'name', 'base_url', 'verify_ssl',
                  'auth_config', 'login_endpoint', 'login_endpoint_name',
                  'login_extract', 'login_expires_in', 'login_parameters',
                  'headers',
                  'credentials', 'has_credentials']
        read_only_fields = ['id']

    def get_has_credentials(self, obj):
        return bool(decrypt(obj.credentials))

    def get_login_parameters(self, obj):
        if not obj.login_endpoint_id:
            return []
        text = str({
            'headers': obj.login_endpoint.headers,
            'query': obj.login_endpoint.query,
            'body': obj.login_endpoint.body,
        })
        return sorted(set(re.findall(r'\{\{\s*([\w.-]+)\s*\}\}', text)))


class EnvironmentSerializer(serializers.ModelSerializer):
    connections = serializers.JSONField(write_only=True, required=False)
    systems = EnvironmentSystemSerializer(many=True, required=False)
    has_connections = serializers.SerializerMethodField()
    connection_summary = serializers.SerializerMethodField()
    owner_name = serializers.CharField(source='owner.username', read_only=True)
    can_edit = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    authentication = serializers.SerializerMethodField()
    public_environment_name = serializers.CharField(
        source='public_environment.name', read_only=True)
    personal_variables = serializers.SerializerMethodField()

    class Meta:
        model = Environment
        exclude = ['credentials']
        read_only_fields = ['owner']
        validators = []
        extra_kwargs = {
            'name': {'required': False},
            'code': {'required': False},
            'project': {'required': False},
        }

    def get_can_edit(self, obj):
        request = self.context.get('request')
        return bool(request and can_manage_environment(request.user, obj))

    def get_authentication(self, obj):
        request = self.context.get('request')
        return auth_status(request.user, obj) if request else None

    def get_can_delete(self, obj):
        request = self.context.get('request')
        return bool(request and can_manage_environment(request.user, obj, 'delete'))

    def get_has_connections(self, obj):
        return bool(decrypt(obj.credentials).get('connections'))

    def get_personal_variables(self, obj):
        request = self.context.get('request')
        if not request or obj.scope != 'public':
            return obj.variables if obj.scope == 'private' else {}
        private = Environment.objects.filter(
            scope='private', owner=request.user,
            public_environment=obj).only('variables').first()
        return private.variables if private else {}

    def get_connection_summary(self, obj):
        private = decrypt(obj.credentials)
        sensitive = {'password', 'pwd', 'token', 'secret', 'api_key'}

        def sanitize(value):
            if isinstance(value, dict):
                return {
                    key: sanitize(item)
                    for key, item in value.items()
                    if key.lower() not in sensitive}
            if isinstance(value, list):
                return [sanitize(item) for item in value]
            return value

        result = {}
        for kind, rows in private.get('connections', {}).items():
            result[kind] = []
            for row in rows:
                public = sanitize(row)
                public['has_credentials'] = any(row.get(key) for key in sensitive)
                result[kind].append(public)
        return result

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.scope == 'public':
            data.pop('variables', None)
        if instance.scope == 'private' and instance.public_environment_id:
            public = instance.public_environment
            data['name'] = public.name
            data['code'] = public.code
            data['variables'] = {**public.variables, **instance.variables}
            data['headers'] = {**public.headers, **instance.headers}
            private_systems = {item.system_key: item for item in instance.systems.all()}
            data['systems'] = []
            for system in public.systems.all():
                item = dict(EnvironmentSystemSerializer(system).data)
                private = private_systems.get(system.system_key)
                item['has_credentials'] = bool(private and decrypt(private.credentials))
                data['systems'].append(item)
        data['systems_by_key'] = {
            item['system_key']: item for item in data.get('systems', [])}
        return data

    def validate(self, attrs):
        actor = self.context['request'].user
        scope = attrs.get(
            'scope', self.instance.scope if self.instance else (
                'public' if actor.is_superuser else 'private'))
        if not self.instance:
            attrs['scope'] = scope
        if self.instance and scope != self.instance.scope:
            raise serializers.ValidationError({'scope': '配置类型创建后不能修改，请新建配置'})
        if scope == 'public' and not actor.is_superuser:
            raise PermissionDenied('只有超级管理员可以维护公共基础环境')
        if scope == 'public' and 'variables' in attrs:
            raise serializers.ValidationError({
                'variables': '环境变量属于个人配置，请使用个人环境变量接口'})
        public = attrs.get(
            'public_environment',
            self.instance.public_environment if self.instance else None)
        if scope == 'private':
            if not public:
                raise serializers.ValidationError({
                    'public_environment': '请先选择已存在的公共环境；如无可选项，请先新增公共环境信息'})
            if public.scope != 'public':
                raise serializers.ValidationError({
                    'public_environment': '私有配置只能关联公共环境'})
            owner = self.instance.owner if self.instance else actor
            duplicate = Environment.objects.filter(
                scope='private', owner=owner, public_environment=public)
            if self.instance:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise serializers.ValidationError({
                    'public_environment': '当前账号已存在该公共环境的私有配置'})
            attrs['name'] = public.name
            attrs['code'] = public.code
            attrs['project'] = public.project
        else:
            attrs['public_environment'] = None
            project = attrs.get(
                'project', self.instance.project if self.instance else
                actor.current_project)
            if not project or not project_scope(actor).filter(pk=project.pk).exists():
                raise serializers.ValidationError({
                    'project': '请先在项目管理中切换当前项目'})
            attrs['project'] = project
            if not attrs.get('name', self.instance.name if self.instance else ''):
                raise serializers.ValidationError({'name': '请填写环境名称'})
            if not attrs.get('code', self.instance.code if self.instance else ''):
                raise serializers.ValidationError({'code': '请填写环境标识'})
        for key in ['variables', 'headers']:
            if key in attrs and not isinstance(attrs[key], dict):
                raise serializers.ValidationError({key: '必须是 JSON 对象'})
        if 'systems' in attrs:
            attrs['systems'] = validate_systems(attrs['systems'])
            for system in attrs['systems']:
                endpoint = system.get('login_endpoint')
                if endpoint and endpoint.system_key != system['system_key']:
                    raise serializers.ValidationError({
                        'systems': '关联登录接口必须与系统配置使用相同的 system_key'})
                if endpoint and endpoint.project_id != attrs['project'].pk:
                    raise serializers.ValidationError({
                        'systems': '关联登录接口必须属于当前项目'})
        if 'connections' in attrs:
            attrs['connections'] = validate_connections(attrs['connections'])
        owner = self.instance.owner if self.instance else self.context['request'].user
        code = attrs.get('code', self.instance.code if self.instance else '')
        project = attrs.get(
            'project', self.instance.project if self.instance else None)
        qs = Environment.objects.filter(
            scope=scope, project=project, code=code)
        if scope == 'private':
            qs = qs.filter(owner=owner)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError({'code': '公共配置或本账号私有配置的环境标识和位置不能重复'})
        return attrs

    def _save_nested(self, environment, systems, connections):
        if connections is not None:
            private = decrypt(environment.credentials)
            existing = {
                (kind, row.get('name')): row
                for kind, rows in private.get('connections', {}).items()
                for row in rows}
            merged = {}
            for kind, rows in connections.items():
                merged[kind] = []
                for row in rows:
                    current = existing.get((kind, row.get('name')), {})
                    item = dict(row)
                    for key in ['password', 'pwd', 'token', 'secret', 'api_key']:
                        if not item.get(key) and current.get(key):
                            item[key] = current[key]
                        elif not item.get(key):
                            item.pop(key, None)
                    merged[kind].append(item)
            private['connections'] = merged
            environment.credentials = encrypt(private)
            environment.save(update_fields=['credentials'])
        if systems is None:
            return
        if environment.scope == 'private' and environment.public_environment_id:
            public_systems = {
                item.system_key: item
                for item in environment.public_environment.systems.all()}
            normalized = []
            for item in systems:
                public = public_systems.get(item['system_key'])
                if not public:
                    raise serializers.ValidationError({
                        'systems': '私有配置只能填写关联公共环境中已有系统的凭据'})
                normalized.append({
                    **item,
                    'name': public.name,
                    'base_url': public.base_url,
                    'verify_ssl': public.verify_ssl,
                    'auth_config': public.auth_config,
                })
            systems = normalized
        existing = {system.system_key: system for system in environment.systems.all()}
        keep = set()
        for item in systems:
            key = item['system_key']
            keep.add(key)
            credentials = item.pop('credentials', None)
            system = existing.get(key)
            if system:
                for field, value in item.items():
                    setattr(system, field, value)
                if credentials is not None:
                    system.credentials = encrypt(credentials)
                system.save()
            else:
                EnvironmentSystem.objects.create(
                    environment=environment,
                    credentials=encrypt(credentials or {}), **item)
        removed = environment.systems.exclude(system_key__in=keep)
        from .models import EnvironmentSession
        EnvironmentSession.objects.filter(
            environment=environment,
            system_key__in=removed.values_list('system_key', flat=True)).delete()
        removed.delete()

    @transaction.atomic
    def create(self, validated_data):
        systems = validated_data.pop('systems', [])
        connections = validated_data.pop('connections', {})
        environment = super().create(validated_data)
        self._save_nested(environment, systems, connections)
        return environment

    @transaction.atomic
    def update(self, instance, validated_data):
        systems = validated_data.pop('systems', None)
        connections = validated_data.pop('connections', None)
        environment = super().update(instance, validated_data)
        self._save_nested(environment, systems, connections)
        return environment


class ProjectResourceSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        project = (attrs.get('project') or getattr(self.instance, 'project', None)
                   or self.context['request'].user.current_project)
        if not project or not project_scope(self.context['request'].user).filter(pk=project.pk).exists():
            raise serializers.ValidationError({'project': '请先在项目管理中切换当前项目'})
        attrs['project'] = project
        if self.instance and project.pk != self.instance.project_id:
            raise serializers.ValidationError({'project': '创建后不能跨项目移动'})
        return attrs


class EndpointSerializer(ProjectResourceSerializer):
    class Meta:
        model = Endpoint
        fields = '__all__'
        validators = []
        extra_kwargs = {
            'project': {'required': False},
            'system_key': {'required': False},
        }

    def validate(self, attrs):
        attrs = super().validate(attrs)
        function_name = attrs.get('function_name', self.instance.function_name if self.instance else '')
        if not function_name or not re.fullmatch(r'[a-z][a-z0-9_]*', function_name):
            raise serializers.ValidationError({'function_name': '方法名必填，使用小写字母、数字和下划线，例如 get_device_detail'})
        project = attrs.get('project') or self.instance.project
        duplicate = Endpoint.objects.filter(project=project, function_name=function_name)
        if self.instance:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError({'function_name': '当前项目的方法名已存在'})
        system_key = attrs.get(
            'system_key', self.instance.system_key if self.instance else '')
        if not system_key:
            actor = self.context['request'].user
            environment = Environment.objects.filter(
                pk=actor.current_environment_id,
                project=project, scope='public').first()
            if not environment:
                environment = Environment.objects.filter(
                    project=project, scope='public').order_by('id').first()
            system = environment.systems.order_by('id').first() if environment else None
            if not system:
                raise serializers.ValidationError({
                    'system_key': '当前项目尚未配置环境系统，请先新增环境系统'})
            attrs['system_key'] = system.system_key
        for key in ['headers', 'query']:
            if key in attrs and not isinstance(attrs[key], dict):
                raise serializers.ValidationError({key: '必须是 JSON 对象'})
        path = attrs.get('path')
        if path is not None and (urlparse(path).scheme or path.startswith('//')):
            raise serializers.ValidationError({'path': '请填写相对路径，目标地址通过环境地址 Key 选择'})
        return attrs


class CaseSerializer(ProjectResourceSerializer):
    endpoint_name = serializers.CharField(source='endpoint.name', read_only=True)
    method = serializers.CharField(source='endpoint.method', read_only=True)

    class Meta:
        model = TestCase
        fields = '__all__'
        read_only_fields = ['node_id']
        extra_kwargs = {'project': {'required': False}}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        project = attrs.get('project') or self.instance.project
        endpoint = attrs.get('endpoint', self.instance.endpoint if self.instance else None)
        kind = attrs.get('kind', self.instance.kind if self.instance else 'http')
        if kind == 'http' and (not endpoint or endpoint.project_id != project.pk):
            raise serializers.ValidationError({'endpoint': '请选择当前项目的接口'})
        if kind == 'pytest' and (not self.instance or self.instance.kind != 'pytest'):
            raise serializers.ValidationError('Python 用例必须通过扫描导入')
        for key in ['request_overrides', 'extract']:
            if key in attrs and not isinstance(attrs[key], dict):
                raise serializers.ValidationError({key: '必须是 JSON 对象'})
        if 'tags' in attrs and not isinstance(attrs['tags'], list):
            raise serializers.ValidationError({'tags': '必须是数组'})
        if 'assertions' in attrs and not isinstance(attrs['assertions'], list):
            raise serializers.ValidationError({'assertions': '必须是数组'})
        for rule in attrs.get('assertions', []):
            if not isinstance(rule, dict) or rule.get('source') not in ['status', 'json', 'text', 'header', 'duration']:
                raise serializers.ValidationError({'assertions': '断言 source 无效'})
            if rule.get('operator', 'eq') not in ['eq', 'ne', 'contains', 'exists', 'lt', 'gt']:
                raise serializers.ValidationError({'assertions': '不支持的断言运算符'})
        return attrs


class SuiteSerializer(ProjectResourceSerializer):
    class Meta:
        model = Suite
        fields = '__all__'
        extra_kwargs = {'project': {'required': False}}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        project = attrs.get('project') or self.instance.project
        ids = attrs.get('case_ids', self.instance.case_ids if self.instance else [])
        if not isinstance(ids, list) or not ids or any(type(i) is not int for i in ids):
            raise serializers.ValidationError({'case_ids': '至少选择一条用例'})
        if len(ids) != len(set(ids)) or len(ids) > 200:
            raise serializers.ValidationError({'case_ids': '用例不能重复，最多 200 条'})
        if TestCase.objects.filter(project=project, id__in=ids).count() != len(ids):
            raise serializers.ValidationError({'case_ids': '用例不存在或不属于当前项目'})
        return attrs


class ResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = CaseResult
        fields = '__all__'


class RunSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    environment_name = serializers.CharField(source='environment.name', read_only=True)
    project_name = serializers.CharField(source='project.name', read_only=True)

    class Meta:
        model = TestRun
        exclude = ['snapshot']
        read_only_fields = [f.name for f in TestRun._meta.fields]


class RunDetailSerializer(RunSerializer):
    results = ResultSerializer(many=True, read_only=True)


class AuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = '__all__'
