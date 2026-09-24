from django.conf import settings
from django.db import models


class Timestamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ['-id']


class Project(Timestamped):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='projects', blank=True)

    def __str__(self):
        return self.name


class Environment(Timestamped):
    scope = models.CharField(max_length=10, choices=[('public', '公共配置'), ('private', '私有配置')], default='private')
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=40)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    project = models.ForeignKey(
        Project, on_delete=models.PROTECT, related_name='environments')
    public_environment = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE,
        related_name='private_configs')
    variables = models.JSONField(default=dict, blank=True)
    headers = models.JSONField(default=dict, blank=True)
    credentials = models.TextField(blank=True)

    class Meta(Timestamped.Meta):
        permissions = [('manage_public_environment', '维护公共环境配置')]
        constraints = [
            models.UniqueConstraint(fields=['project', 'code'], condition=models.Q(scope='public'), name='unique_public_env'),
            models.UniqueConstraint(fields=['owner', 'project', 'code'], condition=models.Q(scope='private'), name='unique_private_env'),
            models.UniqueConstraint(fields=['owner', 'public_environment'],
                condition=models.Q(scope='private', public_environment__isnull=False),
                name='unique_private_public_env'),
        ]


class EnvironmentSystem(Timestamped):
    environment = models.ForeignKey(Environment, on_delete=models.CASCADE, related_name='systems')
    system_key = models.SlugField(max_length=60)
    name = models.CharField(max_length=100)
    base_url = models.URLField()
    verify_ssl = models.BooleanField(default=True)
    auth_config = models.JSONField(default=dict, blank=True)
    login_endpoint = models.ForeignKey(
        'Endpoint', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+')
    login_extract = models.JSONField(default=dict, blank=True)
    login_expires_in = models.PositiveIntegerField(default=3600)
    headers = models.JSONField(default=dict, blank=True)
    credentials = models.TextField(blank=True)

    class Meta(Timestamped.Meta):
        default_permissions = ()
        constraints = [models.UniqueConstraint(
            fields=['environment', 'system_key'], name='unique_environment_system')]


class Endpoint(Timestamped):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='endpoints')
    name = models.CharField(max_length=100)
    function_name = models.SlugField(max_length=100)
    system_key = models.SlugField(max_length=60)
    method = models.CharField(max_length=10, choices=[(m, m) for m in ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']], default='GET')
    path = models.CharField(max_length=1000)
    body_type = models.CharField(max_length=10, choices=[('json', 'JSON'), ('form', 'Form'), ('text', 'Text')], default='json')
    headers = models.JSONField(default=dict, blank=True)
    query = models.JSONField(default=dict, blank=True)
    body = models.JSONField(default=dict, blank=True)
    description = models.TextField(blank=True)
    use_auth = models.BooleanField(default=True)

    class Meta(Timestamped.Meta):
        constraints = [models.UniqueConstraint(fields=['project', 'function_name'], name='unique_project_endpoint_method')]


class EnvironmentSession(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    environment = models.ForeignKey(Environment, on_delete=models.CASCADE)
    system_key = models.SlugField(max_length=60)
    token = models.TextField()
    expires_at = models.DateTimeField()
    config_hash = models.CharField(max_length=64)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints = [models.UniqueConstraint(
            fields=['owner', 'environment', 'system_key'], name='unique_environment_session')]


class TestCase(Timestamped):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='cases')
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=[('http', 'HTTP'), ('pytest', 'Pytest')], default='http')
    endpoint = models.ForeignKey(Endpoint, null=True, blank=True, on_delete=models.SET_NULL)
    priority = models.CharField(max_length=2, choices=[(p, p) for p in ['P0', 'P1', 'P2', 'P3']], default='P1')
    author = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=True)
    request_overrides = models.JSONField(default=dict, blank=True)
    assertions = models.JSONField(default=list, blank=True)
    extract = models.JSONField(default=dict, blank=True)
    node_id = models.CharField(max_length=1000, blank=True)
    tags = models.JSONField(default=list, blank=True)

    class Meta(Timestamped.Meta):
        permissions = [('scan_testcase', '扫描 Python 测试用例')]


class Suite(Timestamped):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='suites')
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    case_ids = models.JSONField(default=list)
    stop_on_failure = models.BooleanField(default=False)


class TestRun(Timestamped):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name='runs')
    environment = models.ForeignKey(Environment, null=True, on_delete=models.SET_NULL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, default='queued', choices=[
        (s, s) for s in ['queued', 'running', 'passed', 'failed', 'error', 'cancelled']])
    snapshot = models.TextField()
    total = models.PositiveIntegerField(default=0)
    passed = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    heartbeat_at = models.DateTimeField(null=True)
    log = models.TextField(blank=True)
    cancel_requested = models.BooleanField(default=False)

    class Meta(Timestamped.Meta):
        permissions = [('execute_testrun', '执行测试'), ('cancel_testrun', '取消测试')]


class CaseResult(models.Model):
    run = models.ForeignKey(TestRun, on_delete=models.CASCADE, related_name='results')
    name = models.CharField(max_length=200)
    case_id_snapshot = models.PositiveIntegerField()
    status = models.CharField(max_length=20)
    duration_ms = models.FloatField(default=0)
    request = models.JSONField(default=dict)
    response = models.JSONField(default=dict)
    assertions = models.JSONField(default=list)
    error = models.TextField(blank=True)
    log = models.TextField(blank=True)

    class Meta:
        ordering = ['id']


class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    actor_name = models.CharField(max_length=150)
    action = models.CharField(max_length=80)
    resource = models.CharField(max_length=100)
    object_id = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
