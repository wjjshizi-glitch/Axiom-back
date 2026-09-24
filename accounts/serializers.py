from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)
    role_names = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()
    current_project_name = serializers.CharField(
        source='current_project.name', read_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'display_name', 'email', 'password', 'is_active',
                  'is_superuser', 'groups', 'role_names', 'permissions',
                  'current_project', 'current_project_name',
                  'current_environment', 'date_joined']
        read_only_fields = ['is_superuser', 'permissions', 'current_project',
                            'current_environment', 'date_joined']

    def get_role_names(self, obj):
        return list(obj.groups.values_list('name', flat=True))

    def get_permissions(self, obj):
        return sorted(obj.get_all_permissions())

    def validate(self, attrs):
        actor = self.context['request'].user
        if self.instance and self.instance.is_superuser and not actor.is_superuser:
            raise PermissionDenied('只有超级管理员可以修改超级管理员')
        if 'groups' in attrs and not actor.has_perm('accounts.manage_roles'):
            raise PermissionDenied('分配角色需要角色管理权限')
        if not actor.is_superuser:
            own = actor.get_all_permissions()
            for group in attrs.get('groups', []):
                perms = {f'{p.content_type.app_label}.{p.codename}' for p in group.permissions.select_related('content_type')}
                if not perms <= own:
                    raise PermissionDenied('不能分配超出自身权限的角色')
        if self.instance == actor and attrs.get('is_active') is False:
            raise serializers.ValidationError('不能停用当前登录账号')
        password = attrs.get('password')
        if not self.instance and not password:
            raise serializers.ValidationError({'password': '创建用户必须设置密码'})
        if password:
            try:
                validate_password(password, self.instance or User(username=attrs.get('username', '')))
            except DjangoValidationError as exc:
                raise serializers.ValidationError({'password': exc.messages})
        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password')
        groups = validated_data.pop('groups', [])
        user = User.objects.create_user(password=password, **validated_data)
        user.groups.set(groups)
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        instance = super().update(instance, validated_data)
        if password:
            instance.set_password(password)
            instance.save(update_fields=['password'])
        return instance


class RoleSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(source='user_set.count', read_only=True)

    class Meta:
        model = Group
        fields = ['id', 'name', 'permissions', 'member_count']

    def validate_permissions(self, permissions):
        actor = self.context['request'].user
        if any(permission.codename == 'manage_public_environment' for permission in permissions):
            raise serializers.ValidationError('公共基础环境固定由超级管理员维护，不能授予角色')
        if not actor.is_superuser:
            granted = {f'{p.content_type.app_label}.{p.codename}' for p in permissions}
            if not granted <= actor.get_all_permissions():
                raise PermissionDenied('不能授予超出自身范围的权限')
        return permissions


class PermissionSerializer(serializers.ModelSerializer):
    code = serializers.SerializerMethodField()
    page_path = serializers.SerializerMethodField()
    page_name = serializers.SerializerMethodField()
    action_name = serializers.SerializerMethodField()
    superuser_only = serializers.SerializerMethodField()

    PAGE_MAP = {
        'project': ('/projects', '项目管理'), 'endpoint': ('/endpoints', '接口管理'),
        'testcase': ('/cases', '测试用例'), 'suite': ('/suites', '测试套件'),
        'testrun': ('/runs', '执行记录'), 'environment': ('/environments', '环境配置'),
        'user': ('/users', '用户管理'), 'group': ('/roles', '角色权限'),
        'auditlog': ('/audit-logs', '操作审计'), 'caseresult': ('/runs/:id', '执行详情'),
    }
    ACTION_MAP = {
        'view': '查看', 'add': '新建', 'change': '编辑', 'delete': '删除',
        'execute_testrun': '执行测试', 'cancel_testrun': '取消执行',
        'scan_testcase': '同步 Python 用例', 'manage_users': '管理用户',
        'manage_roles': '管理角色权限', 'view_audit': '查看审计日志',
        'manage_public_environment': '维护公共基础环境',
    }

    class Meta:
        model = Permission
        fields = ['id', 'name', 'codename', 'code', 'page_path', 'page_name',
                  'action_name', 'superuser_only']

    def get_code(self, obj):
        return f'{obj.content_type.app_label}.{obj.codename}'

    def get_page_path(self, obj):
        if obj.codename == 'manage_roles':
            return '/roles'
        if obj.codename == 'manage_users':
            return '/users'
        if obj.codename == 'view_audit':
            return '/audit-logs'
        return self.PAGE_MAP.get(obj.content_type.model, ('/system', '系统管理'))[0]

    def get_page_name(self, obj):
        if obj.codename == 'manage_roles':
            return '角色权限'
        if obj.codename == 'manage_users':
            return '用户管理'
        if obj.codename == 'view_audit':
            return '操作审计'
        return self.PAGE_MAP.get(obj.content_type.model, ('/system', '系统管理'))[1]

    def get_action_name(self, obj):
        if obj.codename in self.ACTION_MAP:
            return self.ACTION_MAP[obj.codename]
        prefix = obj.codename.split('_', 1)[0]
        return self.ACTION_MAP.get(prefix, obj.name)

    def get_superuser_only(self, obj):
        return obj.codename == 'manage_public_environment'
