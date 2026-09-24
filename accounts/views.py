from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets
from rest_framework.decorators import action, api_view
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from automation.security import RBACPermission, audit
from .serializers import UserSerializer, RoleSerializer, PermissionSerializer


class LoginView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'


@api_view(['GET', 'PATCH'])
def me(request):
    if request.method == 'PATCH':
        for key in ['display_name', 'email']:
            if key in request.data:
                setattr(request.user, key, str(request.data[key])[:80])
        request.user.save()
    return Response(UserSerializer(request.user, context={'request': request}).data)


@api_view(['POST'])
def logout(request):
    try:
        token = RefreshToken(request.data.get('refresh', ''))
        if str(token['user_id']) != str(request.user.pk):
            raise ValidationError('无效令牌')
        token.blacklist()
    except TokenError:
        raise ValidationError('无效令牌')
    return Response(status=204)


@api_view(['POST'])
def change_password(request):
    if not request.user.check_password(request.data.get('old_password', '')):
        raise ValidationError({'old_password': '原密码不正确'})
    password = request.data.get('new_password', '')
    try:
        validate_password(password, request.user)
    except DjangoValidationError as exc:
        raise ValidationError({'new_password': exc.messages})
    request.user.set_password(password)
    request.user.save(update_fields=['password'])
    audit(request.user, 'change_password', 'user', request.user.pk)
    return Response({'detail': '密码已更新，请重新登录'})


class UserViewSet(viewsets.ModelViewSet):
    queryset = get_user_model().objects.all().order_by('-id')
    serializer_class = UserSerializer
    permission_classes = [RBACPermission]
    permission_map = {a: 'accounts.manage_users' for a in
                      ['list', 'retrieve', 'create', 'update', 'partial_update', 'destroy']}
    search_fields = ['username', 'display_name', 'email']

    def perform_create(self, serializer):
        obj = serializer.save()
        audit(self.request.user, 'create', 'user', obj.pk)

    def perform_update(self, serializer):
        obj = serializer.save()
        audit(self.request.user, 'update', 'user', obj.pk)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj == request.user or obj.is_superuser:
            raise PermissionDenied('不能停用自己或超级管理员')
        obj.is_active = False
        obj.save(update_fields=['is_active'])
        audit(request.user, 'deactivate', 'user', obj.pk)
        return Response(status=204)


class RoleViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.all().order_by('id')
    serializer_class = RoleSerializer
    permission_classes = [RBACPermission]
    permission_map = {a: 'accounts.manage_roles' for a in
                      ['list', 'retrieve', 'create', 'update', 'partial_update', 'destroy', 'permissions']}
    search_fields = ['name']

    def check_delegation(self, obj):
        if self.request.user.is_superuser:
            return
        codes = {f'{p.content_type.app_label}.{p.codename}' for p in obj.permissions.select_related('content_type')}
        if not codes <= self.request.user.get_all_permissions():
            raise PermissionDenied('不能修改权限高于自己的角色')

    def perform_create(self, serializer):
        obj = serializer.save()
        audit(self.request.user, 'create', 'role', obj.pk)

    def perform_update(self, serializer):
        self.check_delegation(serializer.instance)
        obj = serializer.save()
        audit(self.request.user, 'update', 'role', obj.pk)

    def perform_destroy(self, instance):
        self.check_delegation(instance)
        audit(self.request.user, 'delete', 'role', instance.pk)
        instance.delete()

    @action(detail=False, methods=['get'])
    def permissions(self, request):
        qs = Permission.objects.filter(
            content_type__app_label__in=['accounts', 'automation']).exclude(
                codename='manage_public_environment').select_related('content_type').order_by(
                    'content_type__app_label', 'content_type__model', 'codename')
        return Response(PermissionSerializer(qs, many=True).data)
