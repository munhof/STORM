from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('storm_studio', '0005_archived_study_and_preparation'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='progress',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
