import re
from django.db import migrations, models


def fill_function_names(apps, schema_editor):
    Endpoint = apps.get_model('automation', 'Endpoint')
    used = {}
    for endpoint in Endpoint.objects.order_by('project_id', 'id'):
        base = endpoint.function_name or f'api_{endpoint.id}'
        base = re.sub(r'[^a-z0-9_]', '_', base.lower()).strip('_')
        if not base or not base[0].isalpha():
            base = f'api_{endpoint.id}'
        candidate = base
        index = 2
        while candidate in used.setdefault(endpoint.project_id, set()):
            candidate = f'{base}_{index}'
            index += 1
        endpoint.function_name = candidate
        endpoint.save(update_fields=['function_name'])
        used[endpoint.project_id].add(candidate)


class Migration(migrations.Migration):
    dependencies = [('automation', '0004_public_environment_permission')]
    operations = [
        migrations.RunPython(fill_function_names, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='endpoint', name='function_name',
            field=models.SlugField(max_length=100)),
        migrations.AddConstraint(
            model_name='endpoint',
            constraint=models.UniqueConstraint(
                fields=('project', 'function_name'),
                name='unique_project_endpoint_method')),
    ]
