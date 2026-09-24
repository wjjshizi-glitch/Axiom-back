from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    display_name = models.CharField(max_length=80, blank=True)
    current_environment = models.ForeignKey(
        'automation.Environment', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='active_users',
    )
    current_project = models.ForeignKey(
        'automation.Project', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='active_users',
    )

    class Meta:
        permissions = [('manage_users', '管理用户'), ('manage_roles', '管理角色与权限'),
                       ('view_audit', '查看操作审计')]
