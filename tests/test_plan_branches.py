from copy import deepcopy
import pytest


def test_branch_specs_isolate_inputs_and_never_inherit_branches():
    from storm.plans import branch_specs
    spec = {'model': 'identity', 'config': {}, 'steps': [{'type': 'scale', 'factor': 2}],
            'data': {'inputs': [1, 2]}, 'branch_models': ['constant'],
            'branch_configs': {'constant': {'value': 3}},
            'branch_overrides': {'constant': {'steps': [], 'dataset_revision_id': 7,
                                              'data': {}, 'connector': 'json_records'}}}
    original = deepcopy(spec)
    branches = dict(branch_specs(spec))
    assert branches['constant']['config'] == {'value': 3}
    assert branches['constant']['steps'] == []
    assert branches['constant']['dataset_revision_id'] == 7
    assert 'branch_models' not in branches['constant']
    branches['root']['steps'].append({'type': 'center'})
    assert spec == original


def test_validation_uses_each_branch_preparation():
    from storm.contracts import validate_plan
    from storm.suite import Component, default_catalog
    from storm.contracts import ModelInputContract
    def catalog_with_contracts():
        catalog = default_catalog()
        for name, mode in [('native', 'external'), ('official', 'internal')]:
            catalog.register(Component(name, lambda c: None, (), {}, input_contract=
                ModelInputContract(preparation=mode)))
        return catalog
    spec = {'model': 'native', 'steps': [{'type': 'scale', 'factor': 2}],
            'branch_models': ['official'], 'branch_overrides': {'official': {'steps': []}}}
    assert not any(p.code == 'preparation.external_forbidden' for p in
                   validate_plan(spec, catalog_with_contracts()))


@pytest.mark.django_db
def test_running_branches_preserves_editable_root_and_configuration(client):
    from storm_studio import services
    from storm_studio.models import Job, Project, Revision, Study
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    root = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'config': {}, 'branch_models': ['constant'],
        'branch_configs': {'constant': {'value': 9}},
        'data': {'inputs': [1, 2], 'targets': [1, 2], 'train': [0], 'test': [1]}})
    response = client.post(f'/plans/{root.pk}/run/', {'run_branches': '1'})
    assert response.status_code == 302
    jobs = list(Job.objects.filter(revision__study=study).select_related('revision'))
    assert len(jobs) == 2
    variant = next(job.revision for job in jobs if job.revision_id != root.pk)
    assert variant.payload['config'] == {'value': 9}
    assert 'branch_models' not in variant.payload
    assert services.active_plan(study).pk == root.pk


def test_undeclared_override_fields_are_rejected():
    from storm.plans import branch_specs
    with pytest.raises(ValueError, match='Unknown branch override'):
        branch_specs({'model': 'identity', 'branch_models': ['constant'],
                      'branch_overrides': {'constant': {'model': 'unknown'}}})


@pytest.mark.django_db
def test_branch_preparation_reference_is_resolved_without_mutating_revision(client):
    from storm_studio import services
    from storm_studio.models import Dataset, DatasetRevision, Job, Project, Revision, Study
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    source = DatasetRevision.objects.create(dataset=Dataset.objects.create(name='Branch'),
        number=1, connector='json_records', status='ready')
    recipe = Revision.objects.create(study=study, kind='preparation', payload={
        'dataset_revision_id': source.pk, 'steps': [{'type': 'scale', 'factor': 3}]})
    root = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'config': {}, 'branch_models': ['constant'],
        'branch_overrides': {'constant': {'preparation_revision_id': recipe.pk}},
        'data': {'inputs': [1, 2], 'train': [0], 'test': [1]}})
    before = deepcopy(root.payload)
    response = client.post(f'/plans/{root.pk}/run/', {'run_branches': '1'})
    assert response.status_code == 302
    variant = Job.objects.get(revision__study=study, revision__parent=root).revision
    assert variant.payload['steps'] == recipe.payload['steps']
    assert variant.payload['dataset_revision_id'] == source.pk
    assert variant.payload['connector'] == 'json_records'
    root.refresh_from_db()
    assert root.payload == before


@pytest.mark.django_db
def test_submission_diagnostics_keep_branch_identity():
    from storm.contracts import PlanValidationError
    from storm_studio import services
    from storm_studio.models import Job, Project, Revision, Study
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    revision = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'branch_models': ['constant'],
        'branch_configs': {'constant': {'unknown': 3}}})
    with pytest.raises(PlanValidationError) as caught:
        services.submit(revision)
    assert any(p.branch == 'constant' and p.code == 'config.invalid' for p in caught.value.problems)
    assert not Job.objects.exists()


@pytest.mark.django_db
def test_loading_recipe_clears_overrides_from_inherited_branches(client, monkeypatch):
    from storm.suite import default_catalog
    from storm_studio import services
    from storm_studio.models import Project, Revision, Study
    catalog = default_catalog()
    catalog.register_recipe_preset({'id': 'fresh', 'model': 'identity', 'config': {}, 'steps': []})
    monkeypatch.setattr(services, 'catalog', lambda: catalog)
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    original = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'constant', 'branch_models': ['identity'],
        'branch_overrides': {'identity': {'steps': [{'type': 'scale', 'factor': 3}]}}})
    response = client.get(f'/studies/{study.pk}/flow/?recipe_preset=fresh')
    assert response.context['form'].initial['branch_models'] == []
    assert response.context['form'].initial['branch_overrides'] == {}
    original.refresh_from_db()
    assert original.payload['branch_overrides']['identity']['steps']


@pytest.mark.django_db
def test_device_resume_preserves_editable_plan(client, settings, tmp_path, monkeypatch):
    from storm.artifacts import FileArtifactStore
    from storm.suite import default_catalog
    from storm_studio import services
    from storm_studio.models import Job, Project, Revision, Study
    settings.ARTIFACT_ROOT = tmp_path
    catalog = default_catalog()
    catalog.get('identity').schema.setdefault('properties', {})['device'] = {
        'type': 'string', 'enum': ['cpu', 'cuda']}
    monkeypatch.setattr(services, 'catalog', lambda: catalog)
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity', 'config': {'device': 'cpu'}, 'execution_variant': False,
        'branch_models': ['constant'], 'branch_configs': {'constant': {'value': 3}},
        'branch_overrides': {'constant': {'steps': []}}})
    source = Job.objects.create(revision=plan, status='interrupted')
    FileArtifactStore(tmp_path).save(kind='checkpoints', artifact_id=str(source.pk), value={})
    response = client.post(f'/jobs/{source.pk}/resume/', {'device': 'cuda'})
    assert response.status_code == 302
    assert services.active_plan(study).pk == plan.pk
    resumed = Job.objects.get(operation='resume').revision
    assert resumed.payload['config']['device'] == 'cuda'
    assert all(key not in resumed.payload for key in ('branch_models', 'branch_configs', 'branch_overrides'))
