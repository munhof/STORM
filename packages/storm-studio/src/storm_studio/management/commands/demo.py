from django.core.management.base import BaseCommand
from storm_studio.models import Project, Study, Revision


class Command(BaseCommand):
    help = 'Create a synthetic study with a supervised plan (does not start training).'

    def handle(self, *args, **options):
        project = Project.objects.create(name='STORM · ejemplo genérico')
        study = Study.objects.create(project=project, name='Comparación y revisión de datos sintéticos')
        Revision.objects.create(study=study, kind='plan', payload={
            'model': 'mean_regressor', 'config': {}, 'seed': 42, 'steps': [],
            'data': {'inputs': list(range(12)), 'targets': [x * 2 for x in range(12)],
                     'train': list(range(8)), 'test': list(range(8, 12))}})
        self.stdout.write(f'Created study {study.pk}. Open /studies/{study.pk}/flow/')
