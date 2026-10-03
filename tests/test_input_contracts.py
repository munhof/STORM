"""Preflight must use declarations, never construct scientific models."""
import json
import subprocess
import sys

import pytest


def catalog_with_contracts():
    from storm.contracts import ModelInputContract
    from storm.suite import Component, default_catalog
    catalog = default_catalog()
    def forbidden(config):
        raise AssertionError('preflight constructed a model')
    catalog.register(Component('native', forbidden, ('group',), {}, input_contract=
        ModelInputContract(input_type='temporal', preparation='external',
                           required_steps=('scale',))))
    catalog.register(Component('official', forbidden, ('group',), {}, input_contract=
        ModelInputContract(input_type='pose', preparation='internal',
                           granularity='session')))
    return catalog


def test_branches_have_local_errors_without_building_models():
    from storm.contracts import validate_plan
    problems = validate_plan({'model': 'official', 'steps': [{'type': 'scale', 'factor': 2}],
                              'branch_models': ['native'],
                              'branch_configs': {'native': {'bad': True}}}, catalog_with_contracts())
    assert any(p.code == 'preparation.external_forbidden' and p.branch == 'root' for p in problems)
    assert any(p.code == 'config.invalid' and p.branch == 'native' for p in problems)
    json.dumps([p.to_dict() for p in problems])


def test_required_steps_need_validated_materialized_provenance():
    from storm.contracts import validate_plan
    spec = {'model': 'native', 'preapplied_steps': ['scale']}
    catalog = catalog_with_contracts()
    assert any(p.code == 'preparation.required_step' for p in validate_plan(spec, catalog))
    summary = {'preparation': {'validated': True, 'fingerprint': 'sha256:' + 'a' * 64,
                               'resolved_steps': [{'type': 'scale', 'factor': 2}]}}
    assert not any(p.severity == 'error' for p in validate_plan(spec, catalog, summary))
    summary['preparation']['fingerprint'] = 'unverified'
    assert any(p.code == 'preparation.required_step' for p in validate_plan(spec, catalog, summary))


def test_legacy_and_numeric_plugins_remain_readable():
    from storm.contracts import validate_plan
    from storm.suite import Component, default_catalog
    component = Component('old', lambda config: None, (), {}, '1')
    assert component.input_contract is None
    catalog = default_catalog()
    assert not any(p.severity == 'error' for p in validate_plan({'model': 'identity'}, catalog))
    assert any(p.code == 'input.unknown' for p in validate_plan({'model': 'identity'}, catalog))


def test_contract_module_has_no_scientific_or_web_imports():
    subprocess.run([sys.executable, '-c',
        "import sys; import storm.contracts; assert not any(n in sys.modules for n in "
        "('torch', 'tensorflow', 'django', 'rainstorm_thesis'))"], check=True)


@pytest.mark.django_db
def test_form_and_submit_share_python_diagnostics(monkeypatch):
    from storm.contracts import validate_plan
    from storm_studio import forms, services
    from storm_studio.models import Job, Project, Revision, Study
    catalog = catalog_with_contracts()
    monkeypatch.setattr(forms, 'catalog', lambda: catalog)
    monkeypatch.setattr(services, 'catalog', lambda: catalog)
    spec = {'model': 'official', 'config': {}, 'steps': [{'type': 'scale', 'factor': 2}],
            'data': {'inputs': [1, 2], 'train': [0], 'test': [1]}}
    form = forms.PlanForm({**spec, 'config': '{}', 'steps': json.dumps(spec['steps']),
                           'data': json.dumps(spec['data']), 'seed': 42})
    assert not form.is_valid()
    assert form.validation_problems == validate_plan(form.cleaned_data, catalog)
    study = Study.objects.create(project=Project.objects.create(name='contracts'), name='preflight')
    revision = Revision.objects.create(study=study, kind='plan', payload=spec)
    with pytest.raises(ValueError, match='external'):
        services.submit(revision)
    assert not Job.objects.filter(revision=revision).exists()


def test_shape_and_session_requirements_are_errors_when_known():
    from storm.contracts import validate_plan
    catalog = catalog_with_contracts()
    assert any(p.code == 'input.full_sessions' for p in validate_plan(
        {'model': 'official'}, catalog, {'full_sessions': False}))


def test_progress_protocol_preserves_observer_callback():
    from storm.contracts import ProgressReporter
    from storm.observability import ExecutionObserver
    class Reporter:
        def set_progress_callback(self, callback):
            self.callback = callback
    reporter = Reporter()
    assert isinstance(reporter, ProgressReporter)
    events = []
    setter = ExecutionObserver(events.append).bind(reporter)
    reporter.callback({'phase': 'fit', 'epoch': 1})
    setter(None)
    assert events[0]['epoch'] == 1
    assert reporter.callback is None


@pytest.mark.parametrize('step', [None, {'type': 'scale', 'config': [1]},
                                 {'type': 'unregistered', 'config': 3},
                                 {'type': 'pose.select_coordinates', 'config': 3}])
def test_malformed_steps_return_problems(step):
    from storm.contracts import validate_plan
    problems = validate_plan({'model': 'identity', 'steps': [step]}, catalog_with_contracts())
    assert any(p.code == 'preparation.invalid' for p in problems)


@pytest.mark.django_db
def test_studio_verifies_prepared_provenance_without_loading_artifacts():
    from storm.config import fingerprint
    from storm.contracts import validate_plan
    from storm_studio.models import Dataset, DatasetRevision, Project, Revision, Study
    from storm_studio.services import plan_data_summary
    catalog = catalog_with_contracts()
    dataset = Dataset.objects.create(name='prepared numeric')
    source = DatasetRevision.objects.create(dataset=dataset, number=1,
        connector='json_records', artifact_ref={'kind': 'datasets', 'artifact_id': 'source'})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    steps = [{'type': 'scale', 'factor': 2}]
    recipe = Revision.objects.create(study=study, kind='preparation', payload={'steps': steps, 'dataset_revision_id': source.pk})
    versions = [{'type': 'scale', 'version': '1'}]
    identity = fingerprint({'source': source.artifact_ref, 'steps': steps, 'step_versions': versions})
    prepared = DatasetRevision.objects.create(dataset=dataset, number=2,
        connector='prepared_artifact', inventory={'source_fingerprint': identity},
        config={'source_dataset_revision_id': source.pk, 'preparation_revision_id': recipe.pk,
                'preparation_fingerprint': identity, 'step_versions': versions})
    spec = {'model': 'native', 'dataset_revision_id': prepared.pk}
    summary = plan_data_summary(spec, catalog)
    assert not any(p.severity == 'error' for p in validate_plan(spec, catalog, summary))
    prepared.inventory = {'source_fingerprint': 'sha256:' + 'b' * 64}
    prepared.save()
    assert any(p.code == 'preparation.required_step' for p in validate_plan(
        spec, catalog, plan_data_summary(spec, catalog)))


@pytest.mark.django_db
def test_run_endpoint_rejects_external_preparation_with_zero_jobs(client, monkeypatch):
    from storm_studio import services
    from storm_studio.models import Job, Project, Revision, Study
    monkeypatch.setattr(services, 'catalog', catalog_with_contracts)
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    revision = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'official', 'config': {}, 'steps': [{'type': 'scale', 'factor': 2}]})
    response = client.post(f'/plans/{revision.pk}/run/')
    assert response.status_code == 302
    assert response.url == f'/studies/{study.pk}/flow/'
    from django.contrib.messages import get_messages
    diagnostics = [str(message) for message in get_messages(response.wsgi_request)]
    assert any('external preparation' in message and 'Configurar' in message
               for message in diagnostics)
    revision.refresh_from_db()
    assert revision.payload['steps'] == [{'type': 'scale', 'factor': 2}]
    assert not Job.objects.filter(revision__study=study).exists()


def test_python_execution_accepts_verified_materialized_steps(tmp_path):
    from storm.contracts import ModelInputContract
    from storm.suite import Component, default_catalog, execute
    catalog = default_catalog()
    identity = catalog.get('identity')
    catalog.register(Component('prepared_numeric', identity.builder, identity.capabilities, {},
        input_contract=ModelInputContract(preparation='external', required_steps=('scale',))))
    result = execute({'model': 'prepared_numeric',
        'data_summary': {'preparation': {'validated': True,
            'fingerprint': 'sha256:' + 'a' * 64,
            'resolved_steps': [{'type': 'scale', 'factor': 2}]}},
        'data': {'inputs': [2, 4], 'train': [0], 'test': [1]}}, tmp_path, 'prepared', catalog)
    assert result['predictions'] == [4]
