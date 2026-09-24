from django.db import migrations


def grant_public_environment_management(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    Group = apps.get_model('auth', 'Group')
    ct, _ = ContentType.objects.get_or_create(app_label='automation', model='environment')
    permission, _ = Permission.objects.get_or_create(
        content_type=ct, codename='manage_public_environment',
        defaults={'name': '维护公共环境配置'})
    # The permission remains an internal marker. Public configuration is enforced
    # by is_superuser and cannot be delegated through a role.


class Migration(migrations.Migration):
    dependencies = [
        ('automation', '0003_environmentsession_alter_environment_options_and_more'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]
    operations = [migrations.RunPython(grant_public_environment_management, migrations.RunPython.noop)]
