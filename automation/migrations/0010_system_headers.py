from django.db import migrations, models


def move_headers_to_systems(apps, schema_editor):
    Environment = apps.get_model('automation', 'Environment')
    for environment in Environment.objects.exclude(headers={}):
        for system in environment.systems.all():
            system.headers = {**environment.headers, **system.headers}
            system.save(update_fields=['headers'])
        environment.headers = {}
        environment.save(update_fields=['headers'])


class Migration(migrations.Migration):
    dependencies = [('automation', '0009_environmentsystem_linked_login')]
    operations = [
        migrations.AddField(
            model_name='environmentsystem', name='headers',
            field=models.JSONField(blank=True, default=dict)),
        migrations.RunPython(move_headers_to_systems, migrations.RunPython.noop),
    ]
