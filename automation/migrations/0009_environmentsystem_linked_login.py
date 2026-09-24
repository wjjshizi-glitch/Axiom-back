import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('automation', '0008_move_public_variables_to_owner')]
    operations = [
        migrations.AddField(
            model_name='environmentsystem', name='login_endpoint',
            field=models.ForeignKey(blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+', to='automation.endpoint')),
        migrations.AddField(
            model_name='environmentsystem', name='login_extract',
            field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(
            model_name='environmentsystem', name='login_expires_in',
            field=models.PositiveIntegerField(default=3600)),
    ]
