import django.db.models.deletion
from django.db import migrations, models


def link_existing_private_configs(apps, schema_editor):
    Environment = apps.get_model('automation', 'Environment')
    public_by_code = {
        env.code: env for env in Environment.objects.filter(scope='public')
    }
    for env in Environment.objects.filter(scope='private', public_environment__isnull=True):
        public = public_by_code.get(env.code)
        if public and not Environment.objects.filter(
                owner=env.owner, scope='private', public_environment=public).exists():
            env.public_environment = public
            env.save(update_fields=['public_environment'])


class Migration(migrations.Migration):
    dependencies = [('automation', '0006_environment_systems_and_connections')]
    operations = [
        migrations.AddField(
            model_name='environment', name='public_environment',
            field=models.ForeignKey(blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='private_configs', to='automation.environment')),
        migrations.RunPython(link_existing_private_configs, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='environment',
            constraint=models.UniqueConstraint(
                fields=('owner', 'public_environment'),
                condition=models.Q(scope='private', public_environment__isnull=False),
                name='unique_private_public_env')),
    ]
