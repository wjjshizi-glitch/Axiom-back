import django.db.models.deletion
from django.db import migrations, models


def migrate_environment_systems(apps, schema_editor):
    Environment = apps.get_model('automation', 'Environment')
    EnvironmentSystem = apps.get_model('automation', 'EnvironmentSystem')
    Endpoint = apps.get_model('automation', 'Endpoint')
    for env in Environment.objects.all():
        systems = dict(env.endpoints or {})
        if env.base_url:
            systems.setdefault('default', env.base_url)
        for key, url in systems.items():
            if url:
                EnvironmentSystem.objects.get_or_create(
                    environment=env, system_key=key,
                    defaults={'name': key, 'base_url': url,
                              'verify_ssl': env.verify_ssl,
                              'auth_config': env.auth_config if key == 'default' else {}})
    for endpoint in Endpoint.objects.all():
        endpoint.system_key = endpoint.service_key
        endpoint.save(update_fields=['system_key'])


class Migration(migrations.Migration):
    dependencies = [('automation', '0005_endpoint_callable_methods')]
    operations = [
        migrations.CreateModel(
            name='EnvironmentSystem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('system_key', models.SlugField(max_length=60)),
                ('name', models.CharField(max_length=100)),
                ('base_url', models.URLField()),
                ('verify_ssl', models.BooleanField(default=True)),
                ('auth_config', models.JSONField(blank=True, default=dict)),
                ('credentials', models.TextField(blank=True)),
                ('environment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='systems', to='automation.environment')),
            ],
            options={'ordering': ['-id'], 'default_permissions': ()},
        ),
        migrations.AddField(
            model_name='endpoint', name='system_key',
            field=models.SlugField(default='default', max_length=60),
            preserve_default=False),
        migrations.AddField(
            model_name='environmentsession', name='system_key',
            field=models.SlugField(default='default', max_length=60),
            preserve_default=False),
        migrations.RunPython(migrate_environment_systems, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='environment', name='unique_public_env'),
        migrations.RemoveConstraint(
            model_name='environment', name='unique_private_env'),
        migrations.RemoveConstraint(
            model_name='environmentsession', name='unique_environment_session'),
        migrations.RemoveField(model_name='environment', name='location'),
        migrations.RemoveField(model_name='environment', name='base_url'),
        migrations.RemoveField(model_name='environment', name='endpoints'),
        migrations.RemoveField(model_name='environment', name='verify_ssl'),
        migrations.RemoveField(model_name='environment', name='auth_config'),
        migrations.RemoveField(model_name='endpoint', name='service_key'),
        migrations.AddConstraint(
            model_name='environment',
            constraint=models.UniqueConstraint(
                fields=('code',), condition=models.Q(('scope', 'public')),
                name='unique_public_env')),
        migrations.AddConstraint(
            model_name='environment',
            constraint=models.UniqueConstraint(
                fields=('owner', 'code'), condition=models.Q(('scope', 'private')),
                name='unique_private_env')),
        migrations.AddConstraint(
            model_name='environmentsystem',
            constraint=models.UniqueConstraint(
                fields=('environment', 'system_key'),
                name='unique_environment_system')),
        migrations.AddConstraint(
            model_name='environmentsession',
            constraint=models.UniqueConstraint(
                fields=('owner', 'environment', 'system_key'),
                name='unique_environment_session')),
    ]
