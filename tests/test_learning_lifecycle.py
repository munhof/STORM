from copy import deepcopy
import pytest
from storm.suite import execute, infer
from storm.artifacts import FileArtifactStore
from storm.testing.online import OnlineMean


class InterruptedMean(OnlineMean):
    def fit_with_checkpoints(self, inputs, targets, checkpoint):
        def interrupted(state):
            checkpoint(state)
            if state['cursor'] == 1:
                raise RuntimeError('Simulated process interruption')
        return super().fit_with_checkpoints(inputs, targets, interrupted)


class EpochMean(OnlineMean):
    def fit_with_checkpoints(self, inputs, targets, checkpoint):
        self.fit(inputs, targets)
        checkpoint({'epoch': 1, 'training_config': {'epochs': 2}})
        checkpoint({'epoch': 2, 'training_config': {'epochs': 2}})


class BatchProgressMean(OnlineMean):
    def set_progress_callback(self, callback):
        self.progress_callback = callback

    def fit(self, inputs, targets=None):
        self.progress_callback({
            'phase': 'training', 'label': 'Entrenamiento por lotes',
            'batch_step': 1, 'batch_total': 2, 'epoch': 1,
            'processed_observations': 1, 'total_observations': 2,
        })
        self.progress_callback({
            'phase': 'training', 'label': 'Entrenamiento por lotes',
            'batch_step': 2, 'batch_total': 2, 'epoch': 1,
            'processed_observations': 2, 'total_observations': 2,
        })
        return super().fit(inputs, targets)


def test_core_forwards_adapter_batches_and_does_not_save_a_live_callback(tmp_path):
    from storm.suite import Component, default_catalog
    catalog = default_catalog()
    catalog.register(Component('batch_mean', BatchProgressMean, ('train', 'infer'), {}))
    updates = []
    result = execute({
        'model': 'batch_mean',
        'data': {'inputs': [0, 1, 2], 'targets': [2, 4, 9], 'train': [0, 1], 'test': [2]},
    }, tmp_path, 'batches', catalog, progress_callback=updates.append)
    batches = [event for event in updates if event.get('batch_total') == 2]
    assert [event['batch_step'] for event in batches] == [1, 2]
    assert all(event['stage_index'] == 3 for event in batches)
    from storm.artifacts import ArtifactRef
    model = FileArtifactStore(tmp_path).load(ArtifactRef.from_dict(result['model_ref']))
    assert model.progress_callback is None


def test_recovery_after_partial_training_does_not_repeat_samples(tmp_path):
    from storm.suite import default_catalog, Component
    catalog = default_catalog()
    catalog.register(Component('interruptible', InterruptedMean, ('train', 'infer', 'checkpoint'), {'properties': {}}))
    spec = {'model': 'interruptible', 'data': {'inputs': [0, 1, 2], 'targets': [2, 4, 9], 'train': [0, 1], 'test': [2]}}
    with pytest.raises(RuntimeError):
        execute(spec, tmp_path, 'interrupted', catalog)
    ref = FileArtifactStore(tmp_path).resolve(kind='checkpoints', artifact_id='interrupted')
    result = execute(spec, tmp_path, 'resumed', catalog, resume_from=ref)
    assert result['predictions'] == [3]


def test_incremental_update_keeps_original_model(tmp_path):
    spec = {'model': 'online_mean', 'config': {}, 'data': {
        'inputs': [0, 1, 2], 'targets': [2, 4, 9], 'train': [0, 1], 'test': [2]}}
    original = execute(spec, tmp_path, 'base')
    updated_spec = deepcopy(spec)
    updated_spec['data'] = {'inputs': [3, 4], 'targets': [12, 9], 'train': [0], 'test': [1]}
    updated = execute(updated_spec, tmp_path, 'updated', update_from=original)
    assert infer(original, [1], tmp_path) == [3]
    assert infer(updated, [1], tmp_path) == [6]
    assert updated['source_execution'] == 'base'


def test_checkpoint_resume_matches_completed_run_and_rejects_changes(tmp_path):
    spec = {'model': 'online_mean', 'data': {'inputs': [0, 1, 2], 'targets': [2, 4, 9],
                                         'train': [0, 1], 'test': [2]}}
    original = execute(spec, tmp_path, 'base')
    checkpoint = FileArtifactStore(tmp_path).resolve(kind='checkpoints', artifact_id='base')
    resumed = execute(spec, tmp_path, 'resumed', resume_from=checkpoint)
    assert resumed['predictions'] == original['predictions']
    changed = deepcopy(spec)
    changed['data']['targets'][0] = 100
    with pytest.raises(ValueError, match='compatible'):
        execute(changed, tmp_path, 'bad', resume_from=checkpoint)


def test_execution_reports_current_phase_and_checkpoint_epochs(tmp_path):
    from storm.suite import Component, default_catalog

    catalog = default_catalog()
    catalog.register(Component(
        'epoch_mean', EpochMean, ('train', 'infer', 'checkpoint'), {
            'type': 'object', 'properties': {'epochs': {'type': 'integer'}},
        }))
    progress = []
    execute({
        'model': 'epoch_mean', 'config': {'epochs': 2},
        'data': {'inputs': [0, 1, 2], 'targets': [2, 4, 9], 'train': [0, 1], 'test': [2]},
    }, tmp_path, 'progress-run', catalog, progress_callback=progress.append)

    store = FileArtifactStore(tmp_path)
    for epoch in (1, 2):
        saved = store.resolve(kind='checkpoints', artifact_id=f'progress-run-epoch-{epoch}')
        assert store.load(saved)['state']['epoch'] == epoch
    latest = store.resolve(kind='checkpoints', artifact_id='progress-run')
    assert store.load(latest)['state']['epoch'] == 2

    phases = [item['phase'] for item in progress]
    assert phases[0] == 'loading'
    assert 'preparing' in phases
    assert 'training' in phases
    assert 'evaluating' in phases
    assert phases[-1] == 'saving'
    checkpoints = [item for item in progress
                   if item['phase'] == 'training' and item.get('phase_total') == 2
                   and item.get('phase_step', 0) > 0]
    assert [item['phase_step'] for item in checkpoints] == [1, 2]
    assert all(item['unit_label'] == 'épocas' for item in checkpoints)


@pytest.mark.django_db
def test_annotations_and_interpretations_do_not_mutate_predictions(settings, tmp_path):
    from storm_studio.models import Project, Study, Revision
    from storm_studio.services import submit, perform, annotate
    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    plan = Revision.objects.create(study=study, kind='plan', payload={'model': 'constrained_groups',
        'data': {'inputs': [0, 1, 2], 'train': [0, 1, 2], 'test': []}})
    job = submit(plan)
    perform(str(job.pk))
    job.refresh_from_db()
    before = deepcopy(job.result)
    revision = annotate(job, taxonomy=['A', 'B'], intervals=[{'start': 0, 'stop': 2, 'label': 'A'}],
                        mapping={'0': 'A'}, author='Reviewer', reason='Video correction')
    assert revision.kind == 'annotations'
    job.refresh_from_db()
    assert job.result == before
    with pytest.raises(ValueError):
        annotate(job, taxonomy=['A'], intervals=[{'start': 0, 'stop': 99, 'label': 'A'}],
                 mapping={}, author='Reviewer', reason='Video correction')


@pytest.mark.django_db
def test_web_incremental_resume_and_visualization(client, settings, tmp_path):
    import json
    from storm_studio.models import Project, Study, Revision, Job
    from storm_studio.services import submit, perform
    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    plan = Revision.objects.create(study=study, kind='plan', payload={'model': 'online_mean',
        'data': {'inputs': [0, 1, 2], 'targets': [2, 4, 9], 'train': [0, 1], 'test': [2]}})
    job = submit(plan)
    perform(str(job.pk))
    assert client.get(f'/visualize/{job.pk}/metric_bar/').status_code == 200
    assert client.post(f'/jobs/{job.pk}/resume/').status_code == 302
    resumed = Job.objects.get(operation='resume')
    perform(str(resumed.pk))
    resumed.refresh_from_db()
    assert resumed.result['predictions'] == [3]
    assert client.post(f'/jobs/{job.pk}/update/', {'data': json.dumps({
        'inputs': [3, 4], 'targets': [12, 9], 'train': [0], 'test': [1]})}).status_code == 302
    updated = Job.objects.get(operation='update')
    perform(str(updated.pk))
    updated.refresh_from_db()
    assert updated.result['predictions'] == [6]
    job.refresh_from_db()
    assert job.result['predictions'] == [3]


@pytest.mark.django_db
def test_web_resume_can_switch_only_the_runtime_device(client, settings, tmp_path, monkeypatch):
    from storm.suite import Component, default_catalog
    from storm_studio.models import Project, Study, Revision, Job
    from storm_studio import services

    settings.ARTIFACT_ROOT = tmp_path
    catalog = default_catalog()
    catalog.register(Component(
        'device_mean', OnlineMean, ('train', 'infer', 'checkpoint'), {
            'type': 'object', 'properties': {
                'device': {'type': 'string', 'default': 'auto',
                           'enum': ['auto', 'cpu', 'cuda']},
            },
        }))
    monkeypatch.setattr(services, 'catalog', lambda: catalog)
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'device_mean', 'config': {'device': 'cpu'},
        'data': {'inputs': [0, 1, 2], 'targets': [2, 4, 9],
                 'train': [0, 1], 'test': [2]},
    })
    source = services.submit(plan)
    services.perform(str(source.pk))

    page = client.get(f'/studies/{study.pk}/jobs/')
    assert page.status_code == 200
    assert b'name="device"' in page.content
    assert b'Autom\xc3\xa1tico' in page.content
    assert b'GPU (CUDA/ROCm)' in page.content

    assert client.post(f'/jobs/{source.pk}/resume/', {'device': 'cuda'}).status_code == 302
    resumed = Job.objects.get(operation='resume')
    assert resumed.revision.payload['config']['device'] == 'cuda'
    services.perform(str(resumed.pk))
    resumed.refresh_from_db()
    assert resumed.status == 'completed'
