import django.db.models.deletion
from django.db import migrations, models


def assign_projects(apps, schema_editor):
    Environment = apps.get_model('automation', 'Environment')
    Project = apps.get_model('automation', 'Project')
    User = apps.get_model('accounts', 'User')
    for environment in Environment.objects.filter(scope='public'):
        project_ids = set(environment.systems.exclude(
            login_endpoint__isnull=True).values_list(
                'login_endpoint__project_id', flat=True))
        project = Project.objects.filter(
            pk=next(iter(project_ids), None)).first()
        if not project:
            project = Project.objects.filter(owner=environment.owner).order_by('id').first()
        if not project:
            project = Project.objects.create(
                owner=environment.owner, name='默认项目',
                description='由历史环境配置自动创建')
        environment.project = project
        environment.save(update_fields=['project'])
    for environment in Environment.objects.filter(scope='private'):
        project = environment.public_environment.project if environment.public_environment_id else None
        if not project:
            project = Project.objects.filter(owner=environment.owner).order_by('id').first()
        if not project:
            project = Project.objects.create(
                owner=environment.owner, name='默认项目',
                description='由历史环境配置自动创建')
        environment.project = project
        environment.save(update_fields=['project'])
    for user in User.objects.all():
        project_id = None
        if user.current_environment_id:
            project_id = Environment.objects.filter(
                pk=user.current_environment_id).values_list(
                    'project_id', flat=True).first()
        if not project_id:
            project_id = Project.objects.filter(owner=user).order_by('id').values_list(
                'id', flat=True).first()
        if project_id:
            user.current_project_id = project_id
            user.save(update_fields=['current_project'])


class Migration(migrations.Migration):
    dependencies = [
        ('automation', '0010_system_headers'),
        ('accounts', '0003_user_current_project'),
    ]
    operations = [
        migrations.AddField(
            model_name='environment', name='project',
            field=models.ForeignKey(null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='environments', to='automation.project')),
        migrations.RunPython(assign_projects, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='environment', name='unique_public_env'),
        migrations.RemoveConstraint(
            model_name='environment', name='unique_private_env'),
        migrations.AlterField(
            model_name='environment', name='project',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='environments', to='automation.project')),
        migrations.AddConstraint(
            model_name='environment',
            constraint=models.UniqueConstraint(
                fields=('project', 'code'),
                condition=models.Q(scope='public'),
                name='unique_public_env')),
        migrations.AddConstraint(
            model_name='environment',
            constraint=models.UniqueConstraint(
                fields=('owner', 'project', 'code'),
                condition=models.Q(scope='private'),
                name='unique_private_env')),
    ]
