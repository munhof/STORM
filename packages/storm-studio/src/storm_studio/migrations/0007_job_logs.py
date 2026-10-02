from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('storm_studio', '0006_job_progress'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='logs',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
