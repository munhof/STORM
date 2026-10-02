import pytest

pytestmark = pytest.mark.django_db


class ImportedInferenceModel:
    def __init__(self, config):
        pass

    def predict(self, inputs):
        from storm import ModelOutput
        return ModelOutput([value * 2 for value in inputs])


def test_plan_accepts_empty_configuration_and_steps():
    from storm_studio.forms import PlanForm
    form = PlanForm({'model': 'identity', 'config': '{}', 'steps': '[]', 'seed': 42,
                     'data': '{"inputs":[1,2],"train":[0],"test":[1]}'})
    assert form.is_valid(), form.errors


def test_models_page_points_to_training_execution_and_evaluation_flow(client):
    from storm_studio.models import Project, Study

    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Model workflow')

    response = client.get(f'/studies/{study.pk}/models/')

    assert response.status_code == 200
    assert b'Configurar entrenamiento y evaluaci\xc3\xb3n' in response.content
    assert b'Revisar datos y particiones' in response.content
    assert b'Inici\xc3\xa1 la corrida' in response.content
    assert b'Guard\xc3\xa1 el plan para habilitar el inicio desde Ejecuciones' in response.content


def test_execution_page_shows_live_progress_eta_and_checkpoint_resume(
        client, settings, tmp_path):
    from django.utils import timezone
    from storm.artifacts import FileArtifactStore
    from storm_studio.models import Job, Project, Revision, Study

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Execution progress')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'vame_native', 'config': {}, 'steps': [], 'data': {},
    })
    active = Job.objects.create(
        revision=plan, status='running', operation='train', started=timezone.now(),
        progress={
            'phase': 'training', 'label': 'Entrenando el modelo',
            'stage_index': 3, 'stage_total': 5,
            'phase_step': 2, 'phase_total': 5, 'fraction': 0.4,
            'eta_seconds': 90, 'updated_at': timezone.now().isoformat(),
        })
    interrupted = Job.objects.create(
        revision=plan, status='interrupted', operation='train')
    FileArtifactStore(settings.ARTIFACT_ROOT).save(
        kind='checkpoints', artifact_id=str(interrupted.pk),
        value={'state': {'epoch': 2, 'training_config': {'epochs': 5}}})

    response = client.get(f'/studies/{study.pk}/jobs/')

    assert response.status_code == 200
    rendered_active = next(job for job in response.context['jobs'] if job.pk == active.pk)
    assert rendered_active.progress_percent == 40
    assert rendered_active.stages_after_current == 2
    assert rendered_active.progress_units_remaining == 3
    assert b'Entrenando el modelo' in response.content
    assert b'1 en curso' in response.content
    assert b'role="progressbar"' in response.content
    assert b'value="40"' in response.content
    assert b'1 min 30 s' in response.content
    assert b'Tiempo restante estimado' in response.content
    assert b'Retomar desde el \xc3\xbaltimo checkpoint' in response.content
    status_response = client.get(f'/status/{study.pk}/')
    assert status_response.json()['summary']['label'] == '1 en curso'
    active_status = next(
        item for item in status_response.json()['jobs'] if item['id'] == str(active.pk))
    assert active_status['status_label'] == 'En curso'
    assert active_status['progress']['label'] == 'Entrenando el modelo'
    assert active_status['progress_percent'] == 40
    assert active_status['eta_text'] == '1 min 30 s'


def test_execution_page_shows_expandable_bounded_worker_logs(client):
    from storm_studio.models import Job, Project, Revision, Study
    from storm_studio.services import append_job_logs

    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Worker logs')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'config': {}, 'steps': [], 'data': {},
    })
    job = Job.objects.create(revision=plan, status='running', operation='train')
    append_job_logs(job.pk, [f'mensaje {index}' for index in range(102)])
    append_job_logs(job.pk, ['<script>mensaje de worker</script>'])

    page = client.get(f'/studies/{study.pk}/jobs/')
    status = client.get(f'/status/{study.pk}/').json()
    job_status = next(item for item in status['jobs'] if item['id'] == str(job.pk))

    assert page.status_code == 200
    assert 'Últimos logs del proceso'.encode() in page.content
    assert b'mensaje 0' not in page.content
    assert b'mensaje 3' in page.content
    assert b'&lt;script&gt;mensaje de worker&lt;/script&gt;' in page.content
    assert len(job_status['logs']) == 100
    assert job_status['logs'][-1]['message'] == '<script>mensaje de worker</script>'


def test_execution_page_shows_current_progress_when_worker_logs_are_empty(client):
    from storm_studio.models import Job, Project, Revision, Study

    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='No worker output yet')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'vame_native', 'config': {}, 'steps': [], 'data': {},
    })
    Job.objects.create(revision=plan, status='running', operation='resume', progress={
        'label': 'Preparando los datos para el modelo', 'stage_index': 2,
        'stage_total': 5,
    })

    page = client.get(f'/studies/{study.pk}/jobs/')

    assert page.status_code == 200
    assert 'Preparando los datos para el modelo'.encode() in page.content
    assert 'Etapa 2 de 5'.encode() in page.content
    assert 'Últimos logs del proceso y sus avances (1)'.encode() in page.content


def test_worker_progress_estimates_remaining_epochs_from_observed_rate(monkeypatch):
    from datetime import timedelta
    from django.utils import timezone
    from storm_studio import services
    from storm_studio.models import Job, Project, Revision, Study

    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Progress estimate')
    plan = Revision.objects.create(study=study, kind='plan', payload={'model': 'vame_native'})
    job = Job.objects.create(revision=plan, status='running')
    start = timezone.now()
    timestamps = iter((start, start + timedelta(seconds=10)))
    monkeypatch.setattr(services.timezone, 'now', lambda: next(timestamps))

    services._record_progress(str(job.pk), {
        'phase': 'training', 'label': 'Entrenamiento', 'stage_index': 3,
        'stage_total': 5, 'phase_step': 1, 'phase_total': 5,
    })
    services._record_progress(str(job.pk), {
        'phase': 'training', 'label': 'Entrenamiento', 'stage_index': 3,
        'stage_total': 5, 'phase_step': 2, 'phase_total': 5,
    })

    job.refresh_from_db()
    assert job.progress['fraction'] == 0.4
    assert job.progress['eta_seconds'] == 30


@pytest.mark.django_db
def test_completed_execution_keeps_a_visible_trace_for_all_core_stages(
        client, settings, tmp_path):
    from storm_studio.models import Project, Study, Revision
    from storm_studio.services import perform, submit

    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Trace')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'online_mean', 'config': {},
        'data': {'inputs': [0, 1, 2], 'targets': [2, 4, 9], 'train': [0, 1], 'test': [2]},
    })
    job = submit(plan)
    perform(str(job.pk))

    job.refresh_from_db()
    trace = job.progress.get('trace', [])
    assert {event['stage_index'] for event in trace} == {1, 2, 3, 4, 5}
    assert any('Cargando y validando' in event['message'] for event in trace)
    assert any('Guardando' in event['message'] for event in trace)

    response = client.get(f'/status/{study.pk}/')
    assert response.status_code == 200
    status_job = next(item for item in response.json()['jobs'] if item['id'] == str(job.pk))
    assert any('Preparando los datos para el modelo' in event['message']
               for event in status_job['logs'])


def test_batch_progress_is_visible_and_failure_keeps_the_last_safe_point(
        client, settings, tmp_path, monkeypatch):
    from storm_studio import services
    from storm_studio.models import Project, Study, Revision
    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Batch error')
    plan = Revision.objects.create(study=study, kind='plan', payload={'model': 'identity'})
    job = services.submit(plan)

    def fail(spec, root, execution_id, catalog, *, progress_callback, **options):
        progress_callback({
            'phase': 'training', 'label': 'Entrenando', 'stage_index': 3, 'stage_total': 5,
            'phase_step': 1, 'phase_total': 10, 'unit_label': 'épocas',
            'batch_step': 3, 'batch_total': 4, 'epoch': 2,
            'processed_observations': 6, 'total_observations': 8,
            'throughput': 2.0, 'device': 'cuda', 'checkpoint_epoch': 1,
        })
        page = client.get(f'/studies/{study.pk}/jobs/').content.decode()
        assert 'Lote 3 de 4' in page
        assert '75%' in page
        assert 'observaciones/s' in page
        raise RuntimeError('simulated batch failure')

    monkeypatch.setattr(services, 'execute', fail)
    services.perform(str(job.pk))
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.progress['failure_context']['batch_step'] == 3
    assert job.progress['failure_context']['checkpoint_epoch'] == 1
    assert job.progress['failure_context']['epoch'] == 2
    assert job.progress['failure_context']['device'] == 'cuda'


def test_processing_execution_uses_a_previous_matching_recipe_for_eta(client):
    from datetime import timedelta
    from django.utils import timezone
    from storm_studio.models import Job, Project, Revision, Study

    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Preparation ETA')
    recipe = Revision.objects.create(study=study, kind='preparation', payload={
        'name': 'Pose centrada', 'dataset_revision_id': 9, 'steps': [{'type': 'center'}],
    })
    now = timezone.now()
    Job.objects.create(
        revision=recipe, operation='prepare', status='completed',
        started=now - timedelta(seconds=210), finished=now - timedelta(seconds=30))
    active = Job.objects.create(
        revision=recipe, operation='prepare', status='running',
        started=now - timedelta(seconds=30))

    response = client.get(f'/studies/{study.pk}/jobs/')

    rendered = next(job for job in response.context['jobs'] if job.pk == active.pk)
    assert '2 min' in rendered.eta_text
    assert rendered.eta_basis == 'duración mediana de ejecuciones anteriores comparables'


def test_run_rejects_a_model_when_its_required_preparation_step_is_missing(client, monkeypatch):
    from storm.suite import Component, default_catalog
    from storm.pipeline import PipelineStep
    from storm_studio import services
    from storm_studio.models import Project, Study, Revision, Job

    class NativeVAME:
        required_pipeline_steps = ('pose.temporal_windows',)

    class TemporalWindows(PipelineStep):
        step_type = 'pose.temporal_windows'

        def process(self, context):
            return context

    catalog = default_catalog()
    catalog.steps.register(TemporalWindows)
    catalog.register(Component('vame_native', NativeVAME, ('group',), {'type': 'object'}))
    monkeypatch.setattr(services, 'catalog', lambda: catalog)
    study = Study.objects.create(project=Project.objects.create(name='P'), name='VAME setup')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'vame_native', 'config': {}, 'steps': [],
        'data': {'inputs': [[1], [2]], 'train': [0], 'test': [1]},
    })

    response = client.post(f'/plans/{plan.pk}/run/', follow=True)

    assert Job.objects.filter(revision=plan).count() == 0
    assert 'No se inició la corrida de VAME nativo'.encode() in response.content
    assert b'Crear ventanas temporales' in response.content
    assert b'<code>pose.temporal_windows</code>' not in response.content
    assert 'La corrida no empezó y no modificó tus datos'.encode() in response.content


def test_run_accepts_prepared_dataset_that_already_contains_required_model_steps(
        client, monkeypatch):
    from storm.pipeline import PipelineStep
    from storm.suite import Component, default_catalog
    from storm_studio import services
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study, Job

    class NativeVAME:
        required_pipeline_steps = ('pose.temporal_windows',)

    class TemporalWindows(PipelineStep):
        step_type = 'pose.temporal_windows'

        def process(self, context):
            return context

    catalog = default_catalog()
    catalog.steps.register(TemporalWindows)
    catalog.register(Component('vame_native', NativeVAME, ('group',), {'type': 'object'}))
    monkeypatch.setattr(services, 'catalog', lambda: catalog)
    dataset = Dataset.objects.create(name='Prepared pose')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready')
    study = Study.objects.create(project=Project.objects.create(name='P'), name='VAME setup',
                                 dataset_revision=source)
    recipe = Revision.objects.create(study=study, kind='preparation', payload={
        'name': 'VAME windows', 'dataset_revision_id': source.pk,
        'steps': [{'type': 'pose.temporal_windows',
                   'config': {'offsets': [-1, 0, 1]}}],
    })
    prepared = DatasetRevision.objects.create(
        dataset=dataset, number=2, connector='prepared_artifact', status='ready',
        config={'source_dataset_revision_id': source.pk,
                'preparation_revision_id': recipe.pk},
        artifact_ref={'kind': 'datasets', 'artifact_id': 'prepared-test'})
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'vame_native', 'config': {}, 'steps': [],
        'dataset_revision_id': prepared.pk, 'connector': 'prepared_artifact',
        'data': {},
    })

    response = client.post(f'/plans/{plan.pk}/run/')

    assert response.status_code == 302
    job = Job.objects.get(revision=plan, status='pending')

    captured = {}

    def execute_prepared(spec, *args, **kwargs):
        captured.update(spec)
        return {'resolved_data': spec['data']}

    from storm_studio import dataset_inventory
    monkeypatch.setattr(dataset_inventory, 'connector_input', lambda *args, **kwargs: {
        'inputs': [[[0.0], [1.0], [2.0]]], 'train': [0], 'test': [],
    })
    monkeypatch.setattr(services, 'execute', execute_prepared)
    services.perform(job.pk)

    assert captured['preapplied_steps'] == ['pose.temporal_windows']


def test_execute_accepts_preapplied_steps_without_reapplying_pipeline(
        client, tmp_path, monkeypatch):
    from storm.pipeline import PipelineStep
    from storm.suite import Component, default_catalog, execute
    from storm.testing.models import IdentityModel

    monkeypatch.setattr(
        IdentityModel, 'required_pipeline_steps', ('pose.temporal_windows',),
        raising=False)

    class TemporalWindows(PipelineStep):
        step_type = 'pose.temporal_windows'

        def process(self, context):
            pytest.fail('a materialized temporal-window step must not run again')

    catalog = default_catalog()
    catalog.steps.register(TemporalWindows)
    catalog.register(Component('prepared_model', IdentityModel, ('train',), {
        'type': 'object', 'properties': {},
    }))

    result = execute({
        'model': 'prepared_model', 'connector': 'json_records', 'config': {},
        'steps': [], 'preapplied_steps': ['pose.temporal_windows'], 'metrics': [],
        'data': {
            'inputs': [[[0.0], [1.0], [2.0]], [[1.0], [2.0], [3.0]]],
            'targets': [0, 1], 'train': [0], 'test': [1],
        },
    }, tmp_path, 'prepared-model', catalog)

    assert result['predictions'] == [[[1.0], [2.0], [3.0]]]
    assert result['fitted_steps'] == []
    assert result['spec']['preapplied_steps'] == ['pose.temporal_windows']


def test_flow_can_load_a_registered_historical_recipe_preset(client, monkeypatch):
    from storm.suite import Component, default_catalog
    from storm_studio import forms, services
    from storm_studio.models import Project, Study

    catalog = default_catalog()
    catalog.register(Component('vame_native', lambda config: None, ('group',), {
        'type': 'object', 'properties': {
            'n_states': {'type': 'integer', 'default': 50},
        },
    }))
    catalog.recipe_presets = {
        'vame_native_pose_ego': {
            'name': 'VAME nativo · pose_ego', 'model': 'vame_native',
            'config': {'n_states': 50},
            'steps': [{'type': 'pose.temporal_windows',
                       'config': {'offsets': [-1, 0, 1]}}],
            'provenance': 'Tesis_Facu · receta histórica reconstruida',
        },
    }
    monkeypatch.setattr(services, 'catalog', lambda: catalog)
    monkeypatch.setattr(forms, 'catalog', lambda: catalog)
    study = Study.objects.create(project=Project.objects.create(name='P'), name='VAME')

    response = client.get(
        f'/studies/{study.pk}/flow/?recipe_preset=vame_native_pose_ego')

    assert response.status_code == 200
    assert response.context['form'].initial['model'] == 'vame_native'
    assert response.context['form'].initial['config'] == {'n_states': 50}
    assert response.context['form'].initial['steps'] == [{
        'type': 'pose.temporal_windows', 'config': {'offsets': [-1, 0, 1]},
    }]
    assert b'Tesis_Facu' in response.content


def test_prepare_can_restore_an_older_recipe_and_save_a_version_from_it(client):
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study

    dataset = Dataset.objects.create(name='Pose')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='json_records', status='ready',
        inventory={'feature_names': ['nose_x', 'nose_y']})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Recipes',
                                 dataset_revision=source)
    original = Revision.objects.create(study=study, kind='preparation', payload={
        'name': 'Primera centrada', 'dataset_revision_id': source.pk,
        'steps': [{'type': 'center'}], 'data': {},
    })
    Revision.objects.create(study=study, kind='preparation', parent=original, payload={
        'name': 'Última escalada', 'dataset_revision_id': source.pk,
        'steps': [{'type': 'scale', 'factor': 2}], 'data': {},
    })

    response = client.get(
        f'/studies/{study.pk}/prepare/?recipe_revision={original.pk}')

    assert response.status_code == 200
    assert response.context['form'].initial['name'] == 'Primera centrada'
    assert response.context['form'].initial['steps'] == [{'type': 'center'}]
    assert response.context['form'].initial['base_revision_id'] == original.pk

    response = client.post(f'/studies/{study.pk}/prepare/', {
        'name': 'Primera centrada ajustada',
        'dataset_revision_id': str(source.pk),
        'steps': '[{"type":"center"}]', 'data': '{}',
        'base_revision_id': str(original.pk),
    })

    assert response.status_code == 302
    restored_successor = Revision.objects.get(
        study=study, kind='preparation', payload__name='Primera centrada ajustada')
    assert restored_successor.parent_id == original.pk


def test_infer_plan_requires_a_pretrained_adapter_instead_of_a_saved_training_run(monkeypatch):
    from storm.suite import Component, default_catalog
    from storm_studio import forms

    catalog = default_catalog()
    catalog.register(Component('imported.pretrained', lambda config: None, ('infer',), {
        'type': 'object', 'properties': {},
    }))
    monkeypatch.setattr(forms, 'catalog', lambda: catalog)
    base = {
        'operation': 'infer', 'config': '{}', 'seed': 42,
        'data': '{"inputs":[1,2],"train":[],"test":[0,1]}',
    }

    trained_model_form = forms.PlanForm({**base, 'model': 'identity'})
    pretrained_form = forms.PlanForm({**base, 'model': 'imported.pretrained'})

    assert not trained_model_form.is_valid()
    assert 'corrida guardada' in str(trained_model_form.errors).lower()
    assert pretrained_form.is_valid(), pretrained_form.errors


def test_inference_plan_is_queued_as_inference_and_runs_without_training(client, monkeypatch, settings, tmp_path):
    from storm.suite import Component, default_catalog
    from storm_studio.models import Project, Study, Revision, Job
    from storm_studio import services

    catalog = default_catalog()
    catalog.register(Component('imported.inference_only', ImportedInferenceModel, ('infer',), {'properties': {}}))
    monkeypatch.setattr(services, 'catalog', lambda: catalog)
    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    revision = Revision.objects.create(study=study, kind='plan', payload={
        'operation': 'infer', 'model': 'imported.inference_only', 'config': {},
        'data': {'inputs': [3, 5], 'train': [], 'test': [0, 1]},
    })

    response = client.post(f'/plans/{revision.pk}/run/')

    assert response.status_code == 302
    jobs_page = client.get(f'/studies/{study.pk}/jobs/')
    job = Job.objects.get(revision=revision)
    assert job.operation == 'infer'
    assert b'Plan en ejecuci\xc3\xb3n' in jobs_page.content
    services.perform(str(job.pk))
    job.refresh_from_db()
    assert job.status == 'completed', job.error
    assert job.result['predictions'] == [6, 10]
    jobs_page = client.get(f'/studies/{study.pk}/jobs/')
    assert 'Inferencia'.encode() in jobs_page.content


def test_saved_model_runs_on_a_registered_dataset_revision(client, settings, tmp_path):
    from storm.artifacts import FileArtifactStore
    from storm_studio.models import Dataset, DatasetRevision, Project, Study, Revision, Job
    from storm_studio.services import perform, submit

    settings.ARTIFACT_ROOT = tmp_path
    store = FileArtifactStore(tmp_path)
    dataset = Dataset.objects.create(name='Pose sessions')
    training_data = {
        'inputs': [[1], [2], [3]], 'targets': [2, 4, 100],
        'train': [0, 1], 'test': [2],
    }
    training_artifact = store.save(
        kind='datasets', artifact_id='training-pose', value=training_data)
    training_revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='prepared_artifact', status='ready',
        artifact_ref=training_artifact.to_dict())
    target_data = {'inputs': [[10], [20]], 'train': [], 'test': []}
    target_artifact = store.save(
        kind='datasets', artifact_id='target-pose', value=target_data)
    target_revision = DatasetRevision.objects.create(
        dataset=dataset, number=2, connector='prepared_artifact', status='ready',
        artifact_ref=target_artifact.to_dict())
    study = Study.objects.create(
        project=Project.objects.create(name='Pose project'), name='New session inference',
        dataset_revision=target_revision)
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'mean_regressor', 'config': {}, 'connector': 'prepared_artifact',
        'dataset_revision_id': training_revision.pk, 'data': {}, 'steps': [],
        'metrics': ['mae'], 'seed': 42,
    })
    training_job = submit(plan)
    perform(str(training_job.pk))
    training_job.refresh_from_db()
    assert training_job.status == 'completed', training_job.error

    models_page = client.get(f'/studies/{study.pk}/models/')
    assert models_page.status_code == 200
    assert b'Aplicar a una revisi' in models_page.content

    response = client.post(
        f'/jobs/{training_job.pk}/apply/',
        {'dataset_revision_id': str(target_revision.pk)})

    assert response.status_code == 302
    inference_job = Job.objects.get(operation='apply', source=training_job)
    perform(str(inference_job.pk))
    inference_job.refresh_from_db()
    assert inference_job.status == 'completed', inference_job.error
    assert inference_job.result['predictions'] == [3.0, 3.0]
    assert inference_job.result['indices'] == [0, 1]
    assert inference_job.result['evaluation_status'] == 'inspection_only_no_reference'
    assert inference_job.result['model_ref'] == training_job.result['model_ref']
    assert inference_job.result['source_execution'] == training_job.result['execution_id']


def test_saved_model_apply_reuses_the_resolved_preparation_steps(client):
    from storm_studio.models import Dataset, DatasetRevision, Job, Project, Study, Revision

    dataset = Dataset.objects.create(name='DLC sessions')
    target = DatasetRevision.objects.create(
        dataset=dataset, number=2, connector='dlc_h5', status='ready',
        artifact_ref={'artifact_id': 'target-h5'},
    )
    study = Study.objects.create(
        project=Project.objects.create(name='Pose project'),
        name='Reuse resolved pipeline', dataset_revision=target,
    )
    symbolic_steps = [{'type': 'pose.recenter', 'config': {
        'center_bodypart': 'body', 'bodyparts': ['nose', 'body'],
    }}]
    resolved_steps = [{'type': 'pose.recenter', 'config': {
        'center_indices': [2, 3], 'coordinate_pairs': [[0, 1], [2, 3]],
    }}]
    source_revision = Revision.objects.create(study=study, kind='plan', payload={
        'operation': 'train', 'model': 'vame_native', 'connector': 'dlc_h5',
        'config': {}, 'steps': symbolic_steps,
    })
    source = Job.objects.create(
        revision=source_revision, status='completed', result={
            'model': 'vame_native', 'model_version': '1', 'capabilities': ['infer'],
            'model_ref': {'artifact_id': 'saved-vame', 'kind': 'models',
                          'digest': 'sha256:' + 'a' * 64, 'uri': 'models/saved-vame'},
            'spec': {'model': 'vame_native', 'connector': 'dlc_h5', 'config': {},
                     'steps': resolved_steps},
        })

    response = client.post(f'/jobs/{source.pk}/apply/', {
        'dataset_revision_id': str(target.pk),
    })

    assert response.status_code == 302
    applied = Revision.objects.get(parent=source_revision, kind='plan')
    assert applied.payload['steps'] == resolved_steps


def test_failed_worker_job_keeps_traceback_in_worker_logs(caplog, settings, tmp_path, monkeypatch):
    import logging
    from storm_studio import services
    from storm_studio.models import Project, Study, Revision

    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Worker error')
    revision = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'config': {},
        'data': {'inputs': [1, 2], 'train': [0], 'test': [1]},
    })
    job = services.submit(revision)

    def fail_with_missing_inputs(*_args, **_kwargs):
        raise KeyError('inputs')

    monkeypatch.setattr(services, 'execute', fail_with_missing_inputs)
    with caplog.at_level(logging.ERROR, logger='storm_studio.services'):
        services.perform(str(job.pk))

    assert "KeyError: 'inputs'" in caplog.text
    assert 'Traceback' in caplog.text


def test_worker_resolves_pose_steps_from_registered_inventory_features(monkeypatch):
    from storm.suite import default_catalog
    from storm_studio import dataset_inventory, services
    from storm_studio.models import (Dataset, DatasetRevision, Job, Project,
                                    Revision, Study)

    dataset = Dataset.objects.create(name='Registered pose')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'feature_names': ['nose_x', 'nose_y', 'body_x', 'body_y']})
    study = Study.objects.create(
        project=Project.objects.create(name='Pose project'), name='Named pipeline',
        dataset_revision=source)
    steps = [{'type': 'pose.recenter', 'config': {
        'center_bodypart': 'body', 'bodyparts': ['nose', 'body'],
    }}]
    preparation = Revision.objects.create(study=study, kind='preparation', payload={
        'dataset_revision_id': source.pk, 'steps': steps,
    })
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'operation': 'infer', 'model': 'identity', 'config': {},
        'connector': 'dlc_h5', 'dataset_revision_id': source.pk,
        'preparation_revision_id': preparation.pk, 'steps': steps, 'data': {},
    })
    monkeypatch.setattr(services, 'catalog', default_catalog)
    monkeypatch.setattr(dataset_inventory, 'connector_input',
                        lambda *_args, **_kwargs: {'pose_paths': ['pose.h5']})
    monkeypatch.setattr(services, 'execute', lambda spec, *_args, **_kwargs: {'spec': spec})

    job = services.submit(plan)
    services.perform(str(job.pk))

    job = Job.objects.get(pk=job.pk)
    assert job.status == 'completed', job.error
    assert job.result['spec']['steps'][0]['config'] == {
        'center_indices': [2, 3], 'coordinate_pairs': [[0, 1], [2, 3]],
    }


def test_plan_form_keeps_visual_branch_configuration_and_partitions():
    import json
    from storm_studio.forms import PlanForm

    form = PlanForm({
        'model': 'identity', 'config': '{}', 'seed': 42, 'connector': 'numeric_json',
        'steps': '[{"type":"scale","factor":3.5}]',
        'branch_models': ['constant'],
        'branch_configs': '{"constant":{"value":9}}',
        'data': '{"inputs":[1,2,3],"train":[0,1],"test":[2]}',
    })

    assert form.is_valid(), form.errors
    assert form.cleaned_data['branch_models'] == ['constant']
    assert form.cleaned_data['branch_configs'] == {'constant': {'value': 9}}
    assert form.cleaned_data['steps'] == [{'type': 'scale', 'factor': 3.5}]
    assert json.loads(json.dumps(form.cleaned_data['data']))['train'] == [0, 1]


def test_study_header_exposes_traceable_lifecycle(client, settings, tmp_path):
    from storm_studio.models import Project, Study, Revision
    from storm_studio.services import submit, perform
    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    revision = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'data': {'inputs': [1, 2], 'targets': [1, 2], 'train': [0], 'test': [1]}})
    job = submit(revision)
    perform(str(job.pk))
    page = client.get(f'/studies/{study.pk}/evidence/')
    assert b'Estado del estudio' in page.content
    assert b'Plan activo' in page.content
    assert b'Ejecuci' in page.content
    assert b'Evidencia disponible' in page.content


def test_study_uses_task_sidebar_and_single_study_header(client):
    from storm_studio.models import Project, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    response = client.get(f'/studies/{study.pk}/flow/')

    assert response.status_code == 200
    assert response.content.count(b'<header') == 1
    assert b'aria-label="Etapas del estudio"' in response.content
    for label in ('Datos', 'Preparar', 'Configurar', 'Modelar', 'Ejecuciones',
                  'Analizar', 'Revisar', 'Comparar', 'Reportar'):
        assert label.encode() in response.content
    assert b'Etiquetar' not in response.content
    assert 'Próxima acción'.encode() in response.content
    assert b'Dataset activo' in response.content


def test_flow_page_exposes_pipeline_settings_without_editable_json(client):
    from storm_studio.models import Project, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    response = client.get(f'/studies/{study.pk}/flow/')
    content = response.content.decode()

    assert response.status_code == 200
    assert 'type="hidden" name="config"' in content
    assert 'type="hidden" name="steps"' in content
    assert 'type="hidden" name="data"' in content
    assert 'type="hidden" name="branch_configs"' in content
    assert 'Receta de procesamiento' in content
    assert 'preparation_revision_id' in content
    assert 'Crear o editar una receta' in content
    assert 'Datos y particiones' in content
    assert '<textarea name="config"' not in content
    assert '<textarea name="steps"' not in content
    assert '<textarea name="data"' not in content


def test_preparation_recipe_is_saved_separately_from_model_plans(client):
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study

    dataset = Dataset.objects.create(name='Pose sessions')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'feature_names': ['nose_x', 'nose_y'], 'frame_count': 3})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)

    page = client.get(f'/studies/{study.pk}/prepare/')
    assert page.status_code == 200
    content = page.content.decode()
    assert 'Cómo preparar los datos por separado del modelo' in content
    assert 'Centrar por media del entrenamiento' in content
    assert 'Frames con referencias coincidentes' in content
    assert 'degenerate_reference_policy' in content
    assert 'name="dataset_revision_id"' in content
    assert 'id="model-config-title"' not in content

    response = client.post(f'/studies/{study.pk}/prepare/', {
        'name': 'Pose centrada', 'dataset_revision_id': str(source.pk),
        'steps': '[{"type":"center"}]',
    })

    assert response.status_code == 302, response.context['form'].errors
    recipe = Revision.objects.get(study=study, kind='preparation')
    assert recipe.payload['dataset_revision_id'] == source.pk
    assert recipe.payload['steps'][0]['type'] == 'center'
    assert not study.revision_set.filter(kind='plan').exists()


def test_prepare_analyze_and_review_explain_the_operator_workflow(client, settings, tmp_path):
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study
    from storm_studio.services import submit, perform

    settings.ARTIFACT_ROOT = tmp_path
    dataset = Dataset.objects.create(name='Pose sessions')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'feature_names': ['nose_x', 'nose_y'], 'frame_count': 3})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'connector': 'json_records', 'model': 'identity', 'metrics': ['accuracy'],
        'data': {'inputs': [1, 2, 3], 'targets': [1, 2, 3], 'train': [0, 1], 'test': [2]}})
    job = submit(plan)
    perform(str(job.pk))

    prepare = client.get(f'/studies/{study.pk}/prepare/').content.decode()
    analyze = client.get(f'/studies/{study.pk}/evidence/').content.decode()
    review = client.get(f'/studies/{study.pk}/review/').content.decode()

    assert 'La vista previa no guarda ni procesa el dataset' in prepare
    assert 'Procesar la receta crea una nueva revisión de datos' in prepare
    assert 'name="job"' in analyze
    assert str(job.pk) in analyze
    assert 'Elegí una corrida completada para inspeccionar sus resultados' in analyze
    assert 'Escribí las correcciones como un objeto JSON' in review
    assert '{"2": 0}' in review
    assert 'Aceptar crea un plan nuevo; después iniciá la ejecución desde Ejecuciones' in review


def test_prepare_page_shows_the_registered_pose_xy_and_roi_preview(client):
    from storm_studio.models import Dataset, DatasetRevision, Project, Study

    dataset = Dataset.objects.create(name='Pose sessions')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={
            'feature_names': ['nose_x', 'nose_y', 'tail_x', 'tail_y'],
            'preview': [{
                'observation_id': 'mouse-a:0', 'session_id': 'mouse-a', 'frame': 0,
                'segment': 'clip-1', 'features': [10, 20, 30, 40], 'target': None,
            }],
            'roi': [{
                'file': 'mouse-a_rois.json', 'session_id': 'mouse-a',
                'frame_shape': [800, 500],
                'rectangles': [{'name': 'arena', 'center': [100, 80],
                                'width': 40, 'height': 20}],
            }],
        })
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)

    response = client.get(f'/studies/{study.pk}/prepare/')

    assert response.status_code == 200
    assert b'id="pose-motion"' in response.content
    assert b'pose-motion-features' in response.content
    assert b'nose_x' in response.content and b'nose_y' in response.content
    assert b'pose-motion-rois' in response.content
    assert b'arena' in response.content
    assert response.context['pose_coordinate_parts'] == ['nose', 'tail']


def test_home_archives_studies_without_deleting_them(client):
    from storm_studio.models import Project, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Old study')
    archived = client.post(f'/studies/{study.pk}/archive/')

    assert archived.status_code == 302
    study.refresh_from_db()
    assert study.archived_at is not None
    home = client.get('/')
    assert b'Estudios archivados' in home.content
    assert b'Old study' in home.content
    assert b'Restaurar estudio' in home.content

    restored = client.post(f'/studies/{study.pk}/archive/')
    assert restored.status_code == 302
    study.refresh_from_db()
    assert study.archived_at is None


def test_study_cannot_be_archived_while_a_job_is_active(client):
    from storm_studio.models import Project, Revision, Study
    from storm_studio.services import submit

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Running study')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'data': {'inputs': [1], 'train': [0], 'test': []}})
    submit(plan)

    response = client.post(f'/studies/{study.pk}/archive/', follow=True)

    study.refresh_from_db()
    assert study.archived_at is None
    assert 'No se puede archivar mientras haya trabajos en curso' in response.content.decode()


def test_unused_preparation_can_be_archived_but_active_recipe_is_protected(client):
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study

    dataset = Dataset.objects.create(name='Pose sessions')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready')
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)
    recipe = Revision.objects.create(study=study, kind='preparation', payload={
        'name': 'Center pose', 'dataset_revision_id': source.pk,
        'steps': [{'type': 'center'}],
    })

    archived = client.post(f'/preparations/{recipe.pk}/archive/')

    assert archived.status_code == 302
    prepare = client.get(f'/studies/{study.pk}/prepare/')
    assert b'Recetas archivadas' in prepare.content
    assert b'Center pose' in prepare.content
    assert f'name="run_preparation_revision_id" value="{recipe.pk}"'.encode() not in prepare.content
    flow = client.get(f'/studies/{study.pk}/flow/')
    assert b'Center pose' not in flow.content

    client.post(f'/preparations/{recipe.pk}/archive/')
    Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'preparation_revision_id': recipe.pk,
        'data': {'inputs': [1], 'train': [0], 'test': []},
    })
    rejected = client.post(f'/preparations/{recipe.pk}/archive/', follow=True)

    assert 'no se puede archivar' in rejected.content.decode().lower()


def test_primary_navigation_hides_duplicate_and_advanced_sections(client):
    from storm_studio.models import Project, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    response = client.get(f'/studies/{study.pk}/flow/')
    content = response.content.decode()

    assert response.status_code == 200
    assert 'Accesos rápidos' not in content
    assert 'Vistas avanzadas' in content
    assert 'Etiquetar' not in content
    assert 'Comparar' in content
    assert 'Lineage' in content and 'Historial' in content


def test_wide_studio_uses_available_width_and_large_previews_can_be_collapsed(client):
    from storm_studio.models import Dataset, DatasetRevision, Project, Study

    dataset = Dataset.objects.create(name='Pose sessions')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'feature_names': ['nose_x', 'nose_y'], 'preview': [{
            'observation_id': 'mouse-a:0', 'session_id': 'mouse-a', 'frame': 0,
            'segment': 'clip-1', 'features': [10, 20],
        }]})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)

    response = client.get(f'/studies/{study.pk}/prepare/')
    content = response.content.decode()

    assert 'main{width:min(1680px,100%)' in content
    assert '<details class="card" id="prepare-pose-preview" open>' in content
    assert '<summary>Vista visual de pose y ROI' in content


def test_sidebar_groups_study_stages_as_an_indented_tree(client):
    from storm_studio.models import Project, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    response = client.get(f'/studies/{study.pk}/flow/')
    content = response.content.decode()

    assert '<nav aria-label="Etapas del estudio" class="stage-tree">' in content
    for group in ('Datos y preparación', 'Entrenamiento', 'Análisis y revisión', 'Reportes'):
        assert group in content
    training_group = content.split('<summary>Entrenamiento</summary>', 1)[1].split('</details>', 1)[0]
    assert 'stage-tree__items' in training_group
    assert '/studies/{}/jobs/'.format(study.pk) in training_group
    assert '.stage-tree__items{list-style:none;margin:0 0 10px 15px' in content


def test_model_plan_can_reuse_a_saved_dataset_preparation_recipe(client):
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study

    dataset = Dataset.objects.create(name='Pose sessions')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='json_records', status='ready')
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)
    recipe = Revision.objects.create(study=study, kind='preparation', payload={
        'name': 'Pose centrada', 'dataset_revision_id': source.pk,
        'steps': [{'type': 'center'}],
    })

    response = client.post(f'/studies/{study.pk}/flow/', {
        'operation': 'train', 'model': 'identity', 'connector': 'json_records',
        'dataset_revision_id': str(source.pk), 'preparation_revision_id': str(recipe.pk),
        'data': '{}', 'steps': '[]', 'config': '{}', 'branch_configs': '{}',
        'metrics': [], 'seed': 42,
    })

    assert response.status_code == 302, response.context['form'].errors
    plan = Revision.objects.get(study=study, kind='plan')
    assert plan.payload['preparation_revision_id'] == recipe.pk
    assert plan.payload['steps'] == [{'type': 'center'}]


def test_pipeline_preview_uses_a_registered_dataset_artifact(
        client, settings, tmp_path, monkeypatch):
    from storm.artifacts import FileArtifactStore
    from storm.pipeline import PipelineStep
    from storm.suite import default_catalog
    from storm_studio import services
    from storm_studio.models import Dataset, DatasetRevision, Project, Study

    class AddOne(PipelineStep):
        step_type = 'fixture.add_one'

        def process(self, context):
            context.data = [[value + 1 for value in row] for row in context.data]
            return context

    catalog = default_catalog()
    catalog.steps.register(AddOne)
    monkeypatch.setattr(services, 'catalog', lambda: catalog)

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    loaded = {
        'inputs': [[0, 10], [2, 20], [100, 1000]],
        'targets': [None, None, None],
        'evaluation_mask': [False, False, False],
        'observation_ids': ['mouse:0', 'mouse:1', 'mouse:2'],
        'frames': [0, 1, 2], 'sessions': ['mouse'] * 3,
        'segments': ['mouse:segment-0'] * 3,
        'partitions': ['train', 'train', 'test'],
        'reserved_evaluation': [False, True, False],
        'feature_names': ['nose_x', 'nose_y'],
    }
    artifact = FileArtifactStore(settings.ARTIFACT_ROOT).save(
        kind='datasets', artifact_id='fixture-pose', value=loaded)
    dataset = Dataset.objects.create(name='Pose sessions')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        artifact_ref=artifact.to_dict())
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Pose',
                                 dataset_revision=source)

    response = client.post(f'/studies/{study.pk}/pipeline-preview/', {
        'dataset_revision_id': str(source.pk),
        'steps': '[{"type":"center"},{"type":"fixture.add_one"}]',
    })

    assert response.status_code == 200, response.content
    result = response.json()
    assert result['training'][0]['observation_id'] == 'mouse:0'
    assert result['training'][0]['prepared'] == [1.0, 1.0]
    assert result['evaluation'][0]['observation_id'] == 'mouse:1'
    assert result['evaluation'][0]['prepared'] == [3.0, 11.0]
    assert result['stages'][0]['type'] == 'center'
    assert result['stages'][0]['training'][0]['value'] == [0.0, 0.0]
    assert result['stages'][0]['training'][1]['reserved_evaluation'] is True
    assert result['stages'][0]['training'][1]['value'] == [2.0, 10.0]
    assert result['stages'][0]['evaluation'][0]['observation_id'] == 'mouse:1'
    assert result['stages'][0]['evaluation'][0]['reserved_evaluation'] is True
    assert result['stages'][0]['evaluation'][0]['value'] == [2.0, 10.0]
    assert result['stages'][1]['type'] == 'fixture.add_one'
    assert result['stages'][1]['training'][0]['value'] == [1.0, 1.0]
    assert result['stages'][1]['evaluation'][0]['value'] == [3.0, 11.0]


def test_registered_pipeline_preview_transforms_only_a_bounded_sample(
        client, settings, tmp_path, monkeypatch):
    from storm.artifacts import FileArtifactStore
    from storm_studio.models import Dataset, DatasetRevision, Project, Study
    import storm.suite

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    count = 1_000
    loaded = {
        'inputs': [[float(index), float(index * 2)] for index in range(count * 2)],
        'targets': [None] * (count * 2),
        'observation_ids': [f'mouse:{index}' for index in range(count * 2)],
        'frames': list(range(count)) + list(range(count)),
        'sessions': ['train-mouse'] * count + ['test-mouse'] * count,
        'segments': ['train-0'] * count + ['test-0'] * count,
        'partitions': ['train'] * count + ['test'] * count,
        'reserved_evaluation': [False] * (count * 2),
        'feature_names': ['nose_x', 'nose_y'],
    }
    artifact = FileArtifactStore(settings.ARTIFACT_ROOT).save(
        kind='datasets', artifact_id='large-preview-fixture', value=loaded)
    dataset = Dataset.objects.create(name='Large pose sessions')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        artifact_ref=artifact.to_dict())
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Pose',
                                 dataset_revision=source)

    original_transform = storm.suite.transform_aligned
    transformed_sizes = []

    def record_transform_sizes(values, *args, **kwargs):
        transformed_sizes.append(len(values))
        return original_transform(values, *args, **kwargs)

    monkeypatch.setattr(storm.suite, 'transform_aligned', record_transform_sizes)
    response = client.post(f'/studies/{study.pk}/pipeline-preview/', {
        'dataset_revision_id': str(source.pk),
        'steps': '[{"type":"center"}]',
    })

    assert response.status_code == 200, response.content
    result = response.json()
    assert transformed_sizes == [128, 128]
    assert result['preview'] == {
        'source_observation_count': 2_000,
        'training_input_count': 128,
        'evaluation_input_count': 128,
        'limit': 256,
        'strategy': 'largest_contiguous_partition_block_v1',
        'training_output_count': 128,
        'evaluation_output_count': 128,
    }
    assert result['training'][0]['index'] >= 0
    assert result['evaluation'][0]['index'] >= count


def test_preparation_worker_materializes_a_reusable_dataset_without_a_model(
        client, settings, tmp_path, monkeypatch):
    from storm.artifacts import ArtifactRef, FileArtifactStore
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study
    from storm_studio.services import enqueue_preparation, perform

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    loaded = {
        'inputs': [[0, 10], [2, 20], [100, 1000]],
        'targets': [1, 0, None],
        'evaluation_mask': [True, True, False],
        'taxonomy': ['walk', 'grooming'],
        'observation_ids': ['mouse:0', 'mouse:1', 'mouse:2'],
        'frames': [0, 1, 2], 'sessions': ['mouse'] * 3,
        'video_frames': [10, 11, 12],
        'segments': ['mouse:segment-0'] * 3,
        'partitions': ['train', 'train', 'test'],
        'feature_names': ['nose_x', 'nose_y'],
    }
    store = FileArtifactStore(settings.ARTIFACT_ROOT)
    source_ref = store.save(kind='datasets', artifact_id='raw-mouse', value=loaded)
    dataset = Dataset.objects.create(name='Pose sessions')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='json_records', status='ready',
        inventory={'frame_count': 3, 'feature_names': loaded['feature_names']},
        artifact_ref=source_ref.to_dict())
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)
    recipe = Revision.objects.create(study=study, kind='preparation', payload={
        'name': 'Centrar pose', 'dataset_revision_id': source.pk,
        'steps': [{'type': 'center'}],
    })

    job = enqueue_preparation(study, recipe)
    assert job.operation == 'prepare'
    perform(str(job.pk))
    job.refresh_from_db()

    assert job.status == 'completed', job.error
    prepared = DatasetRevision.objects.get(dataset=dataset, connector='prepared_artifact')
    output = store.load(ArtifactRef.from_dict(prepared.artifact_ref))
    assert output['inputs'] == [[-1.0, -5.0], [1.0, 5.0], [99.0, 985.0]]
    assert output['observation_ids'] == loaded['observation_ids']
    assert output['partitions'] == loaded['partitions']
    assert output['targets'] == loaded['targets']
    assert output['taxonomy'] == loaded['taxonomy']
    assert output['video_frames'] == loaded['video_frames']
    stage = output['preparation']['stage_preview'][0]
    assert stage['type'] == 'center'
    assert stage['training_row_count'] == 2
    assert stage['evaluation_row_count'] == 1
    assert stage['training'][0] == {
        'observation_index': 0, 'observation_id': 'mouse:0',
        'session_id': 'mouse', 'frame': 0, 'segment': 'mouse:segment-0',
        'reserved_evaluation': False, 'value': [-1.0, -5.0],
    }
    assert prepared.inventory['pose_preview_store']['row_count'] == 3
    assert prepared.inventory['pose_preview_store']['sessions'][0]['row_count'] == 3
    assert prepared.inventory['taxonomy'] == loaded['taxonomy']
    assert prepared.inventory['preview'][2]['evaluation_mask'] is False
    from storm_studio.pose_preview import read_pose_preview_page
    prepared_preview = read_pose_preview_page(
        root=settings.ARTIFACT_ROOT, store_ref=prepared.inventory['pose_preview_store'],
        session_id='mouse', offset=0, limit=3)
    assert prepared_preview['taxonomy'] == loaded['taxonomy']
    assert [row['target'] for row in prepared_preview['rows']] == loaded['targets']
    assert [row['evaluation_mask'] for row in prepared_preview['rows']] == loaded['evaluation_mask']
    assert prepared.config['source_dataset_revision_id'] == source.pk
    assert prepared.config['preparation_revision_id'] == recipe.pk
    assert not study.revision_set.filter(kind='plan').exists()
    original_load = FileArtifactStore.load

    def reuse_without_reloading_source(store_instance, reference):
        if reference.artifact_id == source_ref.artifact_id:
            pytest.fail('an already registered preparation should skip raw data loading')
        return original_load(store_instance, reference)

    monkeypatch.setattr(FileArtifactStore, 'load', reuse_without_reloading_source)
    repeat_job = enqueue_preparation(study, recipe)
    perform(str(repeat_job.pk))
    repeat_job.refresh_from_db()
    assert repeat_job.status == 'completed', repeat_job.error
    assert repeat_job.result['prepared_dataset_revision_id'] == prepared.pk
    assert 'Se reutilizó la revisión procesada registrada'.encode() in client.get(
        f'/studies/{study.pk}/jobs/').content
    assert DatasetRevision.objects.filter(dataset=dataset, connector='prepared_artifact').count() == 1
    data_page = client.get(f'/studies/{study.pk}/data/').content.decode()
    assert 'Datasets procesados' in data_page
    assert f'dataset_revision_id={prepared.pk}' in data_page

    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'config': {}, 'connector': 'prepared_artifact',
        'dataset_revision_id': prepared.pk, 'data': {}, 'steps': [], 'metrics': [],
        'seed': 42,
    })
    from storm_studio.services import submit
    model_job = submit(plan)
    perform(str(model_job.pk))
    model_job.refresh_from_db()
    assert model_job.status == 'completed', model_job.error
    assert model_job.result['predictions'] == [[99.0, 985.0]]


def test_preparation_materializes_unsupervised_pose_with_null_targets(
        client, settings, tmp_path):
    from storm.artifacts import ArtifactRef, FileArtifactStore
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study
    from storm_studio.services import enqueue_preparation, perform

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    loaded = {
        'inputs': [[0.0, 10.0], [2.0, 20.0], [100.0, 1000.0]],
        'targets': None,
        'observation_ids': ['mouse:0', 'mouse:1', 'mouse:2'],
        'frames': [0, 1, 2],
        'sessions': ['mouse'] * 3,
        'segments': ['mouse:segment-0'] * 3,
        'partitions': ['train', 'train', 'test'],
        'reserved_evaluation': [False, False, False],
        'feature_names': ['nose_x', 'nose_y'],
    }
    store = FileArtifactStore(settings.ARTIFACT_ROOT)
    source_ref = store.save(kind='datasets', artifact_id='unlabeled-pose', value=loaded)
    dataset = Dataset.objects.create(name='Unsupervised pose')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'frame_count': 3, 'feature_names': loaded['feature_names']},
        artifact_ref=source_ref.to_dict())
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)
    recipe = Revision.objects.create(study=study, kind='preparation', payload={
        'name': 'Centrar pose no supervisada', 'dataset_revision_id': source.pk,
        'steps': [{'type': 'center'}],
    })

    job = enqueue_preparation(study, recipe)
    perform(str(job.pk))
    job.refresh_from_db()

    assert job.status == 'completed', job.error
    prepared_revision = DatasetRevision.objects.get(
        dataset=dataset, connector='prepared_artifact')
    prepared = store.load(ArtifactRef.from_dict(prepared_revision.artifact_ref))
    assert prepared['targets'] is None
    assert prepared['inputs'] == [[-1.0, -5.0], [1.0, 5.0], [99.0, 985.0]]


def test_preparation_fits_and_transforms_training_in_one_pass(
        client, settings, tmp_path, monkeypatch):
    from storm.artifacts import FileArtifactStore
    from storm_studio import data_preparation
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study
    from storm_studio.services import enqueue_preparation, perform

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    loaded = {
        'inputs': [[0.0, 10.0], [2.0, 20.0], [100.0, 1000.0]],
        'targets': None,
        'frames': [0, 1, 2], 'sessions': ['mouse'] * 3,
        'segments': ['mouse:segment-0'] * 3,
        'partitions': ['train', 'train', 'test'],
        'reserved_evaluation': [False, True, False],
        'feature_names': ['nose_x', 'nose_y'],
    }
    store = FileArtifactStore(settings.ARTIFACT_ROOT)
    source_ref = store.save(kind='datasets', artifact_id='one-pass-pose', value=loaded)
    dataset = Dataset.objects.create(name='One-pass pose')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        artifact_ref=source_ref.to_dict())
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)
    recipe = Revision.objects.create(study=study, kind='preparation', payload={
        'name': 'Centrar una vez', 'dataset_revision_id': source.pk,
        'steps': [{'type': 'center'}],
    })

    original = data_preparation.transform_aligned
    calls = []

    def record(values, indices, steps, *args, **kwargs):
        calls.append((len(values), kwargs.get('learned') is not None,
                      kwargs.get('fit_observation_indices')))
        return original(values, indices, steps, *args, **kwargs)

    monkeypatch.setattr(data_preparation, 'transform_aligned', record)
    job = enqueue_preparation(study, recipe)
    perform(str(job.pk))
    job.refresh_from_db()

    assert job.status == 'completed', job.error
    assert calls == [(2, False, [0]), (2, True, None)]


def test_large_execution_results_keep_resolved_data_in_the_artifact_store(
        settings, tmp_path, monkeypatch):
    from storm.artifacts import ArtifactRef
    from storm_studio.models import Job, Project, Revision, Study
    from storm_studio import services

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    monkeypatch.setattr(
        services, 'MAX_INLINE_RESOLVED_DATA_OBSERVATIONS', 2, raising=False)
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Large results')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'operation': 'train', 'model': 'identity', 'connector': 'numeric_json',
        'config': {}, 'steps': [], 'metrics': [], 'seed': 42,
        'data': {'inputs': [1, 2, 3], 'targets': [1, 2, 3],
                 'train': [0, 1], 'test': [2]},
    })
    job = Job.objects.create(revision=plan, operation='train')

    services.perform(str(job.pk))
    job.refresh_from_db()

    assert job.status == 'completed'
    assert 'resolved_data' not in job.result
    assert job.result['resolved_data_summary']['observation_count'] == 3
    restored = services.load_execution_result(job)
    assert restored['resolved_data']['inputs'] == [1, 2, 3]
    assert restored['resolved_data']['targets'] == [1, 2, 3]
    assert ArtifactRef.from_dict(job.result['result_artifact_ref']).kind == 'results'


def test_pose_preparation_names_resolve_after_coordinate_selection():
    from storm_studio.data_preparation import resolve_preparation_steps

    steps = [
        {'type': 'pose.select_coordinates',
         'config': {'names': ['nose_x', 'nose_y']}},
        {'type': 'pose.recenter',
         'config': {'center_bodypart': 'nose', 'bodyparts': ['nose']}},
        {'type': 'pose.likelihood_filter',
         'config': {'threshold': 0.7, 'bodyparts': ['nose']}},
    ]

    resolved = resolve_preparation_steps(
        steps, ['body_x', 'body_y', 'nose_x', 'nose_y'])

    assert resolved[1]['config'] == {
        'center_indices': [0, 1], 'coordinate_pairs': [[0, 1]]}
    assert resolved[2]['config'] == {
        'threshold': 0.7, 'coordinate_pairs': [[0, 1]]}


def test_pose_orientation_names_resolve_after_coordinate_selection():
    from storm_studio.data_preparation import resolve_preparation_steps

    resolved = resolve_preparation_steps(
        [
            {'type': 'pose.select_coordinates', 'config': {'names': [
                'body_x', 'body_y', 'nose_x', 'nose_y']}},
            {'type': 'pose.orient_coordinates', 'config': {
                'from_bodypart': 'body', 'toward_bodypart': 'nose',
                'degenerate_reference_policy': 'identity'}},
        ],
        ['nose_x', 'nose_y', 'tail_x', 'tail_y', 'body_x', 'body_y'],
    )

    assert resolved[1]['config'] == {
        'reference_pairs': [[0, 1], [2, 3]],
        'coordinate_pairs': [[0, 1], [2, 3]],
        'target_angle_degrees': 45,
        'degenerate_reference_policy': 'identity',
    }


def test_pose_preparation_keeps_selected_acceleration_device():
    from storm_studio.data_preparation import resolve_preparation_steps

    resolved = resolve_preparation_steps(
        [
            {'type': 'pose.recenter', 'config': {
                'center_bodypart': 'body', 'bodyparts': ['nose', 'body'],
                'device': 'cuda'}},
            {'type': 'pose.orient_coordinates', 'config': {
                'from_bodypart': 'body', 'toward_bodypart': 'nose',
                'device': 'cpu'}},
        ],
        ['body_x', 'body_y', 'nose_x', 'nose_y'],
    )

    assert resolved[0]['config']['device'] == 'cuda'
    assert resolved[1]['config']['device'] == 'cpu'


def test_preparation_fits_center_without_reserved_training_observations(
        settings, tmp_path):
    from storm.artifacts import ArtifactRef, FileArtifactStore
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study
    from storm_studio.services import enqueue_preparation, perform

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    loaded = {
        'inputs': [[0.0], [100.0], [10.0], [50.0]],
        'observation_ids': ['a:0', 'a:1', 'a:2', 'b:0'],
        'frames': [0, 1, 2, 0], 'sessions': ['a', 'a', 'a', 'b'],
        'segments': ['a:0', 'a:0', 'a:0', 'b:0'],
        'partitions': ['train', 'train', 'train', 'test'],
        'reserved_evaluation': [False, True, False, False],
        'feature_names': ['nose_x'],
    }
    store = FileArtifactStore(settings.ARTIFACT_ROOT)
    artifact = store.save(kind='datasets', artifact_id='reserved-pose', value=loaded)
    dataset = Dataset.objects.create(name='Reserved pose')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='json_records', status='ready',
        artifact_ref=artifact.to_dict(), inventory={'feature_names': ['nose_x']})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)
    recipe = Revision.objects.create(study=study, kind='preparation', payload={
        'name': 'Centrar sin reserva', 'dataset_revision_id': source.pk,
        'steps': [{'type': 'center'}],
    })

    job = enqueue_preparation(study, recipe)
    perform(str(job.pk))
    prepared = DatasetRevision.objects.get(dataset=dataset, connector='prepared_artifact')
    output = store.load(ArtifactRef.from_dict(prepared.artifact_ref))

    assert output['inputs'] == [[-5.0], [95.0], [5.0], [45.0]]
    assert output['train'] == [0, 2]
    assert output['reserved_evaluation'] == [False, True, False, False]


def test_pipeline_branches_submit_with_the_same_preparation(client):
    from storm_studio.models import Project, Study, Revision, Job

    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'config': {}, 'branch_models': ['constant'],
        'branch_configs': {'constant': {'value': 9}},
        'steps': [{'type': 'scale', 'factor': 3}],
        'data': {'inputs': [1, 2], 'targets': [1, 2], 'train': [0], 'test': [1]},
    })

    response = client.post(f'/plans/{plan.pk}/run/', {'run_branches': '1'})

    assert response.status_code == 302
    duplicate = client.post(f'/plans/{plan.pk}/run/', {'run_branches': '1'})
    assert duplicate.status_code == 302
    jobs = list(Job.objects.select_related('revision').order_by('created'))
    assert [job.revision.payload['model'] for job in jobs] == ['identity', 'constant']
    assert all(job.revision.payload['steps'] == [{'type': 'scale', 'factor': 3}] for job in jobs)
    assert jobs[1].revision.payload['config'] == {'value': 9}


def test_pipeline_preview_fits_steps_on_train_and_keeps_source_indices(client):
    from storm_studio.models import Project, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Preview')
    response = client.post(f'/studies/{study.pk}/pipeline-preview/', {
        'connector': 'numeric_json',
        'data': '{"inputs":[1,3,100],"train":[0,1],"test":[2]}',
        'steps': '[{"type":"center"}]',
    })

    assert response.status_code == 200
    result = response.json()
    assert result['fit_partition'] == 'train'
    assert result['training'] == [
        {'index': 0, 'observation_id': '0', 'input': 1, 'prepared': -1.0},
        {'index': 1, 'observation_id': '1', 'input': 3, 'prepared': 1.0},
    ]
    assert result['evaluation'] == [
        {'index': 2, 'observation_id': '2', 'input': 100, 'prepared': 98.0}]


def test_pipeline_preview_centers_pose_vectors_using_training_only(client):
    from storm_studio.models import Project, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Pose preview')
    response = client.post(f'/studies/{study.pk}/pipeline-preview/', {
        'connector': 'json_records',
        'data': '{"inputs":[[0,10],[2,20],[100,1000]],"train":[0,1],"test":[2]}',
        'steps': '[{"type":"center"}]',
    })

    assert response.status_code == 200, response.content
    result = response.json()
    assert [row['prepared'] for row in result['training']] == [[-1.0, -5.0], [1.0, 5.0]]
    assert result['evaluation'][0]['prepared'] == [99.0, 985.0]


def test_dataset_view_previews_fields_partitions_and_fingerprint(client):
    from storm_studio.models import Project, Study, Revision

    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    Revision.objects.create(study=study, kind='plan', payload={
        'connector': 'json_records', 'model': 'identity', 'data': {
            'inputs': [{'speed': 1}, {'speed': None}], 'targets': ['walk', 'run'],
            'train': [0], 'test': [1]}})

    response = client.get(f'/studies/{study.pk}/data/')

    assert response.status_code == 200
    assert b'Vista previa de datos' in response.content
    assert b'speed' in response.content and b'2 filas' in response.content
    assert b'1 faltante' in response.content
    assert b'Fingerprint' in response.content


def test_registered_video_preview_is_scoped_to_the_active_dataset_and_supports_ranges(
        client, settings, tmp_path):
    from storm_studio.models import Dataset, DatasetAsset, DatasetRevision, Project, Study

    settings.WORKSPACE = tmp_path
    dataset = Dataset.objects.create(name='Video dataset')
    video_path = tmp_path / 'data_sources' / 'video.mp4'
    video_path.parent.mkdir()
    video_path.write_bytes(b'0123456789')
    video = DatasetAsset.objects.create(
        dataset=dataset, role='video', original_name='mouse-a.mp4',
        relative_path='data_sources/video.mp4', sha256='a' * 64, size_bytes=10,
        session_id='mouse-a')
    revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', asset_ids=[video.pk],
        status='ready', inventory={})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Video',
                                 dataset_revision=revision)

    response = client.get(f'/studies/{study.pk}/assets/{video.pk}/video/',
                          HTTP_RANGE='bytes=2-5')

    assert response.status_code == 206
    assert response['Content-Range'] == 'bytes 2-5/10'
    assert response['Accept-Ranges'] == 'bytes'
    assert b''.join(response.streaming_content) == b'2345'
    invalid_range = client.get(
        f'/studies/{study.pk}/assets/{video.pk}/video/', HTTP_RANGE='bytes=20-30')
    assert invalid_range.status_code == 416
    assert invalid_range['Content-Range'] == 'bytes */10'

    DatasetRevision.objects.filter(pk=revision.pk).update(inventory={
        'feature_names': ['nose_x', 'nose_y'],
        'preview': [{'session_id': 'mouse-a', 'frame': 2, 'segment': 'clip-1',
                     'features': [10.0, 20.0]}],
    })
    data_page = client.get(f'/studies/{study.pk}/data/')
    assert b'pose-video-data' in data_page.content
    assert f'/studies/{study.pk}/assets/{video.pk}/video/'.encode() in data_page.content
    assert b'id="pose-video-calibration"' in data_page.content
    assert f'"asset_id": {video.pk}'.encode() in data_page.content
    assert f'id="id_asset_video_frame_offset_{video.pk}"'.encode() in data_page.content

    other_dataset = Dataset.objects.create(name='Other')
    other_video = DatasetAsset.objects.create(
        dataset=other_dataset, role='video', original_name='other.mp4',
        relative_path='data_sources/video.mp4', sha256='b' * 64, size_bytes=10)
    denied = client.get(f'/studies/{study.pk}/assets/{other_video.pk}/video/')
    assert denied.status_code == 404


def test_avi_preview_is_prepared_by_worker_and_served_as_range_readable_mp4(
        client, settings, tmp_path, monkeypatch):
    import hashlib
    from pathlib import Path
    from storm_studio.models import Dataset, DatasetAsset, DatasetRevision, Job, Project, Study
    from storm_studio.services import perform

    settings.WORKSPACE = tmp_path
    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    source_path = tmp_path / 'data_sources' / 'mouse.avi'
    source_path.parent.mkdir()
    source_path.write_bytes(b'original-avi')
    dataset = Dataset.objects.create(name='AVI preview')
    video = DatasetAsset.objects.create(
        dataset=dataset, role='video', original_name='mouse.avi',
        relative_path='data_sources/mouse.avi', sha256='a' * 64, size_bytes=12,
        session_id='mouse')
    revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', asset_ids=[video.pk],
        status='ready', inventory={
            'assets': 1,
            'feature_names': ['nose_x', 'nose_y'],
            'preview': [{'session_id': 'mouse', 'frame': 0, 'segment': 'clip-1',
                         'features': [1.0, 2.0]}],
        })
    study = Study.objects.create(project=Project.objects.create(name='P'),
                                 name='AVI preview', dataset_revision=revision)

    queued = client.post(f'/studies/{study.pk}/assets/{video.pk}/video/prepare/')

    assert queued.status_code == 302
    job = Job.objects.get(operation='video_preview')
    assert job.status == 'pending'
    duplicate = client.post(f'/studies/{study.pk}/assets/{video.pk}/video/prepare/')
    assert duplicate.status_code == 302
    assert Job.objects.filter(operation='video_preview').count() == 1

    Job.objects.filter(pk=job.pk).update(status='interrupted')
    cache_key = hashlib.sha256(f'{video.pk}:{video.sha256}'.encode('utf-8')).hexdigest()
    partial_path = tmp_path / 'video_previews' / f'.{cache_key}.partial.mp4'
    partial_path.parent.mkdir()
    partial_path.write_bytes(b'incomplete-after-worker-stop')
    retry = client.post(f'/studies/{study.pk}/assets/{video.pk}/video/prepare/')
    assert retry.status_code == 302
    retried_job = Job.objects.exclude(pk=job.pk).get(operation='video_preview')
    assert retried_job.status == 'pending'
    assert retried_job.previous_id == job.pk

    def fake_ffmpeg(command, **_kwargs):
        Path(command[-1]).write_bytes(b'converted-mp4')

    monkeypatch.setattr('storm_studio.video_previews.subprocess.run', fake_ffmpeg)
    perform(str(retried_job.pk))
    retried_job.refresh_from_db()
    assert retried_job.status == 'completed', retried_job.error
    assert not partial_path.exists()
    assert source_path.read_bytes() == b'original-avi'

    response = client.get(f'/studies/{study.pk}/assets/{video.pk}/video/',
                          HTTP_RANGE='bytes=2-8')

    assert response.status_code == 206
    assert response['Content-Type'].startswith('video/mp4')
    assert response['Content-Range'] == 'bytes 2-8/13'
    assert b''.join(response.streaming_content) == b'nverted'
    data_page = client.get(f'/studies/{study.pk}/data/')
    assert b'Vista compatible lista' in data_page.content


def test_video_preview_encodes_h264_and_preserves_source_frames(settings, tmp_path):
    import hashlib
    import json
    import shutil
    import subprocess
    from storm_studio.models import Dataset, DatasetAsset, DatasetRevision
    from storm_studio.video_previews import materialize_video_preview

    ffmpeg = shutil.which('ffmpeg')
    ffprobe = shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        pytest.skip('ffmpeg and ffprobe are needed for this integration check')
    settings.WORKSPACE = tmp_path
    source_path = tmp_path / 'data_sources' / 'sample.avi'
    source_path.parent.mkdir()
    subprocess.run([
        ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'color=c=red:s=64x48:r=10:d=0.6',
        '-frames:v', '6', '-c:v', 'mpeg4', str(source_path),
    ], check=True, capture_output=True)
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    dataset = Dataset.objects.create(name='Encoded preview')
    video = DatasetAsset.objects.create(
        dataset=dataset, role='video', original_name='sample.avi',
        relative_path='data_sources/sample.avi', sha256=source_hash,
        size_bytes=source_path.stat().st_size, session_id='sample')
    revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', asset_ids=[video.pk],
        status='ready', inventory={})

    result = materialize_video_preview(revision.pk, video.pk, tmp_path)
    probe = json.loads(subprocess.run([
        ffprobe, '-v', 'error', '-count_frames', '-show_streams', '-of', 'json',
        str(tmp_path / result['relative_path']),
    ], check=True, capture_output=True, text=True).stdout)
    stream = probe['streams'][0]

    assert result['video_codec'] == 'h264'
    assert stream['codec_name'] == 'h264'
    assert stream['pix_fmt'] == 'yuv420p'
    assert stream['nb_read_frames'] == '6'
    assert source_path.exists()


def test_compare_uses_explicitly_selected_executions(client, settings, tmp_path):
    from storm_studio.models import Project, Study, Revision
    from storm_studio.services import submit, perform

    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    jobs = []
    for model in ('identity', 'mean_regressor', 'identity'):
        revision = Revision.objects.create(study=study, kind='plan', payload={
            'model': model, 'data': {'inputs': [1, 2], 'targets': [1, 2], 'train': [0], 'test': [1]}})
        job = submit(revision)
        perform(str(job.pk))
        jobs.append(job)

    response = client.get(f'/studies/{study.pk}/compare/?jobs={jobs[0].pk},{jobs[2].pk}')

    assert response.status_code == 200
    comparison_form = response.content.decode()
    assert '<select id="comparison-jobs" name="jobs" multiple' in comparison_form
    assert f'<option value="{jobs[0].pk}" selected' in comparison_form
    assert f'<option value="{jobs[2].pk}" selected' in comparison_form
    assert b'Ejecuciones seleccionadas' in response.content
    assert 'Comparación gráfica de métricas'.encode() in response.content
    assert str(jobs[0].pk).encode() in response.content
    assert str(jobs[2].pk).encode() in response.content
    comparison_panel = response.content.decode().split('Ejecuciones seleccionadas', 1)[1].split('</section>', 1)[0]
    assert str(jobs[1].pk) not in comparison_panel


def test_lineage_view_links_revisions_and_execution(client, settings, tmp_path):
    from storm_studio.models import Project, Study, Revision
    from storm_studio.services import submit, perform

    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'data': {'inputs': [1, 2], 'targets': [1, 2], 'train': [0], 'test': [1]}})
    job = submit(plan)
    perform(str(job.pk))

    response = client.get(f'/studies/{study.pk}/lineage/')

    assert response.status_code == 200
    assert b'Trazabilidad del estudio' in response.content
    assert b'Dataset / plan' in response.content
    assert str(job.pk).encode() in response.content


def test_extension_wizard_downloads_unenabled_package_with_contract_test(client):
    from zipfile import ZipFile
    from io import BytesIO

    response = client.get('/scaffold/?kind=metric&name=balanced_accuracy')

    assert response.status_code == 200
    assert response['Content-Type'] == 'application/zip'
    with ZipFile(BytesIO(response.content)) as package:
        names = package.namelist()
        assert any(name.endswith('descriptor.json') for name in names)
        assert any(name.endswith('test_conformance.py') for name in names)
        assert any(name.endswith('README.md') for name in names)
        descriptor = next(n for n in names if n.endswith('descriptor.json'))
        assert b'pending_implementation' in package.read(descriptor)


def test_evidence_marks_categorical_output_for_lane_visualization(client, settings, tmp_path):
    from storm_studio.models import Project, Study, Revision
    from storm_studio.services import submit, perform

    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'connector': 'json_records', 'model': 'identity', 'metrics': ['accuracy'],
        'data': {'inputs': ['nose', 'tail'], 'targets': ['still', 'move'], 'train': [0], 'test': [1]}})
    job = submit(plan)
    perform(str(job.pk))

    response = client.get(f'/studies/{study.pk}/evidence/?job={job.pk}')

    assert response.status_code == 200
    assert b'data-visual-kind="categorical"' in response.content
    assert b'Carriles categ' in response.content


def test_protected_benchmark_evidence_is_labeled_as_inference_only(client):
    from storm_studio.models import Project, Study, Revision, Job

    study = Study.objects.create(project=Project.objects.create(name='P'), name='NOR test')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'operation': 'infer', 'model': 'vame_native', 'data': {},
    })
    job = Job.objects.create(revision=plan, status='completed', operation='infer', result={
        'model': 'vame_native', 'partition': 'test', 'indices': [0],
        'predictions': [2], 'prediction_mask': [True], 'metrics': {'ari': 0.95},
        'metric_indices': [0], 'metric_definitions': [{
            'name': 'ari', 'version': '1', 'direction': 'maximize',
        }],
        'metric_reason': 'No hay métricas compatibles para mostrar.',
        'spec': {'model': 'vame_native'}, 'output_metadata': {
            'task': 'clustering', 'semantics': 'group identifiers',
        },
        'resolved_data': {
            'inputs': [[1.0, 2.0]], 'targets': ['Known'], 'train': [],
            'inference_only': True,
        },
    })

    response = client.get(f'/studies/{study.pk}/evidence/?job={job.pk}')

    assert response.status_code == 200
    assert 'Evaluación protegida por inferencia'.encode() in response.content
    assert 'Crear lote de revisión'.encode() not in response.content
    assert 'El lote usa observaciones de entrenamiento'.encode() not in response.content
    assert 'Si querés pedir una revisión humana'.encode() not in response.content
    assert 'Métricas sobre etiquetas válidas'.encode() in response.content
    assert '0,9500'.encode() in response.content


def test_adoption_requires_and_records_a_human_justification(client, settings, tmp_path):
    from storm_studio.models import Project, Study, Revision, Job
    from storm_studio.services import submit, perform

    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    revision = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'data': {'inputs': [1, 2], 'targets': [1, 2], 'train': [0], 'test': [1]}})
    job = submit(revision)
    perform(str(job.pk))

    rejected = client.post(f'/jobs/{job.pk}/adopt/', {'reason': '  '})
    assert rejected.status_code == 400
    accepted = client.post(f'/jobs/{job.pk}/adopt/', {'reason': 'Validación manual de evidencia'})

    assert accepted.status_code == 302
    decision = Revision.objects.get(kind='adoption')
    assert decision.payload['reason'] == 'Validación manual de evidencia'
    study.refresh_from_db()
    assert study.adopted_id == job.pk


def test_browser_roundtrip_and_review(client, settings, tmp_path):
    from storm_studio.models import Project, Study, Job, Revision
    from storm_studio.services import submit, perform, propose, review
    settings.ARTIFACT_ROOT = tmp_path
    project = Project.objects.create(name='Research')
    study = Study.objects.create(project=project, name='Example')
    spec = {'model': 'mean_regressor', 'config': {}, 'data': {
        'inputs': [0, 1, 2], 'targets': [2, 4, 100], 'train': [0, 1], 'test': [2]}}
    revision = Revision.objects.create(study=study, kind='plan', payload=spec)
    job = submit(revision)
    perform(str(job.pk))
    job.refresh_from_db()
    assert job.status == 'completed'
    assert job.result['predictions'] == [3.0]
    for page in ['data', 'components', 'flow', 'models', 'jobs', 'evidence', 'review', 'compare', 'reports', 'history']:
        assert client.get(f'/studies/{study.pk}/{page}/').status_code == 200
    response = client.post(f'/jobs/{job.pk}/infer/', {'inputs': '[8, 9]'})
    assert response.status_code == 200
    assert b'3.0' in response.content
    task = propose(job, count=1, seed=42)
    assert 2 not in task.payload['indices']
    changed = review(task, {str(task.payload['indices'][0]): 6}, [])
    revision.refresh_from_db()
    assert revision.payload == spec
    assert changed.pk != revision.pk
    assert Job.objects.count() == 1  # review does not authorize training


@pytest.mark.django_db
@pytest.mark.parametrize(
    'mutation', ['update', 'bulk_update', 'delete_queryset', 'delete_instance'])
def test_revision_history_cannot_be_changed_through_orm_paths(mutation):
    from storm_studio.models import Project, Revision, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Immutable')
    revision = Revision.objects.create(study=study, kind='annotations', payload={'label': 'walk'})
    if mutation == 'update':
        write = lambda: Revision.objects.filter(pk=revision.pk).update(
            payload={'label': 'grooming'})
    elif mutation == 'bulk_update':
        revision.payload = {'label': 'grooming'}
        write = lambda: Revision.objects.bulk_update([revision], ['payload'])
    elif mutation == 'delete_queryset':
        write = lambda: Revision.objects.filter(pk=revision.pk).delete()
    else:
        write = revision.delete

    with pytest.raises(ValueError, match='immutable'):
        write()

    revision.refresh_from_db()
    assert revision.payload == {'label': 'walk'}


@pytest.mark.django_db
def test_dataset_label_correction_creates_an_immutable_successor_layer(
        settings, tmp_path):
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study
    from storm_studio.pose_preview import write_pose_preview_store
    from storm_studio.services import correct_dataset_label

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    data = {
        'inputs': [[0.0, 1.0], [2.0, 3.0], [4.0, 5.0]],
        'feature_names': ['nose_x', 'nose_y'],
        'targets': [0, None, 1],
        'evaluation_mask': [True, False, True],
        'taxonomy': ['walk', 'grooming'],
        'frames': [0, 1, 2], 'video_frames': [10, 11, 12],
        'sessions': ['mouse'] * 3, 'segments': ['clip'] * 3,
        'observation_ids': ['mouse:0', 'mouse:1', 'mouse:2'],
    }
    store = write_pose_preview_store(
        data, root=settings.ARTIFACT_ROOT, artifact_id='label-correction-pose')
    dataset = Dataset.objects.create(name='Label correction dataset')
    dataset_revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'source_fingerprint': 'fingerprint-v1', 'taxonomy': data['taxonomy'],
                   'pose_preview_store': store})
    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Label corrections',
        dataset_revision=dataset_revision)

    first = correct_dataset_label(
        study, dataset_revision, session_id='mouse', observation_id='mouse:1', frame=1,
        label='grooming', author='Investigator', reason='Pose review confirms grooming.')
    second = correct_dataset_label(
        study, dataset_revision, session_id='mouse', observation_id='mouse:1', frame=1,
        label='walk', author='Investigator', reason='Second review corrects the first decision.')

    assert first.kind == second.kind == 'label_corrections'
    assert second.parent_id == first.pk
    assert first.payload['source_fingerprint'] == 'fingerprint-v1'
    assert first.payload['corrections']['mouse:1'] == {
        'session_id': 'mouse', 'observation_id': 'mouse:1', 'pose_frame': 1,
        'video_frame': 11, 'original_target': None, 'original_label_valid': False,
        'label': 'grooming', 'author': 'Investigator',
        'reason': 'Pose review confirms grooming.',
    }
    assert second.payload['corrections']['mouse:1']['label'] == 'walk'
    assert first.payload['corrections']['mouse:1']['label'] == 'grooming'
    assert DatasetRevision.objects.get(pk=dataset_revision.pk).inventory['taxonomy'] == data['taxonomy']
    assert Revision.objects.filter(study=study, kind='label_corrections').count() == 2
    with pytest.raises(ValueError, match='taxonomy'):
        correct_dataset_label(
            study, dataset_revision, session_id='mouse', observation_id='mouse:1', frame=1,
            label='unknown', author='Investigator', reason='Invalid class.')
    with pytest.raises(ValueError, match='does not match'):
        correct_dataset_label(
            study, dataset_revision, session_id='mouse', observation_id='mouse:2', frame=1,
            label='walk', author='Investigator', reason='Wrong source frame.')
    assert Revision.objects.filter(study=study, kind='label_corrections').count() == 2


@pytest.mark.django_db
def test_label_correction_plan_applies_only_to_unreserved_training_rows():
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study
    from storm_studio.services import apply_label_corrections

    dataset = Dataset.objects.create(name='Corrected training data')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'source_fingerprint': 'fingerprint-v1',
                   'taxonomy': ['walk', 'grooming']})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    correction_revision = Revision.objects.create(
        study=study, kind='label_corrections', payload={
            'dataset_revision_id': source.pk,
            'source_fingerprint': 'fingerprint-v1',
            'taxonomy': ['walk', 'grooming'],
            'corrections': {
                'mouse:0': {'session_id': 'mouse', 'observation_id': 'mouse:0',
                            'pose_frame': 0, 'label': 'grooming'},
                'mouse:1': {'session_id': 'mouse', 'observation_id': 'mouse:1',
                            'pose_frame': 1, 'label': 'grooming'},
                'mouse:2': {'session_id': 'mouse', 'observation_id': 'mouse:2',
                            'pose_frame': 2, 'label': 'grooming'},
                'mouse:3': {'session_id': 'mouse', 'observation_id': 'mouse:3',
                            'pose_frame': 3, 'label': 'grooming'},
            },
        })
    data = {
        'inputs': [[0], [1], [2], [3]],
        'targets': [0, 0, None, None],
        'evaluation_mask': [True, True, False, False],
        'observation_ids': ['mouse:0', 'mouse:1', 'mouse:2', 'mouse:3'],
        'sessions': ['mouse'] * 4,
        'frames': [0, 1, 2, 3],
        'partitions': ['train', 'test', 'train', 'train'],
        'reserved_evaluation': [False, False, False, True],
    }

    applied = apply_label_corrections(
        data, correction_revision, dataset_revision=source, study=study)

    assert applied['data']['targets'] == [1, 0, 1, None]
    assert applied['data']['evaluation_mask'] == [True, True, True, False]
    assert applied['applied_observations'] == ['mouse:0', 'mouse:2']
    assert applied['excluded_observations'] == {
        'mouse:1': 'not_training_partition',
        'mouse:3': 'reserved_evaluation',
    }
    assert data['targets'] == [0, 0, None, None]


@pytest.mark.django_db
def test_flow_plan_explicitly_records_selected_label_correction_revision(client):
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study

    dataset = Dataset.objects.create(name='Pose sessions')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='json_records', status='ready',
        inventory={'source_fingerprint': 'fingerprint-v1',
                   'taxonomy': ['walk', 'grooming']})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)
    correction = Revision.objects.create(study=study, kind='label_corrections', payload={
        'dataset_revision_id': source.pk,
        'source_fingerprint': 'fingerprint-v1',
        'taxonomy': ['walk', 'grooming'],
        'corrections': {'mouse:0': {'label': 'grooming'}},
    })

    page = client.get(f'/studies/{study.pk}/flow/')
    assert f'value="{correction.pk}"' in page.content.decode()
    response = client.post(f'/studies/{study.pk}/flow/', {
        'operation': 'train', 'model': 'identity', 'connector': 'json_records',
        'dataset_revision_id': str(source.pk),
        'label_correction_revision_id': str(correction.pk),
        'data': '{}', 'steps': '[]', 'config': '{}', 'branch_configs': '{}',
        'metrics': [], 'seed': 42,
    })

    assert response.status_code == 302, response.context['form'].errors
    plan = Revision.objects.get(study=study, kind='plan')
    assert plan.payload['label_correction_revision_id'] == correction.pk

    without_source = client.post(f'/studies/{study.pk}/flow/', {
        'operation': 'train', 'model': 'identity', 'connector': 'numeric_json',
        'label_correction_revision_id': str(correction.pk),
        'data': '{"inputs":[0,1],"targets":[0,1],"train":[0],"test":[1]}',
        'steps': '[]', 'config': '{}', 'branch_configs': '{}', 'metrics': [], 'seed': 42,
    })
    assert without_source.status_code == 200
    assert 'fuente registrada' in str(without_source.context['form'].errors).lower()
    assert Revision.objects.filter(study=study, kind='plan').count() == 1


@pytest.mark.django_db
def test_training_worker_records_applied_label_correction_provenance(
        monkeypatch, settings, tmp_path):
    from storm.artifacts import FileArtifactStore
    from storm_studio.models import (
        Dataset, DatasetRevision, Job, Project, Revision, Study,
    )
    from storm_studio import services

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    settings.WORKSPACE = tmp_path
    data = {
        'inputs': [[0], [1], [2], [3]],
        'targets': [0, 0, None, None],
        'evaluation_mask': [True, True, False, False],
        'observation_ids': ['mouse:0', 'mouse:1', 'mouse:2', 'mouse:3'],
        'sessions': ['mouse'] * 4,
        'frames': [0, 1, 2, 3],
        'partitions': ['train', 'test', 'train', 'train'],
        'reserved_evaluation': [False, False, False, True],
        'feature_names': ['nose_x'],
        'taxonomy': ['walk', 'grooming'],
    }
    artifact_ref = FileArtifactStore(settings.ARTIFACT_ROOT).save(
        kind='datasets', artifact_id='correction-worker-source', value=data)
    dataset = Dataset.objects.create(name='Training source')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='prepared_artifact', status='ready',
        inventory={'source_fingerprint': 'fingerprint-v1',
                   'taxonomy': data['taxonomy']}, artifact_ref=artifact_ref.to_dict())
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)
    correction = Revision.objects.create(study=study, kind='label_corrections', payload={
        'dataset_revision_id': source.pk,
        'source_fingerprint': 'fingerprint-v1',
        'taxonomy': data['taxonomy'],
        'corrections': {
            'mouse:0': {'session_id': 'mouse', 'observation_id': 'mouse:0',
                        'pose_frame': 0, 'label': 'grooming'},
            'mouse:1': {'session_id': 'mouse', 'observation_id': 'mouse:1',
                        'pose_frame': 1, 'label': 'grooming'},
            'mouse:2': {'session_id': 'mouse', 'observation_id': 'mouse:2',
                        'pose_frame': 2, 'label': 'grooming'},
            'mouse:3': {'session_id': 'mouse', 'observation_id': 'mouse:3',
                        'pose_frame': 3, 'label': 'grooming'},
        },
    })
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'operation': 'train', 'model': 'identity', 'dataset_revision_id': source.pk,
        'label_correction_revision_id': correction.pk, 'data': {}, 'steps': [],
        'config': {}, 'metrics': [], 'seed': 42,
    })
    received = {}

    def fake_execute(spec, *args, **kwargs):
        received['targets'] = spec['data']['targets']
        received['evaluation_mask'] = spec['data']['evaluation_mask']
        return {'spec': spec}

    monkeypatch.setattr(services, 'execute', fake_execute)
    job = Job.objects.create(revision=plan, operation='train')

    services.perform(str(job.pk))

    job.refresh_from_db()
    assert job.status == 'completed', job.error
    assert received['targets'] == [1, 0, 1, None]
    assert received['evaluation_mask'] == [True, True, True, False]
    assert job.result['label_corrections'] == {
        'revision_id': correction.pk,
        'source_dataset_revision_id': source.pk,
        'source_fingerprint': 'fingerprint-v1',
        'applied_observations': ['mouse:0', 'mouse:2'],
        'excluded_observations': {
            'mouse:1': 'not_training_partition',
            'mouse:3': 'reserved_evaluation',
        },
    }


def test_csrf_and_failed_execution(client, settings, tmp_path):
    from django.test import Client
    from storm_studio.models import Project, Study, Revision
    from storm_studio.services import submit, perform
    settings.ARTIFACT_ROOT = tmp_path
    assert Client(enforce_csrf_checks=True).post('/', {'name': 'X'}).status_code == 403
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    job = submit(Revision.objects.create(study=study, kind='plan', payload={'model': 'missing'}))
    perform(str(job.pk))
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.error


def test_snapshot_restores_without_mutating_history(client):
    from storm_studio.models import Project, Study, Revision
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    response = client.post(f'/studies/{study.pk}/snapshot/', {'section': 'compare', 'name': 'Before review'})
    assert response.status_code == 302
    snapshot = Revision.objects.get(kind='snapshot')
    response = client.post(f'/revisions/{snapshot.pk}/revise/')
    assert response.status_code == 302
    assert Revision.objects.filter(kind='snapshot').count() == 2


def test_snapshot_preserves_visual_state_and_aggregate_report(client, settings, tmp_path):
    import json
    from storm_studio.models import Project, Study, Revision
    from storm_studio.services import submit, perform
    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    revision = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'data': {'inputs': [1, 2], 'targets': [1, 2], 'train': [0], 'test': [1]}})
    job = submit(revision)
    perform(str(job.pk))
    state = {'selected_jobs': [str(job.pk)], 'cursor': 1, 'filters': {'label': 'all'}}
    response = client.post(f'/studies/{study.pk}/snapshot/', {
        'section': 'compare', 'name': 'comparison', 'state': json.dumps(state)})
    assert response.status_code == 302
    snapshot = Revision.objects.get(kind='snapshot')
    assert snapshot.payload['visual_state'] == state
    aggregate = client.get(f'/reports/studies/{study.pk}/aggregate/json/')
    assert aggregate.status_code == 200
    assert str(job.pk).encode() in aggregate.content


@pytest.mark.django_db
def test_frozen_study_report_keeps_runs_annotations_and_visual_state(client):
    from storm_studio.models import (
        Dataset, DatasetRevision, Job, Project, Revision, Study,
    )

    dataset = Dataset.objects.create(name='Frozen report data')
    dataset_revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'source_fingerprint': 'data-fingerprint',
                   'feature_names': ['nose_x'], 'frame_count': 2})
    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Frozen report',
        dataset_revision=dataset_revision)
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'example_simple', 'dataset_revision_id': dataset_revision.pk,
        'config': {'threshold': 0.5},
    })
    job = Job.objects.create(revision=plan, status='completed', result={
        'model': 'example_simple', 'partition': 'test',
        'data_fingerprint': 'data-fingerprint',
        'metrics': {'accuracy': 1.0}, 'indices': [0, 1], 'predictions': [1, 0],
        'resolved_data': {
            'inputs': [[0.2], [0.8]], 'targets': [1, 0],
            'observation_ids': ['mouse-a:0', 'mouse-a:1'],
        },
        'spec': {'dataset_revision_id': dataset_revision.pk},
    })
    stale_annotation = Revision.objects.create(
        study=study, kind='annotations', parent=plan, payload={
            'job': str(job.pk), 'data_fingerprint': 'old-fingerprint',
            'taxonomy': ['walk', 'grooming'],
            'intervals': [{'start': 0, 'stop': 1, 'label': 'grooming'}],
            'mapping': {'1': 'grooming'}, 'author': 'Earlier review',
            'reason': 'Reviewed against an earlier dataset.',
        })
    annotation = Revision.objects.create(study=study, kind='annotations', parent=plan,
                                         payload={
        'job': str(job.pk), 'data_fingerprint': 'data-fingerprint',
        'taxonomy': ['walk', 'grooming'], 'intervals': [
            {'start': 0, 'stop': 1, 'label': 'walk'}],
        'mapping': {}, 'author': 'Researcher', 'reason': 'Checked the video.',
    })
    analysis = Revision.objects.create(study=study, kind='analysis', parent=plan, payload={
        'execution_id': str(job.pk), 'question': '¿Separa las conductas?',
        'assessment': 'refine', 'metrics': [{'name': 'accuracy', 'value': 0.5}],
    })
    state = {'selected_jobs': [str(job.pk)], 'cursor': 1,
             'filters': {'pose_timeline': 'reference'}}
    snapshot = Revision.objects.create(study=study, kind='snapshot', payload={
        'section': 'evidence', 'visual_state': state,
    })

    created = client.post(f'/reports/studies/{study.pk}/freeze/', {
        'jobs': [str(job.pk)], 'snapshot_revision_id': str(snapshot.pk),
    })

    assert created.status_code == 302
    frozen = Revision.objects.get(study=study, kind='frozen_report')
    payload = frozen.payload
    assert payload['visual_state'] == state
    assert payload['snapshot_revision_id'] == snapshot.pk
    assert payload['runs'][0]['job_id'] == str(job.pk)
    assert payload['runs'][0]['plan']['payload'] == plan.payload
    assert payload['runs'][0]['result']['metrics'] == {'accuracy': 1.0}
    annotations = payload['runs'][0]['annotations']
    assert [(item['revision_id'], item['applies_to_run']) for item in annotations] == [
        (stale_annotation.pk, False), (annotation.pk, True),
    ]
    assert payload['runs'][0]['analyses'][0]['revision_id'] == analysis.pk
    assert payload['runs'][0]['analyses'][0]['payload']['question'] == '¿Separa las conductas?'
    assert payload['dataset_revisions'][0]['source_fingerprint'] == 'data-fingerprint'

    Job.objects.filter(pk=job.pk).update(result={**job.result, 'metrics': {'accuracy': 0.0}})
    Revision.objects.create(study=study, kind='annotations', parent=annotation,
                            payload={**annotation.payload, 'reason': 'Later review.'})
    report_json = client.get(f'/reports/frozen/{frozen.pk}/json/')
    assert report_json.status_code == 200
    assert report_json.json()['runs'][0]['result']['metrics'] == {'accuracy': 1.0}
    assert report_json.json()['runs'][0]['annotations'][1]['payload']['reason'] == (
        'Checked the video.')
    csv_report = client.get(f'/reports/frozen/{frozen.pk}/csv/')
    assert b'mouse-a:0' in csv_report.content
    assert b'grooming' not in csv_report.content
    frozen_html = client.get(f'/reports/frozen/{frozen.pk}/html/')
    assert b'Checked the video.' in frozen_html.content
    assert '¿Separa las conductas?'.encode() in frozen_html.content
    invalid = client.post(f'/reports/studies/{study.pk}/freeze/', {
        'jobs': ['00000000-0000-0000-0000-000000000000'],
    })
    assert invalid.status_code == 400
    assert Revision.objects.filter(study=study, kind='frozen_report').count() == 1


@pytest.mark.django_db
def test_frozen_report_bundle_contains_source_and_model_artifacts(
        client, settings, tmp_path):
    import hashlib
    import json
    from io import BytesIO
    import zipfile
    from storm.artifacts import FileArtifactStore
    from storm_studio.models import (
        Dataset, DatasetAsset, DatasetRevision, Job, Project, Revision, Study,
    )

    settings.WORKSPACE = tmp_path / 'workspace'
    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    settings.WORKSPACE.mkdir()
    source_bytes = b'tiny pose source for bundle verification'
    relative_path = 'data_sources/aa/pose.h5'
    source_path = settings.WORKSPACE / relative_path
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(source_bytes)
    asset_digest = hashlib.sha256(source_bytes).hexdigest()
    asset_store = FileArtifactStore(settings.ARTIFACT_ROOT)
    dataset_ref = asset_store.save(
        kind='datasets', artifact_id='source-dataset', value={'inputs': [[1.0]]})
    model_ref = asset_store.save(
        kind='models', artifact_id='trained-model', value={'weights': [1.0]})
    dataset = Dataset.objects.create(name='Bundled dataset')
    source_asset = DatasetAsset.objects.create(
        dataset=dataset, role='pose', original_name='pose.h5',
        relative_path=relative_path, sha256=asset_digest,
        size_bytes=len(source_bytes), metadata={'adapter': 'dlc_h5'})
    data_revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', asset_ids=[source_asset.pk],
        status='ready', inventory={'source_fingerprint': 'bundle-source'},
        artifact_ref=dataset_ref.to_dict())
    from storm_studio.pose_preview import write_pose_preview_store
    preview_ref = write_pose_preview_store(
        {'inputs': [[1.0, 2.0]], 'feature_names': ['nose_x', 'nose_y'], 'frames': [5]},
        root=settings.ARTIFACT_ROOT, artifact_id='pose-preview-test')
    data_revision.inventory = {
        **data_revision.inventory, 'pose_preview_store': preview_ref,
    }
    data_revision.save(update_fields=['inventory'])
    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Bundle study',
        dataset_revision=data_revision)
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'example_simple', 'dataset_revision_id': data_revision.pk,
    })
    job = Job.objects.create(revision=plan, status='completed', result={
        'model': 'example_simple', 'spec': {'dataset_revision_id': data_revision.pk},
        'model_ref': model_ref.to_dict(), 'indices': [0], 'predictions': [1],
    })
    checkpoint = settings.ARTIFACT_ROOT / 'checkpoints' / str(job.pk) / 'state.bin'
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b'checkpoint state')
    client.post(f'/reports/studies/{study.pk}/freeze/', {'jobs': [str(job.pk)]})
    frozen = Revision.objects.get(study=study, kind='frozen_report')

    response = client.get(f'/reports/frozen/{frozen.pk}/bundle/')

    assert response.status_code == 200
    archive = zipfile.ZipFile(BytesIO(b''.join(response.streaming_content)))
    members = archive.namelist()
    assert any(name.endswith('/pose.h5') for name in members)
    assert f'artifacts/datasets/source-dataset/payload.pkl' in members
    assert f'artifacts/models/trained-model/payload.pkl' in members
    assert f'checkpoints/{job.pk}/state.bin' in members
    assert 'pose_previews/pose-preview-test/manifest.json' in members
    assert any(name.startswith('pose_previews/pose-preview-test/') and name.endswith('.json.gz')
               for name in members)
    manifest = json.loads(archive.read('bundle_manifest.json'))
    entries = {item['path']: item for item in manifest['files']}
    source_member = next(name for name in members if name.endswith('/pose.h5'))
    assert entries[source_member]['sha256'] == asset_digest
    assert archive.read(source_member) == source_bytes
    artifact_member = 'artifacts/models/trained-model/payload.pkl'
    assert entries[artifact_member]['sha256'] == model_ref.digest.removeprefix('sha256:')

    source_path.write_bytes(b'source changed after the report was frozen')
    changed_source = client.get(f'/reports/frozen/{frozen.pk}/bundle/')
    assert changed_source.status_code == 409


def test_restoring_snapshot_exposes_visual_state_without_running_job(client):
    import json
    from storm_studio.models import Project, Study, Revision
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    state = {'selected_jobs': ['missing'], 'cursor': 4}
    snapshot = Revision.objects.create(study=study, kind='snapshot', payload={
        'section': 'compare', 'visual_state': state, 'plan_revision': None})
    response = client.post(f'/revisions/{snapshot.pk}/revise/')
    assert response.status_code == 302
    assert response['Location'].endswith(f'/studies/{study.pk}/compare/?snapshot={snapshot.pk + 1}')
    page = client.get(response['Location'])
    assert page.status_code == 200
    assert b'selected_jobs' in page.content
    assert b'missing' in page.content


def test_report_has_no_remote_dependencies(client, settings, tmp_path):
    from storm_studio.models import Project, Study, Revision
    from storm_studio.services import submit, perform
    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    job = submit(Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'data': {'inputs': [1, 2], 'targets': [1, 2], 'train': [0], 'test': [1]}}))
    perform(str(job.pk))
    for extension in ('html', 'csv', 'json'):
        response = client.get(f'/reports/{job.pk}/{extension}/')
        assert response.status_code == 200
        assert b'https://' not in response.content


@pytest.mark.django_db
def test_reports_show_visuals_and_a_session_aligned_video_player(
        client, settings, tmp_path):
    from storm_studio.models import (
        Dataset, DatasetAsset, DatasetRevision, Job, Project, Revision, Study,
    )
    from storm_studio.pose_preview import write_pose_preview_store

    settings.WORKSPACE = tmp_path / 'workspace'
    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    video_path = settings.WORKSPACE / 'data_sources' / 'mouse.mp4'
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b'video')
    dataset = Dataset.objects.create(name='Visual report data')
    video = DatasetAsset.objects.create(
        dataset=dataset, role='video', original_name='mouse.mp4',
        relative_path='data_sources/mouse.mp4', sha256='a' * 64,
        size_bytes=5, session_id='mouse-a')
    second_video = DatasetAsset.objects.create(
        dataset=dataset, role='video', original_name='mouse-b.mp4',
        relative_path='data_sources/mouse-b.mp4', sha256='c' * 64,
        size_bytes=5, session_id='mouse-b')
    pose_store = write_pose_preview_store({
        'inputs': [[10.0, 20.0], [11.0, 21.0], [12.0, 22.0], [30.0, 40.0]],
        'feature_names': ['nose_x', 'nose_y'],
        'frames': [0, 1, 2, 0], 'video_frames': [0, 1, 2, 0],
        'sessions': ['mouse-a'] * 3 + ['mouse-b'],
        'segments': ['clip-1'] * 3 + ['clip-b'],
    }, root=settings.ARTIFACT_ROOT, artifact_id='report-pose-preview')
    data_revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5',
        asset_ids=[video.pk, second_video.pk],
        status='ready', config={'fps': 30, 'asset_sessions': {
            str(video.pk): 'mouse-a', str(second_video.pk): 'mouse-b'}},
        inventory={'source_fingerprint': 'report-source', 'assets': 1,
                   'feature_names': ['nose_x', 'nose_y'],
                   'pose_preview_store': pose_store})
    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Visual report',
        dataset_revision=data_revision)
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'example_simple', 'dataset_revision_id': data_revision.pk,
    })
    result = {
        'model': 'example_simple', 'partition': 'test',
        'data_fingerprint': 'report-source',
        'metrics': {'accuracy': 0.75, 'f1': 0.67},
        'metric_definitions': [
            {'name': 'accuracy', 'direction': 'maximize'},
            {'name': 'f1', 'direction': 'maximize'},
        ],
        'indices': [0, 1, 2, 3], 'predictions': [0, 1, 1, 0],
        'prediction_mask': [True, True, True, True],
        'resolved_data': {
            'inputs': [[0.1], [0.2], [0.3], [0.4]], 'targets': [0, 1, 1, 0],
            'observation_ids': ['mouse-a:0', 'mouse-a:1', 'mouse-a:2', 'mouse-b:0'],
            'sessions': ['mouse-a'] * 3 + ['mouse-b'], 'frames': [0, 1, 2, 0],
            'video_frames': [0, 1, 2, 0],
            'segments': ['clip-1', 'clip-1', 'clip-2', 'clip-b'],
        },
        'spec': {'dataset_revision_id': data_revision.pk, 'connector': 'dlc_h5'},
        'output_metadata': {'semantics': 'behavior_classification'},
    }
    job = Job.objects.create(revision=plan, status='completed', result=result)
    other_plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'example_wide', 'dataset_revision_id': data_revision.pk,
    })
    Job.objects.create(revision=other_plan, status='completed', result={
        **result, 'model': 'example_wide', 'metrics': {'accuracy': 0.5, 'f1': 0.4},
        'predictions': [1, 0, 1],
    })

    page = client.get(f'/studies/{study.pk}/reports/?job={job.pk}')
    report = client.get(f'/reports/{job.pk}/html/')
    aggregate = client.get(f'/reports/studies/{study.pk}/aggregate/html/')

    assert page.status_code == 200
    assert b'id="prediction-distribution-chart"' in page.content
    assert b'id="comparison-metrics-chart"' in page.content
    assert b'id="prediction-report-player"' in page.content
    assert b'Comportamiento' in page.content and b'Pose' in page.content
    assert f'/studies/{study.pk}/assets/{video.pk}/video/'.encode() in page.content
    assert b'id="report-pose-endpoint"' in page.content
    assert b'id="report-video-session-select"' in page.content
    import json
    import re
    timeline = re.search(
        rb'<script id="report-video-segments" type="application/json">(.*?)</script>',
        page.content, re.S)
    assert timeline
    timeline_rows = json.loads(timeline.group(1))
    assert [(row['start'], row['stop'], row['segment']) for row in timeline_rows] == [
        (0, 1, 'clip-1'), (1, 2, 'clip-1'), (2, 3, 'clip-2'),
    ]
    second_session = client.get(
        f'/studies/{study.pk}/reports/?job={job.pk}&video_session=mouse-b')
    assert f'/studies/{study.pk}/assets/{second_video.pk}/video/'.encode() in second_session.content
    assert report.status_code == 200
    assert b'id="prediction-distribution-chart"' in report.content
    assert b'id="metric-chart"' in report.content
    assert aggregate.status_code == 200
    assert b'id="comparison-metrics-chart"' in aggregate.content


@pytest.mark.django_db
def test_reports_explain_unavailable_pose_tab_without_aligned_pose_preview(
        client, settings, tmp_path):
    from storm_studio.models import (
        Dataset, DatasetAsset, DatasetRevision, Job, Project, Revision, Study,
    )

    settings.WORKSPACE = tmp_path / 'workspace'
    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    dataset = Dataset.objects.create(name='No pose preview')
    video = DatasetAsset.objects.create(
        dataset=dataset, role='video', original_name='mouse.mp4',
        relative_path='data_sources/mouse.mp4', sha256='b' * 64,
        size_bytes=5, session_id='mouse-a')
    data_revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', asset_ids=[video.pk],
        status='ready', config={'asset_sessions': {str(video.pk): 'mouse-a'}},
        inventory={'source_fingerprint': 'no-pose-source', 'assets': 1})
    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='No pose report',
        dataset_revision=data_revision)
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'dataset_revision_id': data_revision.pk,
    })
    job = Job.objects.create(revision=plan, status='completed', result={
        'model': 'identity', 'metrics': {}, 'indices': [0], 'predictions': [1],
        'resolved_data': {
            'inputs': [[1]], 'observation_ids': ['mouse-a:0'],
            'sessions': ['mouse-a'], 'frames': [0],
        },
        'spec': {'dataset_revision_id': data_revision.pk},
    })

    page = client.get(f'/studies/{study.pk}/reports/?job={job.pk}')

    assert page.status_code == 200
    assert b'id="prediction-report-player"' in page.content
    assert b'id="report-pose-tab"' in page.content
    assert b'aria-describedby="report-pose-unavailable"' in page.content
    assert b'pose visual registrada y alineada' in page.content
    assert b'pose-preview-endpoint' not in page.content


def test_report_visuals_label_vame_states_and_require_matching_metric_definitions():
    from types import SimpleNamespace
    from storm_studio.views import _comparison_metric_visuals, _report_visuals

    visuals = _report_visuals({
        'capabilities': ['group'], 'predictions': [0, 0, 2],
        'prediction_mask': [True, True, True], 'metrics': {},
    })
    assert visuals['distribution_title'] == 'Distribución de estados'
    assert [(row['label'], row['count']) for row in visuals['predictions']] == [
        ('Estado 0', 2), ('Estado 2', 1),
    ]
    first = SimpleNamespace(pk='a', result={
        'metrics': {'accuracy': 0.8},
        'metric_definitions': [{'name': 'accuracy', 'version': '1',
                                'direction': 'maximize'}],
    })
    second = SimpleNamespace(pk='b', result={
        'metrics': {'accuracy': 0.9},
        'metric_definitions': [{'name': 'accuracy', 'version': '2',
                                'direction': 'maximize'}],
    })
    assert _comparison_metric_visuals([first, second]) == []
    same_definition = SimpleNamespace(pk='c', result={
        'model': 'supervised_wide', 'metrics': {'accuracy': 0.9},
        'metric_definitions': [{'name': 'accuracy', 'version': '1',
                                'direction': 'maximize'}],
    })
    no_metrics = SimpleNamespace(pk='d', result={
        'model': 'vame_native', 'metrics': {}, 'metric_definitions': [],
    })
    charts = _comparison_metric_visuals([first, same_definition, no_metrics])
    assert len(charts) == 1
    assert charts[0]['run_count'] == 2
    assert [point['job'] for point in charts[0]['points']] == ['a', 'c']


def test_preparation_windowed_pose_uses_center_frame_for_visual_preview(
        settings, tmp_path):
    from storm.artifacts import ArtifactRef, FileArtifactStore
    from storm.pipeline import PipelineStep
    from storm_studio.data_preparation import materialize_preparation
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study
    from storm_studio.services import catalog

    class TemporalWindowStep(PipelineStep):
        step_type = 'pose.temporal_windows'

        def __init__(self, *, offsets):
            self.offsets = offsets

        def process(self, context):
            context.data = [[[value[0] - 100, value[1] - 100], value,
                             [value[0] + 100, value[1] + 100]]
                            for value in context.data]
            return context

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    loaded = {
        'inputs': [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]],
        'feature_names': ['nose_x', 'nose_y'],
        'frames': [0, 1, 2], 'video_frames': [0, 1, 2],
        'sessions': ['mouse'] * 3, 'segments': ['mouse:segment-0'] * 3,
        'partitions': ['train', 'train', 'test'],
    }
    store = FileArtifactStore(settings.ARTIFACT_ROOT)
    source_ref = store.save(kind='datasets', artifact_id='window-source', value=loaded)
    dataset = Dataset.objects.create(name='Windowed pose')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'frame_count': 3, 'feature_names': loaded['feature_names']},
        artifact_ref=source_ref.to_dict())
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Windows',
                                 dataset_revision=source)
    recipe = Revision.objects.create(study=study, kind='preparation', payload={
        'name': 'Ventanas', 'dataset_revision_id': source.pk,
        'steps': [{'type': 'pose.temporal_windows', 'config': {'offsets': [-1, 0, 1]}}],
    })
    pipeline_catalog = catalog()
    pipeline_catalog.steps.register(TemporalWindowStep)

    result = materialize_preparation(
        recipe.pk, 'window-run', artifact_root=settings.ARTIFACT_ROOT,
        catalog=pipeline_catalog)

    prepared_revision = DatasetRevision.objects.get(pk=result['prepared_dataset_revision_id'])
    prepared = store.load(ArtifactRef.from_dict(prepared_revision.artifact_ref))
    assert prepared['inputs'][1] == [[-97.0, -96.0], [3.0, 4.0], [103.0, 104.0]]
    from storm_studio.pose_preview import read_pose_preview_page
    preview = read_pose_preview_page(
        root=settings.ARTIFACT_ROOT,
        store_ref=prepared_revision.inventory['pose_preview_store'],
        session_id='mouse', offset=0, limit=3)
    assert [row['features'] for row in preview['rows']] == [
        [1.0, 2.0], [3.0, 4.0], [5.0, 6.0],
    ]


def test_preparation_recovers_a_valid_orphan_artifact_without_transforming_again(
        settings, tmp_path, monkeypatch):
    from storm.artifacts import FileArtifactStore
    from storm.config import fingerprint
    from storm_studio import data_preparation
    from storm_studio.data_preparation import materialize_preparation
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study
    from storm_studio.services import catalog

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    store = FileArtifactStore(settings.ARTIFACT_ROOT)
    source_data = {
        'inputs': [[1.0, 2.0], [3.0, 4.0]], 'feature_names': ['nose_x', 'nose_y'],
        'partitions': ['train', 'test'], 'sessions': ['mouse', 'mouse'],
        'frames': [0, 1], 'segments': ['clip', 'clip'],
    }
    source_ref = store.save(kind='datasets', artifact_id='orphan-source', value=source_data)
    dataset = Dataset.objects.create(name='Recover preparation')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'frame_count': 2, 'feature_names': source_data['feature_names']},
        artifact_ref=source_ref.to_dict())
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Recovery',
                                 dataset_revision=source)
    recipe = Revision.objects.create(study=study, kind='preparation', payload={
        'name': 'Sin cambios', 'dataset_revision_id': source.pk, 'steps': [],
    })
    pipeline_catalog = catalog()
    steps = data_preparation.resolve_preparation_steps([], source_data['feature_names'])
    versions = data_preparation._step_versions(steps, pipeline_catalog)
    recipe_fingerprint = fingerprint({
        'source': source.artifact_ref, 'steps': steps, 'step_versions': versions,
    })
    recovered_data = {
        **source_data,
        'preparation': {
            'revision_id': recipe.pk, 'fingerprint': recipe_fingerprint,
            'execution_id': 'interrupted-run', 'resolved_steps': [],
            'step_versions': versions, 'source_dataset_revision_id': source.pk,
            'fitted_steps': [], 'source_observation_indices': [0, 1],
            'stage_preview': [],
        },
    }
    orphan_ref = store.save(
        kind='datasets', artifact_id=f'prepared-dataset-{dataset.pk}-r8',
        value=recovered_data, metadata={
            'source_dataset_revision': source.pk, 'preparation_revision': recipe.pk,
            'preparation_fingerprint': recipe_fingerprint, 'step_versions': versions,
            'execution_id': 'interrupted-run',
        })
    monkeypatch.setattr(
        data_preparation, 'transform_aligned',
        lambda *args, **kwargs: pytest.fail('matching orphan should be reused'))
    original_load = FileArtifactStore.load

    def load_only_orphan(store_instance, reference):
        if reference.artifact_id == 'orphan-source':
            pytest.fail('a recovered preparation should not reload the raw source')
        return original_load(store_instance, reference)

    monkeypatch.setattr(FileArtifactStore, 'load', load_only_orphan)

    result = materialize_preparation(
        recipe.pk, 'retry-run', artifact_root=settings.ARTIFACT_ROOT,
        catalog=pipeline_catalog)

    assert result['recovered'] is True
    assert result['recovered_from_execution_id'] == 'interrupted-run'
    recovered_revision = DatasetRevision.objects.get(pk=result['prepared_dataset_revision_id'])
    assert recovered_revision.artifact_ref == orphan_ref.to_dict()
    assert recovered_revision.config['recovered_artifact'] is True


def test_report_visuals_summarize_state_bouts_and_frame_transitions():
    from storm_studio.views import _report_visuals

    visuals = _report_visuals({
        'capabilities': ['group'], 'indices': [0, 1, 2, 3],
        'predictions': [0, 0, 1, 0], 'prediction_mask': [True] * 4,
        'resolved_data': {
            'inputs': [[0], [0], [0], [0]], 'sessions': ['mouse'] * 4,
            'segments': ['clip'] * 4, 'frames': [0, 1, 2, 3],
        },
    })

    assert visuals['state_transitions'] == [
        {'from': 'Estado 0', 'to': 'Estado 1', 'count': 1},
        {'from': 'Estado 1', 'to': 'Estado 0', 'count': 1},
    ]
    assert [(row['label'], row['bouts'], row['mean_frames'])
            for row in visuals['state_durations']] == [
        ('Estado 0', 2, 1.5), ('Estado 1', 1, 1.0),
    ]


def test_state_diagnostics_do_not_bridge_frame_discontinuities():
    from storm_studio.views import _report_visuals

    visuals = _report_visuals({
        'capabilities': ['group'], 'indices': [0, 1], 'predictions': [0, 1],
        'prediction_mask': [True, True],
        'resolved_data': {
            'inputs': [[0], [0]], 'sessions': ['mouse', 'mouse'],
            'segments': ['clip-a', 'clip-a'], 'frames': [12, 14],
        },
    })

    assert visuals['state_transitions'] == []
    assert all(row['bouts'] == 1 for row in visuals['state_durations'])


def test_state_report_builds_colored_bout_histogram_and_transition_heatmap():
    from storm_studio.views import _report_visuals

    visuals = _report_visuals({
        'capabilities': ['group'], 'indices': list(range(7)),
        'predictions': [0, 0, 1, 1, 1, 0, 2],
        'prediction_mask': [True] * 7,
        'resolved_data': {
            'inputs': [[0]] * 7, 'sessions': ['mouse'] * 7,
            'segments': ['clip'] * 7, 'frames': list(range(7)),
        },
    })

    histogram = visuals['state_duration_histogram']
    assert [bucket['label'] for bucket in histogram['bins']] == ['1', '2–3']
    assert [state['label'] for state in histogram['states']] == [
        'Estado 0', 'Estado 1', 'Estado 2',
    ]
    assert len({state['color'] for state in histogram['states']}) == 3
    assert [bar['count'] for bar in histogram['bins'][0]['bars']] == [1, 0, 1]
    assert [bar['count'] for bar in histogram['bins'][1]['bars']] == [1, 1, 0]

    matrix = visuals['state_transition_matrix']
    assert matrix['labels'] == ['Estado 0', 'Estado 1', 'Estado 2']
    assert [[cell['count'] for cell in row['cells']] for row in matrix['rows']] == [
        [0, 1, 1], [1, 0, 0], [0, 0, 0],
    ]
    assert all(cell['background'] for row in matrix['rows'] for cell in row['cells'])


def test_report_shows_state_charts_and_collapsible_value_tables(client):
    from storm_studio.models import Job, Project, Revision, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='State visuals')
    plan = Revision.objects.create(study=study, kind='plan', payload={'model': 'vame_native'})
    Job.objects.create(revision=plan, status='completed', result={
        'model': 'vame_native', 'partition': 'test', 'capabilities': ['group'],
        'indices': list(range(4)), 'predictions': [0, 0, 1, 1],
        'prediction_mask': [True] * 4, 'resolved_data': {
            'inputs': [[0]] * 4, 'sessions': ['mouse'] * 4,
            'segments': ['clip'] * 4, 'frames': list(range(4)),
        },
    })

    page = client.get(f'/studies/{study.pk}/reports/').content.decode()

    assert 'id="state-duration-histogram"' in page
    assert 'id="state-transition-matrix"' in page
    assert 'Ver tabla de valores por episodio' in page
    assert 'Ver recuentos exactos de transiciones' in page


def test_report_visuals_bin_regression_outputs_instead_of_listing_each_value():
    from storm_studio.views import _report_visuals

    visuals = _report_visuals({
        'indices': list(range(100)), 'predictions': [index / 10 for index in range(100)],
        'prediction_mask': [True] * 100,
        'output_metadata': {'task': 'regression'},
        'resolved_data': {'inputs': [[index] for index in range(100)]},
    })

    assert visuals['distribution_title'] == 'Distribución de salida numérica'
    assert len(visuals['predictions']) <= 20
    assert sum(row['count'] for row in visuals['predictions']) == 100


def test_report_visuals_measure_coverage_within_the_run_scope():
    from storm_studio.views import _report_visuals

    visuals = _report_visuals({
        'indices': [5, 6, 7], 'predictions': [0, 1, 1],
        'prediction_mask': [True, True, True],
        'resolved_data': {
            'inputs': [[value] for value in range(10)],
            'targets': [None] * 10,
        },
    })

    assert visuals['observation_count'] == 10
    assert visuals['prediction_scope_count'] == 3
    assert visuals['valid_prediction_count'] == 3
    assert visuals['excluded_prediction_count'] == 0
    assert visuals['outside_run_count'] == 7
    assert visuals['prediction_coverage'] == 100.0


def test_report_visuals_flag_classification_without_behavior_mapping():
    from storm_studio.views import _report_visuals

    visuals = _report_visuals({
        'indices': [0, 1, 2], 'predictions': [0, 0, 1],
        'prediction_mask': [True, True, True],
        'output_metadata': {
            'task': 'classification', 'category_mapping': {'0': 'unknown', '1': 'unknown'},
        },
    })

    assert visuals['taxonomy_alignment_warning'] is True
    assert [(row['label'], row['count']) for row in visuals['predictions']] == [
        ('Clase 0 (sin correspondencia)', 2), ('Clase 1 (sin correspondencia)', 1),
    ]


def test_report_visuals_count_excluded_outputs_inside_the_run_scope():
    from storm_studio.views import _report_visuals

    visuals = _report_visuals({
        'indices': [2, 4, 6], 'predictions': [0, 1, 1],
        'prediction_mask': [True, False, True],
        'resolved_data': {'inputs': [[value] for value in range(10)]},
    })

    assert visuals['prediction_scope_count'] == 3
    assert visuals['valid_prediction_count'] == 2
    assert visuals['excluded_prediction_count'] == 1
    assert visuals['outside_run_count'] == 7
    assert visuals['prediction_coverage'] == 66.7


def test_evidence_displays_pose_vectors_as_features_not_categorical_labels(client):
    from storm_studio.models import Job, Project, Revision, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Pose report')
    plan = Revision.objects.create(study=study, kind='plan', payload={'model': 'classifier'})
    job = Job.objects.create(revision=plan, status='completed', result={
        'model': 'classifier', 'indices': [0, 1], 'predictions': [0, 1],
        'prediction_mask': [True, True],
        'output_metadata': {'task': 'classification', 'semantics': 'behavior_classification'},
        'resolved_data': {
            'inputs': [[0.25, 0.75], [0.5, 0.8]], 'feature_names': ['nose_x', 'nose_y'],
            'targets': [0, 1], 'evaluation_mask': [True, True],
        },
    })

    response = client.get(f'/studies/{study.pk}/evidence/?job={job.pk}')

    assert response.status_code == 200
    assert b'data-visual-kind="multivariate"' in response.content
    assert b'id="evidence-feature-select"' in response.content
    assert b'nose_x' in response.content and b'nose_y' in response.content
    assert '2 características'.encode() in response.content
    assert b'[0.25, 0.75]' not in response.content


def test_analysis_and_reports_choose_run_before_showing_results(client):
    from storm_studio.models import Job, Project, Revision, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Run dashboard')
    plan = Revision.objects.create(study=study, kind='plan', payload={'model': 'identity'})
    job = Job.objects.create(revision=plan, status='completed', result={
        'model': 'identity', 'indices': [5, 6], 'predictions': [1, 0],
        'prediction_mask': [True, True], 'metrics': {}, 'partition': 'test',
        'resolved_data': {
            'inputs': [[value] for value in range(10)], 'targets': [None] * 10,
        },
    })

    analysis = client.get(f'/studies/{study.pk}/evidence/?job={job.pk}').content.decode()
    reports = client.get(f'/studies/{study.pk}/reports/?job={job.pk}').content.decode()

    assert analysis.index('Elegir corrida para analizar') < analysis.index('id="analysis-dashboard"')
    assert '2 de 2 observaciones evaluadas tienen una salida válida' in analysis
    assert 'Otras 8 observaciones del dataset están fuera de esta corrida' in analysis
    assert reports.index('id="report-run-select"') < reports.index('Congelar evidencia reproducible')
    assert '2 / 2' in reports


def test_analysis_page_calculates_registered_metrics_on_valid_labels(client):
    from storm_studio.models import Job, Project, Revision, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Metrics')
    plan = Revision.objects.create(study=study, kind='plan', payload={'model': 'identity'})
    job = Job.objects.create(revision=plan, status='completed', result={
        'model': 'identity', 'indices': [0, 1, 2], 'predictions': [1, 0, 1],
        'prediction_mask': [True, True, True], 'metrics': {}, 'metric_definitions': [],
        'output_metadata': {'task': 'classification', 'semantics': 'behavior_classification'},
        'resolved_data': {
            'inputs': [[0], [1], [2]], 'targets': [1, 1, None],
            'evaluation_mask': [True, True, False], 'taxonomy': ['rest', 'move'],
            'sessions': ['mouse'] * 3, 'frames': [0, 1, 2],
        },
    })

    page = client.get(f'/studies/{study.pk}/evidence/?job={job.pk}&metric=accuracy')

    assert page.status_code == 200
    assert 'Métricas exploratorias'.encode() in page.content
    assert b'accuracy' in page.content
    assert page.context['analysis_metrics'] == {
        'choices': ['accuracy'], 'rows': [{'name': 'accuracy', 'value': 0.5,
                                           'direction': 'maximize'}],
        'sample_count': 2, 'reason': '', 'errors': [], 'selected': ['accuracy'],
    }
    assert b'0,5000' in page.content
    assert b'2 observaciones etiquetadas y predichas' in page.content


def test_analysis_page_limits_evidence_rows_and_keeps_metric_filter(client):
    from storm_studio.models import Job, Project, Revision, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Paged evidence')
    plan = Revision.objects.create(study=study, kind='plan', payload={'model': 'identity'})
    count = 205
    job = Job.objects.create(revision=plan, status='completed', result={
        'model': 'identity', 'indices': list(range(count)),
        'predictions': [index % 2 for index in range(count)],
        'prediction_mask': [True] * count, 'output_metadata': {'task': 'classification'},
        'resolved_data': {
            'inputs': [[index] for index in range(count)],
            'targets': [index % 2 for index in range(count)],
            'evaluation_mask': [True] * count, 'sessions': ['mouse'] * count,
            'frames': list(range(count)),
        },
    })

    page = client.get(
        f'/studies/{study.pk}/evidence/?job={job.pk}&metric=accuracy&evidence_page=2')

    assert page.status_code == 200
    assert page.context['evidence_page_number'] == 2
    assert len(page.context['evidence']) == 100
    assert page.context['evidence'][0]['index'] == 100
    assert f'job={job.pk}&amp;metric=accuracy&amp;evidence_page=3'.encode() in page.content


def test_analysis_saves_researcher_interpretation_as_a_new_revision(client):
    from storm_studio.models import Job, Project, Revision, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Decision trail')
    plan = Revision.objects.create(study=study, kind='plan', payload={'model': 'identity'})
    job = Job.objects.create(revision=plan, status='completed', result={
        'model': 'identity', 'indices': [0, 1], 'predictions': [1, 0],
        'prediction_mask': [True, True], 'output_metadata': {'task': 'classification'},
        'resolved_data': {
            'inputs': [[0], [1]], 'targets': [1, 1],
            'evaluation_mask': [True, True], 'taxonomy': ['rest', 'move'],
        },
    })

    response = client.post(f'/studies/{study.pk}/analysis/save/', {
        'job': str(job.pk), 'metric': ['accuracy'], 'question': '¿Separa movimiento?',
        'interpretation': 'Aún confunde reposo con movimiento.', 'assessment': 'refine',
    })

    analysis = Revision.objects.get(study=study, kind='analysis')
    assert response.status_code == 302
    assert analysis.parent == plan
    assert analysis.payload['execution_id'] == str(job.pk)
    assert analysis.payload['metrics'][0]['value'] == 0.5
    assert analysis.payload['question'] == '¿Separa movimiento?'
    assert analysis.payload['assessment'] == 'refine'


def test_html_report_collapses_full_manifest_and_shows_coverage(client):
    from storm_studio.models import Job, Project, Revision, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Readable report')
    plan = Revision.objects.create(study=study, kind='plan', payload={'model': 'identity'})
    job = Job.objects.create(revision=plan, status='completed', result={
        'model': 'identity', 'indices': [0, 1], 'predictions': [1, 0],
        'prediction_mask': [True, False], 'metrics': {},
        'resolved_data': {
            'inputs': [[0], [1]], 'targets': [1, None],
            'evaluation_mask': [True, False], 'sessions': ['mouse'] * 2,
            'frames': [0, 1],
        },
    })

    response = client.get(f'/reports/{job.pk}/html/')

    assert response.status_code == 200
    assert response['Content-Disposition'].startswith('inline;')
    assert b'Cobertura de inferencia' in response.content
    assert b'<details><summary>Resumen de procedencia</summary>' in response.content


def test_human_annotation_revisions_record_author_reason_and_parent(client):
    from storm_studio.models import Job, Project, Revision, Study
    from storm_studio.services import annotate

    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    plan = Revision.objects.create(study=study, kind='plan', payload={})
    job = Job.objects.create(revision=plan, status='completed', result={
        'resolved_data': {'inputs': [[0], [1]]},
        'data_fingerprint': 'sha256:dataset',
        'capabilities': ['group'], 'predictions': [0, 1],
    })
    first = annotate(
        job, taxonomy=['rest', 'move'], intervals=[{'start': 0, 'stop': 1, 'label': 'rest'}],
        mapping={}, author='Researcher A', reason='Corrected video review',
    )
    second = annotate(
        job, taxonomy=['rest', 'move'], intervals=[{'start': 1, 'stop': 2, 'label': 'move'}],
        mapping={}, author='Researcher B', reason='Reviewed second interval',
    )

    assert first.payload['author'] == 'Researcher A'
    assert first.payload['reason'] == 'Corrected video review'
    assert second.parent_id == first.pk
    assert first.payload['intervals'] == [{'start': 0, 'stop': 1, 'label': 'rest'}]


def test_import_generic_records_creates_revision(client):
    import json
    from django.core.files.uploadedfile import SimpleUploadedFile
    from storm_studio.models import Project, Study, Revision
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    original = Revision.objects.create(study=study, kind='plan', payload={
        'connector': 'json_records', 'model': 'identity', 'config': {}, 'metrics': ['accuracy'],
        'data': {'inputs': ['A', 'B'], 'targets': ['A', 'B'], 'train': [0], 'test': [1]}})
    data = {'inputs': ['C', 'D'], 'targets': ['C', 'D'], 'train': [0], 'test': [1]}
    upload = SimpleUploadedFile('records.json', json.dumps(data).encode(), content_type='application/json')
    assert client.post(f'/studies/{study.pk}/import/', {'dataset': upload}).status_code == 302
    latest = Revision.objects.order_by('-pk').first()
    assert latest.payload['data'] == data
    assert latest.parent_id == original.pk
    original.refresh_from_db()
    assert original.payload['data']['inputs'] == ['A', 'B']


def test_studio_brand_can_be_configured_for_rainstorm(client, settings):
    from storm_studio.models import Project, Study

    settings.STUDIO_BRAND_NAME = 'RAINSTORM'
    settings.STUDIO_BRAND_TAGLINE = 'Pose and behavior research'

    response = client.get('/')

    assert response.status_code == 200
    content = response.content.decode()
    assert '<title>RAINSTORM · Estudios</title>' in content
    assert 'aria-label="RAINSTORM"' in content
    assert 'class="brand-mark"' in content
    assert '<strong>RAINSTORM</strong>' in content
    assert 'Pose and behavior research' in content
    assert ':root{font:16px/1.5 system-ui,sans-serif' in content
    assert '.app-shell{display:grid;grid-template-columns:240px' in content
    assert '.app-shell-home{grid-template-columns:minmax(0,1fr)}' in content
    assert 'class="app-shell app-shell-home"' in content
    assert '.card{padding:24px;background:white' in content

    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    study_page = client.get(f'/studies/{study.pk}/flow/')
    assert study_page.status_code == 200
    study_content = study_page.content.decode()
    assert '<title>RAINSTORM · Flujo</title>' in study_content
    assert 'aria-label="RAINSTORM"' in study_content
    assert 'class="brand-mark"' in study_content


def test_data_upload_registers_pose_rois_video_and_labels_without_a_model(
        client, settings, tmp_path, monkeypatch):
    import hashlib
    from django.core.files.uploadedfile import SimpleUploadedFile
    from storm_studio.models import DatasetAsset, DatasetRevision, Project, Study
    from types import SimpleNamespace
    import storm_studio.forms

    monkeypatch.setattr(storm_studio.forms, 'catalog', lambda: SimpleNamespace(
        connectors={'dlc_h5': object(), 'dlc_csv': object()}))

    settings.WORKSPACE = tmp_path / 'workspace'
    settings.WORKSPACE.mkdir()
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')

    upload_form = storm_studio.forms.DatasetUploadForm()
    assert 'csv_frame_base' in upload_form.fields
    assert 'pose_frame_base' in upload_form.fields
    assert 'label_frame_reference' in upload_form.fields
    response = client.post(f'/studies/{study.pk}/data/upload/', {
        'pose_adapter': 'dlc_h5',
        'fps': 30,
        'csv_frame_base': 0,
        'pose_frame_base': 1,
        'label_frame_reference': 'video',
        'pose_files': [SimpleUploadedFile('mouse_position.h5', b'h5-content')],
        'roi_files': [SimpleUploadedFile('arena_rois.json', b'{"points": []}')],
        'video_files': [SimpleUploadedFile('mouse.mp4', b'mp4-content')],
        'label_files': [SimpleUploadedFile('manual.csv', b'Frame,label\n1,explore\n')],
    })

    assert response.status_code == 302
    assets = list(DatasetAsset.objects.order_by('role'))
    assert [asset.role for asset in assets] == ['labels', 'pose', 'roi', 'video']
    assert all((settings.WORKSPACE / asset.relative_path).is_file() for asset in assets)
    pose_asset = next(asset for asset in assets if asset.role == 'pose')
    assert pose_asset.sha256 == hashlib.sha256(b'h5-content').hexdigest()
    assert pose_asset.session_id == 'mouse_position'
    revision = DatasetRevision.objects.get()
    assert revision.connector == 'dlc_h5'
    assert set(revision.asset_ids) == {asset.pk for asset in assets}
    associated = revision.config['asset_sessions']
    assert associated[str(pose_asset.pk)] == 'mouse_position'
    assert associated[str(next(asset.pk for asset in assets if asset.role == 'video'))] == 'mouse_position'
    assert associated[str(next(asset.pk for asset in assets if asset.role == 'labels'))] == 'mouse_position'
    assert associated[str(next(asset.pk for asset in assets if asset.role == 'roi'))] == 'global'
    assert revision.config['csv_frame_base'] == 0
    assert revision.config['pose_frame_base'] == 1
    assert revision.config['label_frame_reference'] == 'video'
    from storm_studio.dataset_inventory import connector_input
    adapter_data = connector_input(revision.pk, workspace=settings.WORKSPACE)
    assert adapter_data['csv_frame_base'] == 0
    assert adapter_data['pose_frame_base'] == 1
    assert adapter_data['label_frame_reference'] == 'video'
    assert adapter_data['video_frame_offsets_by_session'] == {'mouse_position': 0}
    study.refresh_from_db()
    assert study.dataset_revision_id == revision.pk
    assert not study.revision_set.filter(kind='plan').exists()


def test_data_page_is_separate_from_model_pipeline_form(client):
    from storm_studio.models import Project, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')

    data_page = client.get(f'/studies/{study.pk}/data/').content.decode()
    flow_page = client.get(f'/studies/{study.pk}/flow/').content.decode()

    assert 'name="pose_files"' in data_page
    assert 'name="roi_files"' in data_page
    assert 'name="video_files"' in data_page
    assert 'name="label_files"' in data_page
    assert 'id="pipeline-form"' not in data_page
    assert 'id="pipeline-form"' in flow_page


def test_source_session_associations_create_a_new_dataset_revision(client, settings, tmp_path):
    from storm_studio.models import Dataset, DatasetAsset, DatasetRevision, Project, Study

    settings.WORKSPACE = tmp_path
    dataset = Dataset.objects.create(name='Associated pose sources')
    pose = DatasetAsset.objects.create(
        dataset=dataset, role='pose', original_name='mouse-aDLC_result.h5',
        relative_path='data_sources/pose.h5', sha256='a' * 64, size_bytes=10,
        session_id='mouse-a')
    video = DatasetAsset.objects.create(
        dataset=dataset, role='video', original_name='camera-recording.mp4',
        relative_path='data_sources/video.mp4', sha256='b' * 64, size_bytes=20,
        session_id='camera-recording')
    roi = DatasetAsset.objects.create(
        dataset=dataset, role='roi', original_name='arena.json',
        relative_path='data_sources/roi.json', sha256='c' * 64, size_bytes=30,
        session_id='')
    labels = DatasetAsset.objects.create(
        dataset=dataset, role='labels', original_name='manual.csv',
        relative_path='data_sources/labels.csv', sha256='d' * 64, size_bytes=40,
        session_id='manual')
    for relative_path in ('data_sources/pose.h5', 'data_sources/video.mp4',
                          'data_sources/roi.json', 'data_sources/labels.csv'):
        source = settings.WORKSPACE / relative_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'source')
    original = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5',
        asset_ids=[pose.pk, video.pk, roi.pk, labels.pk],
        config={'fps': 30.0, 'csv_frame_base': 1, 'pose_frame_base': 0,
            'label_frame_reference': 'video', 'asset_sessions': {
            str(video.pk): '', str(roi.pk): '', str(labels.pk): '',
        }}, status='ready', inventory={
            'assets': 4, 'sessions': [{'session_id': 'mouse-a', 'frames': 2}],
            'frame_count': 2,
        })
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Associations',
                                 dataset_revision=original)

    page = client.get(f'/studies/{study.pk}/data/')
    assert page.status_code == 200
    assert b'Vincular fuentes a sesiones' in page.content
    assert f'name="asset_session_{video.pk}"'.encode() in page.content
    assert f'name="asset_video_frame_offset_{video.pk}"'.encode() in page.content

    response = client.post(f'/studies/{study.pk}/data/associations/', {
        f'asset_session_{video.pk}': 'mouse-a',
        f'asset_video_frame_offset_{video.pk}': '5',
        f'asset_session_{roi.pk}': 'global',
        f'asset_session_{labels.pk}': 'mouse-a',
    })

    assert response.status_code == 302
    study.refresh_from_db()
    original.refresh_from_db()
    updated = study.dataset_revision
    assert updated.pk != original.pk
    assert updated.number == 2
    assert updated.status == 'registered'
    assert updated.config['asset_sessions'] == {
        str(pose.pk): 'mouse-a', str(video.pk): 'mouse-a',
        str(roi.pk): 'global', str(labels.pk): 'mouse-a',
    }
    assert updated.config['video_frame_offsets'] == {str(video.pk): 5}
    assert original.status == 'ready'
    assert original.config == {'fps': 30.0, 'csv_frame_base': 1, 'pose_frame_base': 0,
        'label_frame_reference': 'video', 'asset_sessions': {
        str(video.pk): '', str(roi.pk): '', str(labels.pk): '',
    }}
    assert original.inventory['frame_count'] == 2
    from storm_studio.dataset_inventory import connector_input
    adapter_data = connector_input(updated.pk, workspace=settings.WORKSPACE)
    assert adapter_data['csv_frame_base'] == 1
    assert adapter_data['pose_frame_base'] == 0
    assert adapter_data['label_frame_reference'] == 'video'
    assert adapter_data['video_frame_offsets_by_session'] == {'mouse-a': 5}


def test_adding_pose_sources_preserves_existing_manual_links_and_partitions(
        client, settings, tmp_path, monkeypatch):
    import hashlib
    from django.core.files.uploadedfile import SimpleUploadedFile
    from storm_studio.models import Dataset, DatasetAsset, DatasetRevision, Project, Study
    from types import SimpleNamespace
    import storm_studio.forms

    monkeypatch.setattr(storm_studio.forms, 'catalog', lambda: SimpleNamespace(
        connectors={'dlc_h5': object()}))
    settings.WORKSPACE = tmp_path / 'workspace'
    settings.WORKSPACE.mkdir()
    dataset = Dataset.objects.create(name='Append pose')
    first_pose = DatasetAsset.objects.create(
        dataset=dataset, role='pose', original_name='mouse-aDLC_pose.h5',
        relative_path='data_sources/a.h5', sha256=hashlib.sha256(b'first').hexdigest(),
        size_bytes=5, session_id='mouse-a')
    second_pose = DatasetAsset.objects.create(
        dataset=dataset, role='pose', original_name='mouse-bDLC_pose.h5',
        relative_path='data_sources/b.h5', sha256='b' * 64, size_bytes=5,
        session_id='mouse-b')
    video = DatasetAsset.objects.create(
        dataset=dataset, role='video', original_name='camera-recording.mp4',
        relative_path='data_sources/video.mp4', sha256='c' * 64, size_bytes=5,
        session_id='camera-recording')
    bindings = {str(first_pose.pk): 'mouse-a', str(second_pose.pk): 'mouse-b',
                str(video.pk): 'mouse-b'}
    original = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5',
        asset_ids=[first_pose.pk, second_pose.pk, video.pk],
        config={'fps': 30, 'asset_sessions': bindings,
                'session_partitions': {'mouse-a': 'train', 'mouse-b': 'test'}},
        status='ready', inventory={'assets': 3})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Append',
                                 dataset_revision=original)

    response = client.post(f'/studies/{study.pk}/data/upload/', {
        'pose_adapter': 'dlc_h5', 'fps': 30,
        'pose_files': [SimpleUploadedFile('mouse-aDLC_pose.h5', b'first')],
    })

    assert response.status_code == 302
    study.refresh_from_db()
    updated = study.dataset_revision
    assert updated.pk != original.pk
    assert updated.config['asset_sessions'] == bindings
    assert updated.config['session_partitions'] == {
        'mouse-a': 'train', 'mouse-b': 'test'}
    assert original.config['asset_sessions'] == bindings


def test_data_upload_rejects_pose_extension_mismatch_without_registering_sources(
    client, settings, monkeypatch):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from storm_studio.models import Dataset, DatasetAsset, Project, Study
    import storm_studio.forms

    test_catalog = storm_studio.forms.catalog()
    test_catalog.connectors['dlc_h5'] = object()
    monkeypatch.setattr(storm_studio.forms, 'catalog', lambda: test_catalog)
    settings.WORKSPACE = settings.WORKSPACE / 'invalid-upload-test'
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')

    response = client.post(f'/studies/{study.pk}/data/upload/', {
        'pose_adapter': 'dlc_h5', 'fps': 30,
        'pose_files': [SimpleUploadedFile('pose.txt', b'not pose')],
    }, follow=True)

    assert response.status_code == 200
    assert 'Formato no admitido para pose' in response.content.decode()
    assert Dataset.objects.count() == 0
    assert DatasetAsset.objects.count() == 0
    assert not study.revision_set.filter(kind='plan').exists()


def test_model_flow_can_select_a_registered_ready_dataset(client):
    from storm_studio.models import Dataset, DatasetRevision, Project, Study

    dataset = Dataset.objects.create(name='Pose sessions')
    data_revision = DatasetRevision.objects.create(
        dataset=dataset, number=2, connector='json_records',
        status='ready', inventory={'frame_count': 10})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=data_revision)

    response = client.get(f'/studies/{study.pk}/flow/')
    content = response.content.decode()

    assert response.status_code == 200
    assert 'name="dataset_revision_id"' in content
    assert f'value="{data_revision.pk}"' in content
    assert 'Pose sessions' in content


def test_plan_revision_references_dataset_without_copying_its_inputs(client):
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study

    dataset = Dataset.objects.create(name='Pose sessions')
    data_revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='json_records', status='ready',
        asset_ids=[], inventory={'frame_count': 10})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=data_revision)

    response = client.post(f'/studies/{study.pk}/flow/', {
        'operation': 'train', 'model': 'identity', 'connector': 'json_records',
        'dataset_revision_id': str(data_revision.pk), 'data': '{}', 'steps': '[]',
        'config': '{}', 'branch_configs': '{}', 'metrics': [], 'seed': 42,
    })

    assert response.status_code == 302, response.context['form'].errors
    plan = Revision.objects.get(study=study, kind='plan')
    assert plan.payload['dataset_revision_id'] == data_revision.pk
    assert plan.payload['data'] == {}


def test_session_partition_edit_creates_a_new_dataset_revision(client):
    from storm_studio.models import Dataset, DatasetRevision, Project, Study

    dataset = Dataset.objects.create(name='Pose sessions')
    data_revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='json_records', status='ready',
        inventory={
            'sessions': [
                {'session_id': 's1', 'frames': 2, 'segments': 1,
                 'first_frame': 0, 'last_frame': 1},
                {'session_id': 's2', 'frames': 2, 'segments': 1,
                 'first_frame': 0, 'last_frame': 1},
            ],
            'preview': [
                {'session_id': 's1', 'frame': 0, 'partition': 'train'},
                {'session_id': 's1', 'frame': 1, 'partition': 'train'},
                {'session_id': 's2', 'frame': 0, 'partition': 'train'},
                {'session_id': 's2', 'frame': 1, 'partition': 'train'},
            ],
        })
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=data_revision)

    inventory_page = client.get(f'/studies/{study.pk}/data/').content.decode()
    assert 'Particiones por sesión' in inventory_page
    assert 'VAME oficial requiere sesiones completas' in inventory_page
    assert 'name="session_partition_0"' in inventory_page
    assert 'name="reserved_start_0"' in inventory_page
    assert 'name="reserved_stop_0"' in inventory_page

    response = client.post(f'/studies/{study.pk}/data/partitions/', {
        'session_partition_0': 'train', 'session_partition_1': 'test',
        'reserved_start_0': '1', 'reserved_stop_0': '2',
    })

    assert response.status_code == 302
    study.refresh_from_db()
    updated = study.dataset_revision
    data_revision.refresh_from_db()
    assert updated.pk != data_revision.pk
    assert updated.number == 2
    assert updated.config['session_partitions'] == {'s1': 'train', 's2': 'test'}
    assert updated.config['reserved_evaluation_ranges'] == {
        's1': {'start': 1, 'stop': 2}}
    assert [(row['partition'], row['reserved_evaluation'])
            for row in updated.inventory['preview']] == [
        ('train', False), ('test', True), ('test', False), ('test', False)]
    assert 'session_partitions' not in data_revision.config


def test_session_partition_form_rejects_incomplete_or_empty_reserved_ranges():
    from storm_studio.forms import SessionPartitionForm

    incomplete = SessionPartitionForm(
        {'session_partition_0': 'train', 'reserved_start_0': '2'}, sessions=['s1'])
    assert not incomplete.is_valid()

    empty = SessionPartitionForm(
        {'session_partition_0': 'train', 'reserved_start_0': '2',
         'reserved_stop_0': '2'}, sessions=['s1'])
    assert not empty.is_valid()


def test_inventory_uses_registered_connector_and_keeps_full_data_as_artifact(
        client, settings, tmp_path, monkeypatch):
    import json
    from storm.artifacts import ArtifactRef, FileArtifactStore
    from storm_studio.models import (Dataset, DatasetAsset, DatasetRevision, Job,
                                     Project, Revision, Study)
    from storm_studio.services import enqueue_inventory, perform, submit
    import storm_studio.dataset_inventory as dataset_inventory
    import storm_studio.services

    settings.WORKSPACE = tmp_path / 'workspace'
    settings.WORKSPACE.mkdir()
    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    pose_path = settings.WORKSPACE / 'data_sources' / 'pose.h5'
    pose_path.parent.mkdir()
    pose_path.write_bytes(b'pose')
    roi_path = settings.WORKSPACE / 'data_sources' / 'pose_rois.json'
    roi_path.write_text(json.dumps({
        'frame_shape': [800, 500],
        'rectangles': [{'name': 'arena', 'center': [100, 80],
                        'width': 40, 'height': 20, 'angle': 15}],
        'circles': [{'name': 'target', 'center': [300, 200], 'radius': 12}],
        'points': [{'name': 'feeder', 'center': [500, 250]}],
    }))
    video_path = settings.WORKSPACE / 'data_sources' / 'recording.mp4'
    video_path.write_bytes(b'video')
    monkeypatch.setattr(dataset_inventory, 'inspect_video_asset', lambda *_args, **_kwargs: {
        'frame_count': 5, 'fps': 30.0,
        'discontinuities': [{'after_frame': 3, 'before_frame': 4,
                             'delta_seconds': 0.066666667}],
        'discontinuity_status': 'gaps_detected',
    })
    labels_path = settings.WORKSPACE / 'data_sources' / 'labels.csv'
    labels_path.write_text('Frame,explore\n1,1\n')
    dataset = Dataset.objects.create(name='Mice')
    asset = DatasetAsset.objects.create(
        dataset=dataset, role='pose', original_name='pose.h5',
        relative_path='data_sources/pose.h5', sha256='a' * 64, size_bytes=4,
        session_id='pose')
    roi_asset = DatasetAsset.objects.create(
        dataset=dataset, role='roi', original_name='pose_rois.json',
        relative_path='data_sources/pose_rois.json', sha256='b' * 64,
        size_bytes=roi_path.stat().st_size, session_id='pose_rois')
    video_asset = DatasetAsset.objects.create(
        dataset=dataset, role='video', original_name='recording.mp4',
        relative_path='data_sources/recording.mp4', sha256='c' * 64,
        size_bytes=video_path.stat().st_size, session_id='recording')
    labels_asset = DatasetAsset.objects.create(
        dataset=dataset, role='labels', original_name='labels.csv',
        relative_path='data_sources/labels.csv', sha256='d' * 64,
        size_bytes=labels_path.stat().st_size, session_id='labels')
    data_revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='test_pose',
        asset_ids=[asset.pk, roi_asset.pk, video_asset.pk, labels_asset.pk],
        config={
            'fps': 30, 'session_partitions': {'animal-1': 'train'},
            'csv_frame_base': 1, 'pose_frame_base': 0,
            'label_frame_reference': 'video',
            'video_frame_offsets': {str(video_asset.pk): 3},
            'video_discontinuity_overrides_by_session': {'animal-1': [2]},
            'reserved_evaluation_ranges': {
                'animal-1': {'start': 0, 'stop': 1}},
            'asset_sessions': {
                str(asset.pk): 'animal-1', str(roi_asset.pk): 'global',
                str(video_asset.pk): 'animal-1', str(labels_asset.pk): 'animal-1',
            },
        },
        inventory={'assets': 4})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=data_revision)

    def connector(data):
        assert data['pose_paths'] == [str(pose_path)]
        assert data['pose_session_ids'] == ['animal-1']
        assert data['video_paths'] == [str(video_path)]
        assert data['labels_path'] == str(labels_path)
        assert data['train_session_ids'] == ['animal-1']
        assert data['csv_frame_base'] == 1
        assert data['pose_frame_base'] == 0
        assert data['label_frame_reference'] == 'video'
        assert data['video_frame_offsets_by_session'] == {'animal-1': 3}
        assert data['video_discontinuities_by_session'] == {'animal-1': [2]}
        assert data['reserved_evaluation_ranges_by_session'] == {
            'animal-1': {'start': 0, 'stop': 1}}
        return {
            'inputs': [[1.0, 2.0], [3.0, 4.0]], 'targets': [0, None],
            'observation_ids': ['animal-1:0', 'animal-1:1'], 'frames': [0, 1],
            'video_frames': [3, 4],
            'sessions': ['animal-1', 'animal-1'],
            'segments': ['animal-1:segment-0', 'animal-1:segment-1'],
            'partitions': ['test', 'train'], 'feature_names': ['nose_x', 'nose_y'],
            'evaluation_mask': [True, False], 'taxonomy': ['explore'],
            'reserved_evaluation': [True, False],
            'train': [1], 'validation': [], 'test': [0],
        }

    real_catalog = storm_studio.services.catalog
    test_catalog = real_catalog()
    test_catalog.connectors['test_pose'] = connector
    monkeypatch.setattr(storm_studio.services, 'catalog', lambda: test_catalog)
    job = enqueue_inventory(study, data_revision)

    perform(str(job.pk))
    model_revision = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'constrained_groups', 'connector': 'test_pose', 'config': {},
        'dataset_revision_id': data_revision.pk, 'data': {}, 'steps': [],
        'metrics': [], 'seed': 42})
    with pytest.raises(ValueError, match='[Rr]evisá la continuidad'):
        submit(model_revision)
    review_response = client.post(
        f'/studies/{study.pk}/data/video-timeline-review/', {
            'reviewer': 'Test investigator',
            'reason': 'El salto está alineado con un corte de grabación.',
            'confirm_review': 'on',
        })
    assert review_response.status_code == 302
    model_job = submit(model_revision)
    perform(str(model_job.pk))
    monkeypatch.setattr(storm_studio.services, 'catalog', real_catalog)

    job.refresh_from_db()
    model_job.refresh_from_db()
    data_revision.refresh_from_db()
    assert job.operation == 'inventory'
    assert job.status == 'completed'
    assert model_job.status == 'completed', model_job.error
    assert data_revision.status == 'ready'
    assert data_revision.inventory['preview_strategy'] == 'balanced_sessions_segments_v1'
    assert data_revision.inventory['pose_preview_store']['row_count'] == 2
    assert data_revision.inventory['frame_count'] == 2
    assert [row['video_frame'] for row in data_revision.inventory['preview']] == [3, 4]
    assert data_revision.inventory['reserved_frames'] == 1
    assert [row['reserved_evaluation'] for row in data_revision.inventory['preview']] == [
        True, False]
    assert data_revision.inventory['feature_names'] == ['nose_x', 'nose_y']
    assert data_revision.inventory['taxonomy'] == ['explore']
    assert [(row['target'], row['evaluation_mask'])
            for row in data_revision.inventory['preview']] == [(0, True), (None, False)]
    label_page = client.get(
        f'/studies/{study.pk}/data/pose-preview/{data_revision.pk}/',
        {'session_id': 'animal-1', 'offset': '0'})
    assert label_page.json()['taxonomy'] == ['explore']
    assert [(row['target'], row['evaluation_mask'])
            for row in label_page.json()['rows']] == [(0, True), (None, False)]
    assert data_revision.inventory['video_timeline'][0]['discontinuity_status'] == 'manual_boundaries_set'
    assert data_revision.inventory['video_timeline'][0]['pose_frame_count'] == 2
    assert data_revision.inventory['video_timeline'][0]['discontinuities'][0]['before_frame'] == 2
    assert data_revision.inventory['video_timeline'][0]['detected_discontinuities'][0]['before_frame'] == 4
    assert data_revision.inventory['video_discontinuities_by_session'] == {'animal-1': [2]}
    roi = data_revision.inventory['roi'][0]
    assert roi['frame_shape'] == [800, 500]
    assert roi['session_id'] == 'global'
    assert roi['rectangles'][0]['name'] == 'arena'
    assert roi['circles'][0]['radius'] == 12
    assert roi['points'][0]['center'] == [500, 250]
    stored = FileArtifactStore(settings.ARTIFACT_ROOT).load(
        ArtifactRef.from_dict(data_revision.artifact_ref))
    assert stored['inputs'] == [[1.0, 2.0], [3.0, 4.0]]
    assert model_job.result['resolved_data']['inputs'] == [[1.0, 2.0], [3.0, 4.0]]
    inventory_page = client.get(f'/studies/{study.pk}/data/')
    assert inventory_page.status_code == 200
    assert 'Continuidad y alineación del video'.encode() in inventory_page.content
    assert 'Antes del frame 2'.encode() in inventory_page.content
    assert 'Ver detección automática'.encode() in inventory_page.content
    assert 'Antes del frame 4'.encode() in inventory_page.content
    jobs_page = client.get(f'/studies/{study.pk}/jobs/').content.decode()
    assert 'Inventario de datos' in jobs_page
    assert 'Adoptar candidato' not in jobs_page.split('Inventario de datos', 1)[1]


def test_preview_sample_covers_sessions_and_segments_instead_of_only_early_rows():
    from storm_studio.data_preparation import _inventory
    from storm_studio.preview_sampling import sample_preview_indices

    sessions = []
    segments = []
    for session_index in range(86):
        for segment_index in range(2):
            for _ in range(3):
                sessions.append(f'session-{session_index:02d}')
                segments.append(f'segment-{segment_index}')

    selected = sample_preview_indices(sessions, segments)

    assert len(selected) == 256
    assert selected == sorted(set(selected))
    assert {sessions[index] for index in selected} == set(sessions)
    assert {(sessions[index], segments[index]) for index in selected} == set(
        zip(sessions, segments))

    prepared_inventory = _inventory({
        'inputs': [[index] for index in range(len(sessions))],
        'sessions': sessions,
        'segments': segments,
        'frames': list(range(len(sessions))),
        'feature_names': ['x'],
    }, {}, 'test-fingerprint')

    assert [row['index'] for row in prepared_inventory['preview']] == selected
    assert {row['session_id'] for row in prepared_inventory['preview']} == set(sessions)
    assert prepared_inventory['preview_strategy'] == 'balanced_sessions_segments_v1'


@pytest.mark.django_db
def test_pose_preview_endpoint_pages_any_frame_without_embedding_all_rows(
        client, settings, tmp_path):
    from storm_studio.models import Dataset, DatasetRevision, Project, Study
    from storm_studio.pose_preview import write_pose_preview_store

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    row_count = 1200
    data = {
        'inputs': [[float(index), float(index * 2)] for index in range(row_count)],
        'feature_names': ['nose_x', 'nose_y'],
        'frames': list(range(row_count)),
        'video_frames': [index + 500 for index in range(row_count)],
        'sessions': ['mouse-a'] * row_count,
        'segments': ['clip-1' if index < 600 else 'clip-2'
                     for index in range(row_count)],
        'observation_ids': [f'mouse-a:{index}' for index in range(row_count)],
        'targets': [1 if index == 1100 else None for index in range(row_count)],
        'evaluation_mask': [index == 1100 for index in range(row_count)],
        'taxonomy': ['walk', 'grooming'],
    }
    preview_store = write_pose_preview_store(
        data, root=settings.ARTIFACT_ROOT, artifact_id='pose-preview-test', chunk_size=128)
    dataset = Dataset.objects.create(name='Full pose preview')
    revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'frame_count': row_count, 'feature_names': data['feature_names'],
                   'preview': [], 'pose_preview_store': preview_store})
    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Full timeline',
        dataset_revision=revision)

    page = client.get(
        f'/studies/{study.pk}/data/pose-preview/{revision.pk}/',
        {'session_id': 'mouse-a', 'offset': '896'})
    assert page.status_code == 200
    assert len(page.json()['rows']) == 128
    assert page.json()['rows'][0]['frame'] == 896
    assert page.json()['rows'][-1]['frame'] == 1023
    assert page.json()['rows'][0]['segment'] == 'clip-2'

    selected = client.get(
        f'/studies/{study.pk}/data/pose-preview/{revision.pk}/',
        {'session_id': 'mouse-a', 'frame': '1100'})
    assert selected.status_code == 200
    assert selected.json()['selected_index'] == 1100
    assert any(row['frame'] == 1100 for row in selected.json()['rows'])
    selected_row = next(row for row in selected.json()['rows'] if row['frame'] == 1100)
    assert selected.json()['taxonomy'] == ['walk', 'grooming']
    assert selected_row['target'] == 1
    assert selected_row['evaluation_mask'] is True
    assert selected.json()['rows'][0]['evaluation_mask'] is False

    video_selected = client.get(
        f'/studies/{study.pk}/data/pose-preview/{revision.pk}/',
        {'session_id': 'mouse-a', 'frame': '1600', 'frame_field': 'video_frame'})
    assert video_selected.status_code == 200
    assert video_selected.json()['selected_index'] == 1100
    assert any(row['video_frame'] == 1600 for row in video_selected.json()['rows'])

    other_dataset = Dataset.objects.create(name='Unrelated pose dataset')
    unrelated_revision = DatasetRevision.objects.create(
        dataset=other_dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'pose_preview_store': preview_store})
    denied = client.get(
        f'/studies/{study.pk}/data/pose-preview/{unrelated_revision.pk}/',
        {'session_id': 'mouse-a', 'offset': '0'})
    assert denied.status_code == 404


@pytest.mark.django_db
def test_pose_preview_endpoint_aligns_model_outputs_by_original_observation(
        client, settings, tmp_path):
    from storm_studio.models import (
        Dataset, DatasetRevision, Job, Project, Revision, Study,
    )
    from storm_studio.pose_preview import write_pose_preview_store

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    data = {
        'inputs': [[0, 10], [1, 11], [2, 12]],
        'feature_names': ['nose_x', 'nose_y'],
        'frames': [0, 1, 2], 'video_frames': [20, 21, 22],
        'sessions': ['mouse-a'] * 3,
        'segments': ['clip-1'] * 3,
        'observation_ids': ['mouse-a:0', 'mouse-a:1', 'mouse-a:2'],
        'targets': [0, 1, None], 'taxonomy': ['walk', 'grooming'],
    }
    store = write_pose_preview_store(
        data, root=settings.ARTIFACT_ROOT, artifact_id='pose-model-output-preview')
    dataset = Dataset.objects.create(name='Pose and model outputs')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'pose_preview_store': store,
                   'source_fingerprint': 'fingerprint-v1',
                   'taxonomy': data['taxonomy']})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'dataset_revision_id': source.pk,
    })
    job = Job.objects.create(revision=plan, status='completed', result={
        'model': 'vame_native',
        'spec': {'dataset_revision_id': source.pk},
        'data_fingerprint': 'fingerprint-v1',
        'capabilities': ['group'],
        'resolved_data': {
            **data,
            'partitions': ['test'] * 3,
            'reserved_evaluation': [True] * 3,
        },
        'indices': [0, 2], 'predictions': [7, 9],
        'prediction_mask': [True, False],
        'output_metadata': {'semantics': 'Local motif IDs'},
    })
    annotation = Revision.objects.create(study=study, kind='annotations', parent=plan,
                                         payload={
        'job': str(job.pk), 'data_fingerprint': 'fingerprint-v1',
        'taxonomy': ['walk', 'grooming'],
        'intervals': [{'start': 0, 'stop': 2, 'label': 'walk'}],
        'mapping': {'7': 'walk'}, 'author': 'Researcher',
        'reason': 'Reviewed against the video.',
    })

    response = client.get(
        f'/studies/{study.pk}/data/pose-preview/{source.pk}/',
        {'session_id': 'mouse-a', 'offset': 0, 'limit': 3,
         # UUIDField accepts compact hex form too; lookup must use its
         # normalized representation after parsing.
         'predictions': job.pk.hex})

    assert response.status_code == 200
    rows = response.json()['rows']
    assert rows[0]['model_predictions'] == [{
        'job_id': str(job.pk), 'model': 'vame_native', 'prediction': 7,
        'valid': True, 'semantics': 'Local motif IDs',
    }]
    assert rows[1].get('model_predictions', []) == []
    assert rows[0]['run_annotations'] == [{
        'job_id': str(job.pk), 'model': 'vame_native',
        'revision_id': str(annotation.pk), 'interval_labels': ['walk'],
        'group_interpretation': 'walk', 'author': 'Researcher',
        'reason': 'Reviewed against the video.',
    }]
    assert rows[1]['run_annotations'][0]['interval_labels'] == ['walk']
    assert rows[2]['model_predictions'] == [{
        'job_id': str(job.pk), 'model': 'vame_native', 'prediction': 9,
        'valid': False, 'semantics': 'Local motif IDs',
    }]
    Revision.objects.create(study=study, kind='annotations', parent=annotation,
                            payload={**annotation.payload,
                                     'data_fingerprint': 'different-fingerprint'})
    stale_annotation = client.get(
        f'/studies/{study.pk}/data/pose-preview/{source.pk}/',
        {'session_id': 'mouse-a', 'offset': 0, 'limit': 3,
         'predictions': str(job.pk)})
    assert stale_annotation.status_code == 200
    assert all(not row.get('run_annotations') for row in stale_annotation.json()['rows'])
    page = client.get(
        f'/studies/{study.pk}/data/', {'predictions': str(job.pk)})
    assert page.status_code == 200
    assert 'pose-prediction-selection' in page.content.decode()
    assert f'value="{job.pk}" checked' in page.content.decode()

    unrelated_dataset = Dataset.objects.create(name='Other pose source')
    unrelated_source = DatasetRevision.objects.create(
        dataset=unrelated_dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'pose_preview_store': store,
                   'source_fingerprint': 'other-fingerprint',
                   'taxonomy': data['taxonomy']})
    unrelated_plan = Revision.objects.create(study=study, kind='plan', payload={
        'dataset_revision_id': unrelated_source.pk,
    })
    unrelated_job = Job.objects.create(
        revision=unrelated_plan, status='completed', result={
            'model': 'incorrect-source',
            'spec': {'dataset_revision_id': unrelated_source.pk},
            'resolved_data': {**data, 'partitions': ['test'] * 3,
                              'reserved_evaluation': [True] * 3},
            'indices': [0], 'predictions': [99],
        })
    rejected = client.get(
        f'/studies/{study.pk}/data/pose-preview/{source.pk}/',
        {'session_id': 'mouse-a', 'offset': 0, 'limit': 3,
         'predictions': str(unrelated_job.pk)})
    assert rejected.status_code == 409
    assert 'verified alignment' in rejected.json()['error']

    misaligned_plan = Revision.objects.create(study=study, kind='plan', payload={
        'dataset_revision_id': source.pk,
    })
    misaligned_job = Job.objects.create(
        revision=misaligned_plan, status='completed', result={
            'model': 'misaligned',
            'spec': {'dataset_revision_id': source.pk},
            'resolved_data': {**data, 'frames': [100, 101, 102],
                              'partitions': ['test'] * 3,
                              'reserved_evaluation': [True] * 3},
            'indices': [0], 'predictions': [99],
        })
    misaligned = client.get(
        f'/studies/{study.pk}/data/pose-preview/{source.pk}/',
        {'session_id': 'mouse-a', 'offset': 0, 'limit': 3,
         'predictions': str(misaligned_job.pk)})
    assert misaligned.status_code == 200
    assert all(not row.get('model_predictions') for row in misaligned.json()['rows'])


def test_video_asset_inventory_detects_frame_timestamp_discontinuities(monkeypatch, tmp_path):
    import json
    from storm_studio import dataset_inventory

    video = tmp_path / 'gapped.mp4'
    video.write_bytes(b'fixture')
    probe = {
        'streams': [{'nb_frames': '4', 'r_frame_rate': '30/1'}],
        'frames': [{'best_effort_timestamp_time': value}
                   for value in ('0.000000', '0.033333', '0.100000', '0.133333')],
    }
    monkeypatch.setattr(dataset_inventory.subprocess, 'check_output',
                        lambda *_args, **_kwargs: json.dumps(probe))

    result = dataset_inventory.inspect_video_asset(video, fallback_fps=30)

    assert result['frame_count'] == 4
    assert result['fps'] == 30
    assert result['discontinuity_status'] == 'gaps_detected'
    assert len(result['discontinuities']) == 1
    assert result['discontinuities'][0]['after_frame'] == 1
    assert result['discontinuities'][0]['before_frame'] == 2
    assert result['discontinuities'][0]['delta_seconds'] == pytest.approx(0.066667)


def test_inventory_button_queues_independent_dataset_job(client):
    from storm_studio.models import Dataset, DatasetRevision, Job, Project, Study

    dataset = Dataset.objects.create(name='Mice')
    data_revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', inventory={'assets': 1})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=data_revision)

    data_page = client.get(f'/studies/{study.pk}/data/').content.decode()
    assert 'Inspeccionar contenido' in data_page
    assert 'name="pose_files"' in data_page

    response = client.post(f'/studies/{study.pk}/data/inventory/')

    assert response.status_code == 302
    job = Job.objects.get()
    data_revision.refresh_from_db()
    assert job.operation == 'inventory'
    assert job.revision.kind == 'dataset_inventory'
    assert data_revision.status == 'queued'
    assert not study.revision_set.filter(kind='plan').exists()


@pytest.mark.parametrize('timeline_status, discontinuities', [
    ('gaps_detected', [{'after_frame': 8, 'before_frame': 9, 'delta_seconds': 0.2}]),
    ('unknown_requires_review', None),
])
def test_dataset_with_unreviewed_video_timeline_requires_decision(
        client, timeline_status, discontinuities):
    from storm_studio.models import Dataset, DatasetRevision, Job, Project, Revision, Study
    from storm_studio.services import enqueue_preparation, submit

    timeline = [{
        'file': 'mouse.mp4', 'session_id': 'mouse', 'frame_count': 20,
        'fps': 30.0, 'discontinuity_status': timeline_status,
        'discontinuities': discontinuities,
    }]
    dataset = Dataset.objects.create(name='Gapped video')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'source_fingerprint': 'source-hash', 'video_timeline': timeline},
        artifact_ref={'path': 'complete-fixture'},
    )
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)
    recipe = Revision.objects.create(study=study, kind='preparation', payload={
        'dataset_revision_id': source.pk, 'steps': [],
    })

    with pytest.raises(ValueError, match='[Rr]evisá la continuidad'):
        enqueue_preparation(study, recipe)
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'operation': 'train', 'model': 'identity',
        'dataset_revision_id': source.pk,
    })
    with pytest.raises(ValueError, match='[Rr]evisá la continuidad'):
        submit(plan)
    preview = client.post(f'/studies/{study.pk}/pipeline-preview/', {
        'dataset_revision_id': str(source.pk), 'steps': '[]',
    })
    assert preview.status_code == 400
    assert 'Revisá la continuidad' in preview.json()['error']
    queued = client.post(f'/plans/{plan.pk}/run/')
    assert queued.status_code == 302
    assert not Job.objects.filter(revision=plan).exists()

    data_page = client.get(f'/studies/{study.pk}/data/').content.decode()
    assert 'Decisión del investigador' in data_page
    assert 'Guardar revisión de continuidad' in data_page
    if timeline_status == 'unknown_requires_review':
        assert 'aceptar implica tratarlos como continuos' in data_page
    unconfirmed = client.post(f'/studies/{study.pk}/data/video-timeline-review/', {
        'reviewer': 'Investigadora', 'reason': 'Revisión manual.',
    })
    assert unconfirmed.status_code == 302
    assert not study.revision_set.filter(kind='video_timeline_review').exists()
    response = client.post(f'/studies/{study.pk}/data/video-timeline-review/', {
        'reviewer': 'Investigadora', 'reason': 'El salto coincide con un corte de cámara.',
        'confirm_review': 'on',
    })

    assert response.status_code == 302
    decision = study.revision_set.get(kind='video_timeline_review')
    assert decision.payload['dataset_revision_id'] == source.pk
    assert decision.payload['video_timeline'][0]['discontinuities'] == discontinuities
    assert decision.payload['reviewer'] == 'Investigadora'
    assert decision.payload['reason'] == 'El salto coincide con un corte de cámara.'
    assert enqueue_preparation(study, recipe).operation == 'prepare'
    assert submit(plan).operation == 'train'
    assert client.post(f'/plans/{plan.pk}/run/').status_code == 302

    reviewed_page = client.get(f'/studies/{study.pk}/data/').content.decode()
    assert 'Revisión guardada' in reviewed_page
    assert 'El salto coincide con un corte de cámara.' in reviewed_page


def test_registered_video_with_legacy_inventory_requires_manual_continuity_review():
    from storm_studio.models import Dataset, DatasetAsset, DatasetRevision, Project, Study
    from storm_studio.video_timeline_reviews import review_state

    dataset = Dataset.objects.create(name='Legacy video dataset')
    video = DatasetAsset.objects.create(
        dataset=dataset, role='video', original_name='legacy.avi',
        relative_path='uploads/legacy.avi', sha256='a' * 64, size_bytes=10,
        session_id='mouse',
    )
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', asset_ids=[video.pk],
        status='ready', inventory={'assets': 1},
    )
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)

    state = review_state(study, source)

    assert state['required'] is True
    assert state['has_unknown'] is True
    assert state['items'][0]['file'] == 'legacy.avi'
    verified = DatasetRevision.objects.create(
        dataset=dataset, number=2, connector='dlc_h5', asset_ids=[video.pk],
        status='ready', inventory={'video_timeline': [{
            'file': 'legacy.avi', 'session_id': 'mouse', 'frame_count': 30,
            'fps': 30.0, 'discontinuity_status': 'verified_contiguous',
            'discontinuities': [],
        }]},
    )
    assert review_state(study, verified)['required'] is False


def test_video_timeline_review_reuses_matching_sources_but_not_changed_boundaries():
    from storm_studio.models import Dataset, DatasetAsset, DatasetRevision, Project, Study
    from storm_studio.video_timeline_reviews import record_review, review_state

    dataset = Dataset.objects.create(name='Versioned video timeline')
    video = DatasetAsset.objects.create(
        dataset=dataset, role='video', original_name='mouse.mp4',
        relative_path='uploads/mouse.mp4', sha256='c' * 64, size_bytes=20,
        session_id='mouse',
    )
    timeline = [{
        'file': 'mouse.mp4', 'session_id': 'mouse', 'frame_count': 20,
        'pose_frame_count': 20, 'fps': 30.0,
        'discontinuity_status': 'gaps_detected',
        'discontinuities': [{'after_frame': 8, 'before_frame': 9,
                             'delta_seconds': 0.2}],
    }]
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', asset_ids=[video.pk],
        status='ready', config={'fps': 30, 'asset_sessions': {str(video.pk): 'mouse'}},
        inventory={'video_timeline': timeline},
    )
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)
    record_review(study, source, reviewer='Investigator', reason='Reviewed cut.')
    same_timeline = DatasetRevision.objects.create(
        dataset=dataset, number=2, connector='dlc_h5', asset_ids=[video.pk],
        status='ready', config={
            'fps': 30, 'asset_sessions': {str(video.pk): 'mouse'},
            'session_partitions': {'mouse': 'test'},
        }, inventory={'video_timeline': timeline},
    )
    changed_timeline = DatasetRevision.objects.create(
        dataset=dataset, number=3, connector='dlc_h5', asset_ids=[video.pk],
        status='ready', config={'fps': 30, 'asset_sessions': {str(video.pk): 'mouse'}},
        inventory={'video_timeline': [{**timeline[0], 'discontinuities': [
            {'after_frame': 7, 'before_frame': 8, 'delta_seconds': 0.2}]}]},
    )
    prepared = DatasetRevision.objects.create(
        dataset=dataset, number=4, connector='prepared_artifact', asset_ids=[video.pk],
        status='ready', config={'source_dataset_revision_id': source.pk},
        inventory={},
    )

    assert review_state(study, same_timeline)['accepted'] is True
    assert review_state(study, changed_timeline)['accepted'] is False
    assert review_state(study, prepared)['accepted'] is True


def test_manual_video_boundary_edit_creates_new_dataset_revision_and_reinspects(client):
    from storm_studio.models import (Dataset, DatasetAsset, DatasetRevision, Job,
                                     Project, Revision, Study)

    dataset = Dataset.objects.create(name='Corrected video timeline')
    pose = DatasetAsset.objects.create(
        dataset=dataset, role='pose', original_name='mouse.h5',
        relative_path='data/mouse.h5', sha256='a' * 64, size_bytes=100,
        session_id='mouse',
    )
    video = DatasetAsset.objects.create(
        dataset=dataset, role='video', original_name='mouse.mp4',
        relative_path='data/mouse.mp4', sha256='b' * 64, size_bytes=200,
        session_id='mouse',
    )
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5',
        asset_ids=[pose.pk, video.pk], config={
            'fps': 30, 'asset_sessions': {str(pose.pk): 'mouse', str(video.pk): 'mouse'}},
        status='ready', artifact_ref={'artifact_id': 'source'}, inventory={
            'assets': 2, 'roles': {'pose': 1, 'video': 1}, 'bytes': 300,
            'source_fingerprint': 'old-fingerprint',
            'video_timeline': [{
                'file': 'mouse.mp4', 'session_id': 'mouse', 'frame_count': 20,
                'pose_frame_count': 20, 'fps': 30.0,
                'discontinuity_status': 'gaps_detected',
                'discontinuity_count': 1,
                'discontinuities': [{'after_frame': 8, 'before_frame': 9,
                                     'delta_seconds': 0.2}],
            }],
        },
    )
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)

    data_page = client.get(f'/studies/{study.pk}/data/').content.decode()
    assert 'Definir límites entre segmentos' in data_page
    assert 'name="boundaries_0" value="9"' in data_page

    invalid = client.post(f'/studies/{study.pk}/data/video-discontinuities/', {
        'boundaries_0': '0, 20', 'reviewer': 'Investigator', 'reason': 'Review.',
    })
    assert invalid.status_code == 302
    study.refresh_from_db()
    assert study.dataset_revision_id == source.pk
    assert not Job.objects.filter(operation='inventory').exists()

    response = client.post(f'/studies/{study.pk}/data/video-discontinuities/', {
        'boundaries_0': '5, 14', 'reviewer': 'Investigator',
        'reason': 'Los timestamps de la cámara muestran un corte.',
    })

    assert response.status_code == 302
    study.refresh_from_db()
    source.refresh_from_db()
    current = study.dataset_revision
    assert current.pk != source.pk
    assert current.status == 'queued'
    assert current.number == 2
    assert current.config['video_discontinuity_overrides_by_session'] == {'mouse': [5, 14]}
    assert source.status == 'ready'
    assert source.artifact_ref == {'artifact_id': 'source'}
    job = Job.objects.get(operation='inventory')
    assert job.revision.payload['dataset_revision'] == current.pk
    edit = Revision.objects.get(study=study, kind='video_discontinuity_edit')
    assert edit.payload['previous_dataset_revision_id'] == source.pk
    assert edit.payload['dataset_revision_id'] == current.pk
    assert edit.payload['video_discontinuity_overrides_by_session'] == {'mouse': [5, 14]}
    assert edit.payload['author'] == 'Investigator'
    assert edit.payload['reason'] == 'Los timestamps de la cámara muestran un corte.'


@pytest.mark.parametrize('operation', ['train', 'prepare'])
def test_worker_rechecks_timeline_review_before_running_queued_work(
        operation, settings, tmp_path):
    from storm_studio.models import Dataset, DatasetRevision, Job, Project, Revision, Study
    from storm_studio.services import perform

    dataset = Dataset.objects.create(name='Queued gapped dataset')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'video_timeline': [{
            'file': 'mouse.mp4', 'session_id': 'mouse', 'fps': 30.0,
            'discontinuity_status': 'gaps_detected',
            'discontinuities': [{'after_frame': 8, 'before_frame': 9,
                                 'delta_seconds': 0.2}],
        }]},
        artifact_ref={'path': 'unread-fixture'},
    )
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S',
                                 dataset_revision=source)
    revision = Revision.objects.create(
        study=study, kind='preparation' if operation == 'prepare' else 'plan',
        payload={'dataset_revision_id': source.pk, 'steps': [], 'data': {},
                 'model': 'identity', 'config': {}, 'seed': 42},
    )
    job = Job.objects.create(revision=revision, operation=operation)
    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'

    perform(str(job.pk))

    job.refresh_from_db()
    assert job.status == 'failed'
    assert 'Revisá la continuidad' in job.error


def test_comparison_hides_joint_ranking_when_model_label_meanings_differ(client):
    from storm_studio.models import Job, Project, Revision, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Compare meanings')
    results = []
    for index, meaning in enumerate(('object approach', 'object exploration')):
        revision = Revision.objects.create(study=study, kind='plan', payload={
            'model': f'supervised_{index}', 'config': {}, 'data': {},
        })
        result = {
            'data_fingerprint': 'same-dataset', 'partition': 'test',
            'indices': [0, 1], 'metric_indices': [0, 1],
            'resolved_data': {'inputs': [[0.0], [1.0]], 'targets': [0, 1],
                              'taxonomy': ['behavior_a', 'behavior_b']},
            'predictions': [0, 1], 'metrics': {'accuracy': 0.5},
            'metric_definitions': [{'name': 'accuracy', 'version': '1',
                                    'direction': 'maximize'}],
            'model_version': '1',
            'output_metadata': {'task': 'binary_classification',
                                'output_meaning': meaning},
        }
        results.append(Job.objects.create(revision=revision, status='completed', result=result))

    response = client.get(
        f'/studies/{study.pk}/compare/?jobs={results[0].pk}&jobs={results[1].pk}')

    assert response.status_code == 200
    assert 'output task or category meanings differ' in response.content.decode()
    assert 'Comparación gráfica de métricas' not in response.content.decode()


def test_comparison_shows_group_stability_without_claiming_human_accuracy(client):
    from storm_studio.models import Job, Project, Revision, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Group stability')
    jobs = []
    for index, predictions in enumerate(([0, 0, 1, 1], [9, 9, 4, 4])):
        revision = Revision.objects.create(study=study, kind='plan', payload={
            'model': 'vame_native', 'config': {}, 'data': {},
        })
        jobs.append(Job.objects.create(revision=revision, status='completed', result={
            'model': 'vame_native', 'model_version': '1', 'capabilities': ['group'],
            'data_fingerprint': 'same-dataset', 'partition': 'training groups',
            'indices': [0, 1, 2, 3], 'predictions': predictions,
            'prediction_mask': [True] * 4, 'metrics': {}, 'metric_definitions': [],
            'output_metadata': {'semantics': 'model-local VAME state IDs'},
            'resolved_data_summary': {
                'observation_count': 4, 'taxonomy': None,
                'targets_available_for_metrics': False,
            },
        }))

    response = client.get(
        f'/studies/{study.pk}/compare/?jobs={jobs[0].pk}&jobs={jobs[1].pk}')

    assert response.status_code == 200
    assert response.context['section'] == 'compare'
    assert response.context['comparison_requested'] is True
    assert response.context['group_stability']['value'] == 1.0
    content = response.content.decode()
    assert 'Estabilidad entre corridas · ARI 1,0' in content
    assert 'No mide concordancia humana' in content
    assert 'Inspección solamente; no se habilita ranking conjunto.' in content


def test_inference_only_dataset_rejects_training_submission():
    from storm_studio import services
    from storm_studio.models import (
        Dataset, DatasetRevision, Job, Project, Revision, Study,
    )

    dataset = Dataset.objects.create(name='NOR test benchmark')
    data_revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        config={'inference_only': True}, inventory={'sessions': []})
    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Benchmark only',
        dataset_revision=data_revision)
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'operation': 'train', 'model': 'identity', 'config': {}, 'steps': [],
        'data': {}, 'dataset_revision_id': data_revision.pk,
    })

    with pytest.raises(ValueError, match='inference-only'):
        services.submit(plan)

    assert not Job.objects.filter(revision=plan).exists()


def test_inference_only_dataset_requires_saved_group_model_for_inference(monkeypatch):
    from storm.suite import Component, default_catalog
    from storm_studio import services
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study

    catalog = default_catalog()
    catalog.register(Component('test.group_infer', object, ('group', 'infer'), {}))
    monkeypatch.setattr(services, 'catalog', lambda: catalog)
    dataset = Dataset.objects.create(name='NOR test benchmark')
    data_revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        config={'inference_only': True}, inventory={'sessions': []})
    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Benchmark only',
        dataset_revision=data_revision)
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'operation': 'infer', 'model': 'test.group_infer', 'config': {}, 'steps': [],
        'data': {}, 'dataset_revision_id': data_revision.pk,
    })

    with pytest.raises(ValueError, match='completed model'):
        services.submit(plan)


def test_studio_imports_registered_inference_only_dataset_preset(
        client, settings, tmp_path, monkeypatch):
    from storm.suite import default_catalog
    from storm_studio.models import DatasetAsset, Project, Study

    source_root = tmp_path / 'preset'
    source_root.mkdir()
    pose_source = source_root / 'NOR_TS_01.h5'
    pose_source.write_bytes(b'pose fixture')
    catalog = default_catalog()
    catalog.connectors['dlc_h5'] = lambda data: data
    catalog.register_dataset_preset({
        'id': 'example.inference_only', 'label': 'Inference only sample',
        'description': 'Use registered test data only for model inference.',
        'source_root': str(source_root), 'connector': 'dlc_h5',
        'assets': [{'role': 'pose', 'source_path': str(pose_source),
                    'session_id': 'NOR_TS_01'}],
        'config': {'inference_only': True, 'fps': 25,
                   'session_partitions': {'NOR_TS_01': 'test'}},
    })
    monkeypatch.setattr('storm_studio.views.services.catalog', lambda: catalog)
    settings.WORKSPACE = tmp_path / 'workspace'
    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Preset import')

    page = client.get(f'/studies/{study.pk}/data/')
    assert page.status_code == 200
    assert b'Benchmarks de ejemplo' in page.content
    assert b'Inference only sample' in page.content

    response = client.post(f'/studies/{study.pk}/data/presets/', {
        'preset_id': 'example.inference_only',
    })

    assert response.status_code == 302
    study.refresh_from_db()
    revision = study.dataset_revision
    assert revision.connector == 'dlc_h5'
    assert revision.config['inference_only'] is True
    asset = DatasetAsset.objects.get(dataset=revision.dataset, role='pose')
    assert asset.session_id == 'NOR_TS_01'
    assert (settings.WORKSPACE / asset.relative_path).read_bytes() == b'pose fixture'
    model_page = client.get(f'/studies/{study.pk}/models/')
    assert model_page.status_code == 200
    assert b'Evaluar el benchmark protegido' in model_page.content
    assert b'no puede entrenar ni ajustar modelos' in model_page.content


def test_connector_input_preserves_inference_only_policy_and_label_mapping(tmp_path):
    from storm_studio.dataset_inventory import connector_input
    from storm_studio.models import Dataset, DatasetAsset, DatasetRevision

    pose_path = tmp_path / 'data_sources' / 'pose.h5'
    labels_path = tmp_path / 'data_sources' / 'labels.csv'
    pose_path.parent.mkdir()
    pose_path.write_bytes(b'pose')
    labels_path.write_text('Frame,object_a,object_b\n', encoding='utf-8')
    dataset = Dataset.objects.create(name='NOR benchmark')
    pose = DatasetAsset.objects.create(
        dataset=dataset, role='pose', original_name='NOR_TS_01.h5',
        relative_path=pose_path.relative_to(tmp_path).as_posix(),
        sha256='a' * 64, size_bytes=4, session_id='NOR_TS_01')
    labels = DatasetAsset.objects.create(
        dataset=dataset, role='labels', original_name='labels.csv',
        relative_path=labels_path.relative_to(tmp_path).as_posix(),
        sha256='b' * 64, size_bytes=27, session_id='NOR_TS_01')
    revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', asset_ids=[pose.pk, labels.pk],
        config={
            'inference_only': True,
            'asset_sessions': {str(pose.pk): 'NOR_TS_01', str(labels.pk): 'NOR_TS_01'},
            'session_partitions': {'NOR_TS_01': 'test'},
            'canonical_taxonomy': ['Known', 'Novel'],
            'label_mapping_by_session': {
                'NOR_TS_01': {'object_a': 'Novel', 'object_b': 'Known'},
            },
        })

    data = connector_input(revision.pk, workspace=tmp_path)

    assert data['inference_only'] is True
    assert data['test_session_ids'] == ['NOR_TS_01']
    assert data['train_session_ids'] == []
    assert data['canonical_taxonomy'] == ['Known', 'Novel']
    assert data['label_mapping_by_session']['NOR_TS_01'] == {
        'object_a': 'Novel', 'object_b': 'Known'}


def test_progress_events_do_not_inherit_previous_component_duration():
    from storm_studio import services
    from storm_studio.models import Job, Project, Revision, Study
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Trace')
    plan = Revision.objects.create(study=study, kind='plan', payload={})
    job = Job.objects.create(revision=plan, status='running')
    services._record_progress(job.pk, {'phase': 'preparing', 'label': 'Step completed',
                                      'component': 'First', 'span_id': 'first',
                                      'status': 'completed', 'duration_seconds': 8,
                                      'batch_step': 17, 'batch_total': 17})
    services._record_progress(job.pk, {'phase': 'preparing', 'label': 'Validating alignment'})
    job.refresh_from_db()
    assert 'duration_seconds' not in job.progress['trace'][-1]
    assert 'component' not in job.progress['trace'][-1]
    assert 'batch_step' not in job.progress['trace'][-1]
    services._record_progress(job.pk, {'phase': 'preparing', 'label': 'Step started',
                                      'component': 'Second', 'span_id': 'second', 'status': 'started'})
    job.refresh_from_db()
    assert 'duration_seconds' not in job.progress['trace'][-1]


def test_worker_abort_records_failure_context_and_clears_live_batch_eta():
    from storm_studio.management.commands.worker import record_worker_exit
    from storm_studio.models import Job, Project, Revision, Study
    study = Study.objects.create(project=Project.objects.create(name='P'), name='GPU failure')
    plan = Revision.objects.create(study=study, kind='plan', payload={})
    job = Job.objects.create(revision=plan, status='running', progress={
        'phase': 'training', 'label': 'Entrenando', 'stage_index': 3, 'stage_total': 5,
        'epoch': 1, 'batch_step': 15, 'batch_total': 4325, 'batch_eta_seconds': 1247,
        'throughput': 885.4, 'device': 'cuda'},
        logs=[{'timestamp': '', 'message': 'HW Exception reason :GPU Hang'}])
    record_worker_exit(job.pk, -6)
    job.refresh_from_db()
    assert job.status == 'failed'
    assert job.progress['phase'] == 'failed'
    assert job.progress['failure_context']['batch_step'] == 15
    assert job.progress['failure_context']['signal'] == 'SIGABRT'
    assert 'GPU Hang' in job.error
    assert 'batch_eta_seconds' not in job.progress
    assert 'batch_step' not in job.progress


def test_gpu_hang_card_explains_retry_keeps_the_failed_backend(client):
    from storm_studio.models import Job, Project, Revision, Study
    study = Study.objects.create(project=Project.objects.create(name='P'), name='GPU failure guidance')
    revision = Revision.objects.create(study=study, kind='plan', payload={'model': 'vame_native', 'config': {'device': 'cuda'}})
    job = Job.objects.create(revision=revision, status='failed', error='GPU Hang: SIGABRT')
    response = client.get(f'/studies/{study.pk}/jobs/')
    assert 'Reintentar conserva la configuración' in response.content.decode()
    assert 'rnn_backend' in response.content.decode()
    assert f'/jobs/{job.pk}/retry/' not in response.content.decode()


def test_binary_probability_histogram_respects_mask_and_declared_meaning():
    from storm_studio.views import _report_visuals
    result = {'indices': [0, 1, 2, 3], 'predictions': [0, 1, 0, 1],
              'prediction_mask': [True, True, False, True],
              'output_metadata': {'task': 'binary_classification', 'threshold': 0.5,
                                  'probabilities': [0.0, 0.5, 0.2, 1.0],
                                  'category_mapping': {'0': 'negative', '1': 'positive'}},
              'resolved_data': {'inputs': [[0]] * 4}}
    histogram = _report_visuals(result)['probability_histogram']
    assert histogram['count'] == 3
    assert sum(row['count'] for row in histogram['bins']) == 3
    assert histogram['bins'][0]['count'] == 1
    assert histogram['bins'][-1]['count'] == 1
    assert histogram['mean'] == 0.5
    assert histogram['meaning'] == 'positive'
    result['output_metadata']['probabilities'] = [0.1]
    assert _report_visuals(result)['probability_histogram'] is None
