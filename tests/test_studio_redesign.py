import pytest
from django.urls import reverse
from storm_studio.models import Job, Project, Revision, Study


@pytest.fixture
def study(db):
    return Study.objects.create(project=Project.objects.create(name='Research'), name='Pose experiment')


def graph_run(study):
    revision = Revision.objects.create(study=study, kind='experiment_run', payload={'variant_id': 'native'})
    return Job.objects.create(revision=revision, operation='experiment', status='completed', result={
        'status': 'complete', 'variant_id': 'native', 'fingerprint': 'sha256:example',
        'nodes': {'vame': {'type': 'model.vame_native', 'status': 'completed', 'duration_seconds': 2}},
        'metrics': {'score.metric': {'name': 'mse', 'value': 0.25, 'partition': 'validation', 'direction': 'minimize'}}})


def test_new_study_opens_the_experiment_workspace(client, db):
    response = client.post('/', {'name': 'New experiment'})
    assert response.url == reverse('experiment', args=[Study.objects.get(name='New experiment').pk])


@pytest.mark.parametrize('section', ['experiment', 'data', 'jobs', 'results'])
def test_workspace_has_one_consistent_primary_navigation(client, study, section):
    response = client.get(f'/studies/{study.pk}/{section}/')
    assert response.status_code == 200
    content = response.content.decode()
    primary = content.split('aria-label="Navegación del estudio"', 1)[1].split('</nav>', 1)[0]
    assert primary.count('<a ') == 5
    for label in ('Experimento', 'Preparación', 'Datos', 'Ejecuciones', 'Resultados'):
        assert label in primary
    assert 'assistant-toggle' not in content
    assert 'Workspace local · Python + Django' not in content


def test_graph_result_has_readable_evidence_without_loading_worker_resources(client, study, monkeypatch):
    job = graph_run(study)
    from storm_studio import services
    monkeypatch.setattr(services, 'load_execution_result', lambda *a: pytest.fail('No worker deserialization in results'))
    response = client.get(f'/studies/{study.pk}/results/?job={job.pk}')
    assert response.status_code == 200
    assert 'native' in response.content.decode()
    assert '0.25' in response.content.decode() or '0,25' in response.content.decode()
    assert 'vame' in response.content.decode()


def test_legacy_evidence_link_routes_graph_runs_to_their_results(client, study):
    job = graph_run(study)
    response = client.get(f'/studies/{study.pk}/evidence/?job={job.pk}')
    assert response.status_code == 302
    assert response.url == f'/studies/{study.pk}/results/?job={job.pk}'


def test_jobs_are_paginated_without_deleting_history(client, study):
    job = graph_run(study)
    for _ in range(24):
        Job.objects.create(revision=job.revision, operation='experiment', status='completed', result=job.result)
    response = client.get(f'/studies/{study.pk}/jobs/')
    assert response.status_code == 200
    assert response.content.count(b'data-job-card ') == 20
    assert b'href="?page=2"' in response.content
    assert Job.objects.count() == 25
    response = client.get(f'/studies/{study.pk}/jobs/?page=2')
    assert response.content.count(b'data-job-card ') == 5


def test_header_status_does_not_download_all_logs(client, study):
    graph_run(study)
    result = client.get(f'/status/{study.pk}/?summary=1').json()
    assert 'summary' in result
    assert 'jobs' not in result


def test_editor_focuses_on_graph_and_links_to_results(client, study):
    content = client.get(f'/studies/{study.pk}/experiment/').content.decode()
    assert 'id="catalog-search"' in content
    assert '<summary>Archivo</summary>' in content
    assert 'Ejecuciones y evidencia</h2>' not in content
    assert f'/studies/{study.pk}/results/' in content


def test_graph_results_do_not_offer_legacy_analysis_for_a_different_run(client, study):
    job = graph_run(study)
    content = client.get(f'/studies/{study.pk}/results/?job={job.pk}').content.decode()
    assert 'aria-label="Vistas de resultados"' not in content


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('size', [(1920, 1080), (2560, 1440)])
def test_desktop_navigation_pagination_and_results(live_server, size):
    from playwright.sync_api import sync_playwright, expect
    study = Study.objects.create(project=Project.objects.create(name='Desktop'), name='Results review')
    job = graph_run(study)
    for _ in range(24):
        Job.objects.create(revision=job.revision, operation='experiment', status='completed', result=job.result)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': size[0], 'height': size[1]})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'{live_server.url}/studies/{study.pk}/jobs/')
        expect(page.locator('[data-job-card]')).to_have_count(20)
        page.get_by_role('link', name='Siguiente →').click()
        expect(page.locator('[data-job-card]')).to_have_count(5)
        page.locator('[data-job-card] summary').first.click()
        page.get_by_role('link', name='Ver resultados').first.click()
        expect(page.locator('.metric-card')).to_contain_text('0,25')
        expect(page.get_by_role('heading', name='Resultados por nodo')).to_be_visible()
        expect(page.get_by_role('navigation', name='Navegación del estudio').get_by_role('link')).to_have_count(5)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert errors == []
        browser.close()


def test_prepare_entry_opens_graph_and_keeps_legacy_recipes_explicit(client, study):
    response = client.get(f'/studies/{study.pk}/prepare/', follow=True)
    assert response.status_code == 200
    assert b'id="experiment-editor"' in response.content
    assert b'features.distance' in response.content
    assert b'?editor=recipes' in response.content
    legacy = client.get(f'/studies/{study.pk}/prepare/?editor=recipes')
    assert legacy.status_code == 200
    assert b'id="preparation-form"' in legacy.content
