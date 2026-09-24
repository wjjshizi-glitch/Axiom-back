from django.db import migrations


def move_public_variables(apps, schema_editor):
    Environment = apps.get_model('automation', 'Environment')
    for public in Environment.objects.filter(scope='public').exclude(variables={}):
        private = Environment.objects.filter(
            scope='private', owner=public.owner,
            public_environment=public).first()
        if not private:
            private = Environment.objects.filter(
                scope='private', owner=public.owner, code=public.code,
                public_environment__isnull=True).first()
        if not private:
            private = Environment.objects.create(
                scope='private', owner=public.owner, public_environment=public,
                name=public.name, code=public.code)
        private.public_environment = public
        private.variables = {**public.variables, **private.variables}
        private.save(update_fields=['public_environment', 'variables'])
        public.variables = {}
        public.save(update_fields=['variables'])


class Migration(migrations.Migration):
    dependencies = [('automation', '0007_environment_public_environment')]
    operations = [
        migrations.RunPython(move_public_variables, migrations.RunPython.noop),
    ]
