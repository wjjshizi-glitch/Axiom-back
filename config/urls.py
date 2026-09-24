from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from accounts.views import LoginView, UserViewSet, RoleViewSet, me, logout, change_password
from automation.views import (ProjectViewSet, EnvironmentViewSet, EndpointViewSet, CaseViewSet,
    SuiteViewSet, RunViewSet, AuditViewSet, dashboard, health, demo_echo)

router = DefaultRouter()
for prefix, view in [('users', UserViewSet), ('roles', RoleViewSet), ('projects', ProjectViewSet),
                     ('environments', EnvironmentViewSet), ('endpoints', EndpointViewSet),
                     ('cases', CaseViewSet), ('suites', SuiteViewSet), ('runs', RunViewSet),
                     ('audit-logs', AuditViewSet)]:
    router.register(prefix, view, basename=prefix)
urlpatterns = [
    path('api/v1/auth/login/', LoginView.as_view()),
    path('api/v1/auth/refresh/', TokenRefreshView.as_view()),
    path('api/v1/auth/me/', me),
    path('api/v1/auth/logout/', logout),
    path('api/v1/auth/password/', change_password),
    path('api/v1/dashboard/', dashboard),
    path('api/v1/health/', health),
    path('api/v1/demo/echo/', demo_echo),
    path('api/v1/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/v1/docs/', SpectacularSwaggerView.as_view(url_name='schema')),
    path('api/v1/', include(router.urls)),
]
