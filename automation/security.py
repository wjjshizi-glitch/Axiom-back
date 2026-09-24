import base64
import hashlib
import json
from cryptography.fernet import Fernet
from django.conf import settings
from django.db.models import Q
from rest_framework.permissions import BasePermission


def encrypt(value):
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key).encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()


def decrypt(value):
    if not value:
        return {}
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return json.loads(Fernet(key).decrypt(value.encode()))


def project_scope(user):
    from .models import Project
    qs = Project.objects.all()
    return qs if user.is_superuser else qs.filter(Q(owner=user) | Q(members=user)).distinct()


def audit(user, action, resource, object_id=''):
    from .models import AuditLog
    AuditLog.objects.create(actor=user, actor_name=user.username, action=action,
                            resource=resource, object_id=str(object_id))


def environment_scope(user):
    from .models import Environment
    if not user.is_authenticated:
        return Environment.objects.none()
    queryset = Environment.objects.filter(
        Q(scope='public') | Q(scope='private', owner=user))
    if user.current_project_id:
        return queryset.filter(project_id=user.current_project_id)
    return queryset.none()


def can_manage_environment(user, environment, action='change'):
    if not user.is_authenticated or not user.has_perm(f'automation.{action}_environment'):
        return False
    if environment.scope == 'public':
        return user.is_superuser
    return environment.owner_id == user.pk


class EnvironmentPermission(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if view.action in ['list', 'retrieve', 'activate', 'current', 'logout_system',
                           'personal_variables']:
            return True
        if view.action == 'login_system':
            return request.user.has_perm('automation.execute_testrun')
        if view.action == 'create':
            return request.user.is_superuser
        verb = {'create': 'add', 'update': 'change', 'partial_update': 'change', 'destroy': 'delete'}.get(view.action)
        return bool(verb and request.user.has_perm(f'automation.{verb}_environment'))

    def has_object_permission(self, request, view, obj):
        if obj.scope == 'private' and obj.owner_id != request.user.pk:
            return False
        if view.action in ['list', 'retrieve', 'activate', 'current', 'login_system',
                           'logout_system', 'personal_variables']:
            return True
        return can_manage_environment(request.user, obj, 'delete' if view.action == 'destroy' else 'change')


class RBACPermission(BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        custom = getattr(view, 'permission_map', {}).get(view.action)
        if custom is not None:
            return request.user.has_perm(custom)
        verb = {'list': 'view', 'retrieve': 'view', 'create': 'add',
                'update': 'change', 'partial_update': 'change', 'destroy': 'delete'}.get(view.action)
        model = view.queryset.model
        return bool(verb and request.user.has_perm(
            f'{model._meta.app_label}.{verb}_{model._meta.model_name}'))
