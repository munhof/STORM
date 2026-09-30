import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('storm_studio', '0004_dataset_datasetrevision_study_dataset_revision_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='study',
            name='archived_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.CreateModel(
            name='ArchivedPreparation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('archived_at', models.DateTimeField(auto_now_add=True)),
                ('preparation', models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='archive_marker', to='storm_studio.revision')),
            ],
        ),
    ]
