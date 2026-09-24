import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0002_initial'),
        ('automation', '0010_system_headers'),
    ]
    operations = [
        migrations.AddField(
            model_name='user', name='current_project',
            field=models.ForeignKey(blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='active_users', to='automation.project')),
    ]
