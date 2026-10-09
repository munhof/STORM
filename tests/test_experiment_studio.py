import json
import pytest
from tests.test_experiments import tabular_graph


@pytest.fixture
def study(db):
    from storm_studio.models import Project, Study
    return Study.objects.create(project=Project.objects.create(name='Experiments'), name='Tabular')


def post(client, study, action, **data):
    return client.post(f'/studies/{study.pk}/experiment/',
                       data=json.dumps({'action': action, **data}), content_type='application/json')


def test_python_and_studio_share_diagnostics(client, study):
    from storm.experiment_nodes import experiment_registry
    spec = tabular_graph()
    spec['nodes'][4]['config'] = {'unknown': 1}
    response = post(client, study, 'validate', graph=spec)
    assert response.status_code == 200
    assert response.json()['problems'] == [p.to_dict() for p in experiment_registry().validate(spec)]


def test_save_reopen_export_keeps_science_separate_from_canvas(client, study):
    from storm_studio.models import Revision
    from storm.experiments import scientific_fingerprint
    spec = tabular_graph()
    response = post(client, study, 'save', graph=spec, visual={'zoom': 2}, seeds=[156])
    assert response.status_code == 200
    revision = Revision.objects.get(pk=response.json()['revision'])
    assert 'visual' not in revision.payload['graph']
    response = client.get(f'/studies/{study.pk}/experiment/?format=json')
    assert response.json()['visual'] == {'zoom': 2}
    assert scientific_fingerprint(response.json()['graph']) == scientific_fingerprint(spec)


def test_entire_batch_validates_before_jobs_are_created(client, study):
    from storm_studio.models import Job
    spec = tabular_graph()
    response = post(client, study, 'enqueue', graph=spec, sweep={'model.unknown': [1, 2]})
    assert response.status_code == 400
    assert Job.objects.count() == 0


def test_sweep_preview_matches_worker_jobs_and_persists_evidence(client, study, settings, tmp_path):
    from storm_studio.models import Job
    from storm_studio.services import perform
    settings.ARTIFACT_ROOT = tmp_path
    spec = tabular_graph()
    spec['nodes'][4]['type'] = 'model.constant'
    options = {'graph': spec, 'sweep': {'model.value': [2, 9]}, 'seeds': [156]}
    preview = post(client, study, 'expand', **options)
    assert preview.status_code == 200
    response = post(client, study, 'enqueue', **options)
    assert response.status_code == 200
    assert [j.revision.payload['run_id'] for j in Job.objects.order_by('created')] == [
        run['run_id'] for run in preview.json()['runs']]
    for job in Job.objects.all():
        perform(str(job.pk))
        job.refresh_from_db()
        assert job.status == 'completed', job.error
        assert job.result['status'] == 'complete'
        assert job.result['outputs_ref']
        assert job.result['nodes']['metric']['status'] == 'completed'


def test_node_preview_never_trains_a_model(client, study):
    response = post(client, study, 'preview', graph=tabular_graph(), node='center')
    assert response.status_code == 200
    assert response.json()['outputs']['validation']['rows'] == [98.]
    assert response.json()['scope'] == 'sample_only'
    response = post(client, study, 'preview', graph=tabular_graph(), node='model')
    assert response.status_code == 400


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('size', [(1920, 1080), (2560, 1440)])
def test_desktop_experiment_roundtrip(live_server, size):
    from concurrent.futures import ThreadPoolExecutor
    from playwright.sync_api import sync_playwright, expect
    from storm_studio.models import Project, Study
    study = Study.objects.create(project=Project.objects.create(name='Desktop'), name='Graph')
    with ThreadPoolExecutor(max_workers=1), sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': size[0], 'height': size[1]})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'{live_server.url}/studies/{study.pk}/experiment/')
        page.get_by_text('Archivo', exact=True).click()
        page.get_by_role('button', name='Ejemplo tabular').click()
        page.get_by_text('Archivo', exact=True).click()
        expect(page.locator('[data-node-id]')).to_have_count(6)
        page.get_by_label('Buscar componente').fill('model.constant')
        expect(page.locator('#catalog button:visible')).to_have_count(1)
        page.get_by_label('Buscar componente').fill('')
        page.get_by_role('button', name='Ordenar grafo').click()
        page.locator('[data-node-id="model"]').click()
        expect(page.get_by_label('Entrada train')).to_have_value('train_inputs.context')
        page.get_by_role('button', name='Duplicar nodo').click()
        expect(page.locator('[data-node-id]')).to_have_count(7)
        page.get_by_role('button', name='Deshacer', exact=True).click()
        expect(page.locator('[data-node-id]')).to_have_count(6)
        page.get_by_label('Semillas').fill('156, 42')
        page.get_by_role('button', name='Guardar').click()
        expect(page.locator('#experiment-status')).to_contain_text('Guardado')
        page.reload()
        expect(page.locator('[data-node-id]')).to_have_count(6)
        page.get_by_role('button', name='Ver corridas').click()
        expect(page.locator('#batch-preview')).to_contain_text('2 corridas')
        page.get_by_role('button', name='Encolar lote').click()
        expect(page.locator('#experiment-status')).to_contain_text('2 trabajos')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=f'/tmp/storm-experiment-{size[0]}.png', full_page=True)
        assert errors == []
        browser.close()


def test_reserved_evaluation_is_a_separate_job_from_frozen_selection(client, study, settings, tmp_path):
    from storm_studio.models import Job
    from storm_studio.services import perform
    settings.ARTIFACT_ROOT = tmp_path
    response = post(client, study, 'enqueue', graph=tabular_graph())
    job = Job.objects.get(pk=response.json()['jobs'][0])
    perform(str(job.pk))
    frozen = post(client, study, 'freeze', graph=tabular_graph(), job=str(job.pk), model_node='model')
    assert frozen.status_code == 200
    assert Job.objects.count() == 1
    response = post(client, study, 'evaluate_test', graph=tabular_graph(), selection=frozen.json()['selection'])
    assert response.status_code == 200
    test_job = Job.objects.get(pk=response.json()['job'])
    perform(str(test_job.pk))
    test_job.refresh_from_db()
    assert test_job.status == 'completed', test_job.error
    assert test_job.result['partition'] == 'test'
    assert test_job.result['predictions'] == [3.]


def test_plugin_preview_uses_declared_sampler_without_loading_full_dataset(client, study, monkeypatch):
    from storm.suite import default_catalog
    from storm.experiments import NodeOperation
    from storm.pipeline import PipelineContext
    from storm_studio import experiments
    calls = []
    def load(inputs, config):
        raise AssertionError('Full dataset must not be loaded for preview')
    def sample(inputs, config):
        calls.append('sample')
        return {'context': PipelineContext(data=[1, 2, 3])}
    catalog = default_catalog()
    catalog.experiment_nodes.append(NodeOperation('data.external', '1', {}, {'context': 'context'},
        {'type': 'object', 'properties': {}}, load, kind='load', preview=sample))
    monkeypatch.setattr(experiments, 'catalog', lambda: catalog)
    graph = {'version': '1', 'nodes': [{'id': 'source', 'type': 'data.external'}], 'edges': []}
    response = post(client, study, 'preview', graph=graph, node='source')
    assert response.status_code == 200
    assert response.json()['outputs']['context']['rows'] == [1, 2, 3]
    assert calls == ['sample']


def test_python_export_restores_studio_plugins(client, study, settings, monkeypatch):
    from tests.plugin_fixtures import steps
    monkeypatch.setattr(steps, 'register', lambda catalog: None, raising=False)
    settings.STORM_PLUGINS = ('tests.plugin_fixtures.steps',)
    post(client, study, 'save', graph=tabular_graph(), seeds=[156])
    response = client.get(f'/studies/{study.pk}/experiment/?format=python')
    assert response['Content-Type'].startswith('text/x-python')
    assert 'tests.plugin_fixtures.steps' in response.content.decode()
    namespace = {}
    exec(compile(response.content.decode(), 'experiment.py', 'exec'), namespace)
    assert namespace['runs'][0]['result'].status == 'complete'


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('size', [(1920, 1080), (2560, 1440)])
def test_desktop_internal_model_graph_stays_in_node_configuration(live_server, size, monkeypatch):
    from dataclasses import replace
    from storm.suite import default_catalog
    from storm_studio import experiments
    from storm_studio.models import Project, Study
    from playwright.sync_api import sync_playwright, expect
    catalog = default_catalog()
    descriptor = {'field': 'architecture_graph', 'default': {'version': '1',
        'nodes': [{'id': 'encoder', 'type': 'dense', 'config': {}}], 'edges': []},
        'nodes': {'dense': {'schema': {'type': 'object', 'properties': {}}}},
        'slots': {'encoder': ['dense']}}
    catalog.register(replace(catalog.get('constant'), name='architecture_example',
        schema={'type': 'object', 'properties': {'value': {'type': 'number', 'default': 0},
            'architecture_graph': {'type': 'object', 'default': {}}}}, descriptor={'graph': descriptor}))
    monkeypatch.setattr(experiments, 'catalog', lambda: catalog)
    study = Study.objects.create(project=Project.objects.create(name='Architecture'), name='Internal graph')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': size[0], 'height': size[1]})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'{live_server.url}/studies/{study.pk}/experiment/')
        page.get_by_role('button', name='model.architecture_example', exact=True).click()
        page.get_by_text('Arquitectura interna del modelo', exact=True).click()
        expect(page.locator('[data-model-graph]')).to_be_visible()
        page.get_by_role('button', name='Activar grafo', exact=True).click()
        page.get_by_role('button', name='Guardar', exact=True).click()
        expect(page.locator('#experiment-status')).to_contain_text('Guardado')
        payload = page.request.get(f'{live_server.url}/studies/{study.pk}/experiment/?format=json').json()
        assert len(payload['graph']['nodes']) == 1
        assert payload['graph']['nodes'][0]['config']['architecture_graph']['nodes'][0]['id'] == 'encoder'
        assert payload['graph']['edges'] == []
        assert errors == []
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=f'/tmp/storm-model-detail-{size[0]}.png', full_page=True)
        browser.close()


def test_editor_assets_are_available_with_debug_disabled(client, settings):
    from django.urls import reverse
    settings.DEBUG = False
    response = client.get(reverse('experiment_asset', args=['experiment.js']))
    assert response.status_code == 200
    assert response['Content-Type'].startswith('text/javascript')
    assert b'experiment-editor' in b''.join(response.streaming_content)
    assert client.get(reverse('experiment_asset', args=['secret.key'])).status_code == 404


def test_editor_header_shows_saved_experiment_revision(client, study):
    saved = post(client, study, 'save', graph=tabular_graph()).json()['revision']
    response = client.get(f'/studies/{study.pk}/experiment/')
    assert f'Plan activo: r{saved}' in response.content.decode()
    assert b'Historial' in response.content


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('size', [(1920, 1080), (2560, 1440)])
def test_desktop_connect_feature_blocks(live_server, size):
    from playwright.sync_api import sync_playwright, expect
    from storm_studio.models import Project, Study
    study = Study.objects.create(project=Project.objects.create(name='Features'), name='Pose features')
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': size[0], 'height': size[1]})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(f'{live_server.url}/studies/{study.pk}/prepare/')
        page.get_by_text('Archivo', exact=True).click()
        page.get_by_role('button', name='Ejemplo tabular').click()
        page.get_by_text('Archivo', exact=True).click()
        page.get_by_role('button', name='Distancia entre puntos', exact=True).click()
        expect(page.get_by_role('heading', name='Extracción de características')).to_be_visible()
        page.get_by_role('button', name='Salida del bloque data.train', exact=True).click()
        page.get_by_role('button', name='Entrada del bloque features_distance.context', exact=True).click()
        expect(page.get_by_label('Entrada context', exact=True)).to_have_value('data.train')
        page.get_by_role('button', name='Salida del bloque features_distance.context', exact=True).press('Enter')
        page.get_by_role('button', name='Entrada del bloque features_distance.context', exact=True).press('Enter')
        expect(page.locator('#experiment-status')).to_contain_text('no son compatibles')
        page.keyboard.press('Escape')
        page.get_by_role('button', name='Guardar', exact=True).click()
        expect(page.locator('#experiment-status')).to_contain_text('Guardado')
        page.reload()
        page.locator('[data-node-id="features_distance"]').click()
        expect(page.get_by_label('Entrada context', exact=True)).to_have_value('data.train')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=f'/tmp/storm-feature-graph-{size[0]}.png', full_page=True)
        assert not errors
        browser.close()


def test_comparison_page_opens_without_selected_runs(client, study):
    response = client.get(f'/studies/{study.pk}/comparisons/')
    assert response.status_code == 200
    assert 'Comparar corridas' in response.content.decode()


def test_model_sources_do_not_offer_untrained_models_as_bundles(study):
    from storm_studio.experiments import _model_source_options
    sources = _model_source_options(study)
    assert not any(source.get('bundle') and source['model'] in
                   ('constant', 'identity', 'mean_regressor') for source in sources)


def test_comparison_collects_downstream_metrics_and_keeps_session_identity(monkeypatch):
    from types import SimpleNamespace
    from storm_studio.experiments import _comparison_profiles
    evidence = {'model': {'model': 'model', 'partition': 'validation',
        'predictions': [1, 2], 'prediction_mask': [True, True],
        'resolved_data': {'observation_ids': ['frame1', 'frame1'],
                          'sessions': ['session_a', 'session_b']},
        'output_metadata': {'task': 'regression'}}}
    monkeypatch.setattr('storm_studio.context_evidence.load_evidence', lambda ref: evidence)
    metric = {'name': 'mae', 'value': 1, 'version': '1', 'direction': 'minimize',
              'task': 'regression', 'partition': 'validation', 'observation_ids': ['frame1']}
    job = SimpleNamespace(operation='experiment', result={'evidence_ref': {},
        'resolved': {'edges': [{'source': 'model.predictions', 'target': 'metric.context'}]},
        'metrics': {'metric.metric': metric}})
    job.result['evidence_ref'] = {'id': 'test'}
    profiles = _comparison_profiles([job])
    assert len(profiles[0]['metrics']) == 1
    assert len(profiles[0]['valid']) == 2


def test_sources_include_completed_graph_model(client, study):
    from storm_studio.models import Job, Revision
    from storm_studio.experiments import _model_source_options
    revision = Revision.objects.create(study=study, kind='experiment', payload={})
    job = Job.objects.create(revision=revision, operation='experiment', status='completed', result={
        'outputs_ref': {'id': 'test'}, 'resolved': {'nodes': [{'id': 'baseline',
        'type': 'model.constant', 'config': {'value': 3}}]},
        'nodes': {'baseline': {'type': 'model.constant', 'status': 'completed', 'version': '1'}}})
    options = _model_source_options(study)
    assert any(option.get('job') == str(job.pk) and option['config'] == {'value': 3}
               for option in options)


def test_copy_model_parameters_reads_saved_source_and_returns_unfitted_node(client, study):
    from storm_studio.models import Job, Revision
    revision = Revision.objects.create(study=study, kind='experiment', payload={})
    job = Job.objects.create(revision=revision, operation='experiment', status='completed', result={
        'outputs_ref': {'id': 'test'}, 'resolved': {'nodes': [{'id': 'baseline',
        'type': 'model.constant', 'config': {'value': 3}}]},
        'nodes': {'baseline': {'type': 'model.constant', 'status': 'completed', 'version': '1'}}})
    response = post(client, study, 'copy_model_parameters', source=f'run:{job.pk}:baseline')
    assert response.status_code == 200
    assert response.json()['node'] == {'type': 'model.constant', 'version': '1', 'config': {'value': 3}}
    assert Job.objects.count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('size', [(1920, 1080), (2560, 1440)])
def test_desktop_recover_model_and_copy_parameters(live_server, size):
    from playwright.sync_api import sync_playwright, expect
    from storm_studio.models import Project, Study, Job, Revision
    study = Study.objects.create(project=Project.objects.create(name='Recovery'), name='Recovery')
    revision = Revision.objects.create(study=study, kind='run', payload={})
    job = Job.objects.create(revision=revision, operation='experiment', status='completed', result={
        'outputs_ref': {'id': 'test'}, 'resolved': {'nodes': [{'id': 'baseline',
        'type': 'model.constant', 'config': {'value': 3}}]},
        'nodes': {'baseline': {'type': 'model.constant', 'status': 'completed', 'version': '1'}}})
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': size[0], 'height': size[1]})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'{live_server.url}/studies/{study.pk}/experiment/')
        page.get_by_label('Buscar componente').fill('model.saved')
        page.locator('#catalog button[data-search^="model.saved "]').click()
        assert not errors
        expect(page.locator('#inspector select')).to_have_count(1)
        page.get_by_label('Modelo entrenado, bundle o pesos recuperados').select_option(f'run:{job.pk}:baseline')
        page.get_by_role('button', name='Copiar parámetros para entrenar').click()
        expect(page.get_by_label('value', exact=True)).to_have_value('3')
        page.locator('[data-node-id="model_saved"]').click()
        page.get_by_role('button', name='Agregar rama de inferencia').click()
        expect(page.get_by_label('Entrada model', exact=True)).to_have_value('model_saved.model')
        expect(page.get_by_label('Entrada context', exact=True)).to_have_value('')
        expect(page.locator('[data-node-id]')).to_have_count(4)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=f'/tmp/storm-model-recovery-{size[0]}.png', full_page=True)
        assert not errors
        browser.close()


def test_comparison_rejects_different_category_meanings(client, study, monkeypatch):
    from storm_studio.models import Job, Revision
    revision = Revision.objects.create(study=study, kind='experiment', payload={})
    job = Job.objects.create(revision=revision, operation='experiment', status='completed', result={
        'evidence_ref': {'id': 'test'}, 'resolved': {'edges': [
            {'source': 'a.predictions', 'target': 'ma.context'},
            {'source': 'b.predictions', 'target': 'mb.context'}]},
        'metrics': {key + '.metric': {'name': 'accuracy', 'version': '1', 'value': 1,
            'task': 'classification', 'partition': 'validation', 'direction': 'maximize',
            'observation_ids': ['1']} for key in ('ma', 'mb')}})
    def output(mapping):
        return {'predictions': [0], 'prediction_mask': [True], 'partition': 'validation',
            'resolved_data': {'observation_ids': ['1'], 'sessions': ['s'], 'targets': [0]},
            'source_hashes': ['same'], 'output_metadata': {'task': 'classification',
                'category_mapping': mapping, 'category_mapping_version': '1'}}
    monkeypatch.setattr('storm_studio.context_evidence.load_evidence', lambda ref: {
        'a': output({'0': 'rest'}), 'b': output({'0': 'walk'})})
    response = client.get(f'/studies/{study.pk}/comparisons/', {'jobs': str(job.pk)})
    assert response.status_code == 200
    assert not response.context['comparable']
    assert any('categorías' in reason for reason in response.context['reasons'])


def test_compare_completed_tabular_runs_with_different_model_parameters(client, study, settings, tmp_path):
    from storm_studio.models import Job
    from storm_studio.services import perform
    settings.ARTIFACT_ROOT = tmp_path
    spec = tabular_graph()
    spec['nodes'][4]['type'] = 'model.constant'
    response = post(client, study, 'enqueue', graph=spec, sweep={'model.value': [2, 9]})
    assert response.status_code == 200
    jobs = list(Job.objects.all())
    for job in jobs:
        perform(str(job.pk))
    response = client.get(f'/studies/{study.pk}/comparisons/', {'jobs': [str(job.pk) for job in jobs]})
    assert response.status_code == 200
    assert response.context['comparable'], response.context['reasons']
    assert len(response.context['comparisons']) == 1


def test_comparison_has_label_distribution_without_claiming_metric_ranking(monkeypatch):
    from types import SimpleNamespace
    from storm_studio.experiments import _comparison_profiles
    monkeypatch.setattr('storm_studio.context_evidence.load_evidence', lambda ref: {'dummy': {
        'predictions': [0, 0, 1], 'prediction_mask': [True, True, False],
        'partition': 'validation', 'output_metadata': {'task': 'clustering'},
        'resolved_data': {'observation_ids': ['a', 'b', 'c'], 'sessions': ['s'] * 3}}})
    job = SimpleNamespace(operation='experiment', result={'evidence_ref': {'id': 'test'}})
    profile = _comparison_profiles([job])[0]
    assert profile['distribution'] == [{'label': '0', 'count': 2, 'percent': 100.0}]
    assert profile['label_count'] == 1
    assert profile['discarded'] == 1


def test_cannot_freeze_model_loader_as_a_prediction_output(client, study):
    from storm_studio.models import Revision, Job
    revision = Revision.objects.create(study=study, kind='experiment_run', payload={})
    job = Job.objects.create(revision=revision, operation='experiment', status='completed', result={
        'status': 'complete', 'fingerprint': 'test', 'outputs_ref': {},
        'nodes': {'saved': {'type': 'model.saved', 'status': 'completed'}}})
    response = post(client, study, 'freeze', job=str(job.pk), model_node='saved')
    assert response.status_code == 400
    assert not Revision.objects.filter(kind='experiment_selection').exists()
