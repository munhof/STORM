import json
import pytest
from storm.pipeline import PipelineContext
from storm_studio.models import Job, Project, Revision, Study


def test_export_preserves_identity_masks_geometry_and_window_anchor():
    from storm_studio.context_evidence import model_evidence
    context = PipelineContext(data=[2, 3], targets=['a', None], metadata={
        'observation_ids': ['s:9', 's:10'], 'sessions': ['s','s'], 'frames': [9,10],
        'partition': 'validation', 'task': 'clustering', 'embeddings': [[1,2],[3,4]],
        'prediction_mask': [True, False]}, state={
        'model_inputs': [[[10,20],[30,40]], [[50,60],[70,80]]],
        'window_parents': [['s:9','s:10'],['s:9','s:10']]})
    result = model_evidence(context, 'model', {'nodes': [], 'edges': []}, {})
    assert result['resolved_data']['inputs'] == [[10,20],[70,80]]
    assert result['resolved_data']['observation_ids'] == ['s:9','s:10']
    assert result['prediction_mask'] == [True,False]
    assert result['output_metadata']['embeddings'] == [[1,2],[3,4]]
    assert 'model_inputs' not in json.dumps(result)


def test_window_without_identity_is_not_drawn_as_a_pose():
    from storm_studio.context_evidence import model_evidence
    context = PipelineContext(data=[1], state={'model_inputs': [[[1,2],[3,4]]]})
    result = model_evidence(context, 'model', {'nodes': [], 'edges': []}, {})
    assert result['resolved_data']['inputs'] == [[]]
    assert result['geometry_reason']


@pytest.mark.django_db
def test_preparation_is_a_primary_graphical_workspace(client):
    study=Study.objects.create(project=Project.objects.create(name='P'),name='S')
    page=client.get(f'/studies/{study.pk}/experiment/?mode=prepare')
    content=page.content.decode()
    assert 'Preparación de datos' in content
    assert 'id="context-comparison"' in content
    assert 'mode=prepare' in content


@pytest.mark.django_db
def test_graph_results_render_visual_evidence_without_unpickling(client, settings, tmp_path, monkeypatch):
    from storm_studio.context_evidence import save_evidence
    settings.ARTIFACT_ROOT=tmp_path
    study=Study.objects.create(project=Project.objects.create(name='P'),name='S')
    revision=Revision.objects.create(study=study,kind='experiment_run',payload={})
    job=Job.objects.create(revision=revision,operation='experiment',status='completed',result={})
    context=PipelineContext(data=[0,1],metadata={'task':'clustering','observation_ids':['s:0','s:1'],
        'sessions':['s','s'],'frames':[0,1],'embeddings':[[1,2],[3,4]],'feature_names':['nose_x','nose_y']},
        state={'model_inputs':[[10,20],[30,40]]})
    ref=save_evidence(job,{'model':{'predictions':context}}, {'nodes':[], 'edges':[]}, {})
    job.result={'evidence_ref':ref,'nodes':{},'metrics':{}};job.save()
    from storm.artifacts import FileArtifactStore
    monkeypatch.setattr(FileArtifactStore,'load',lambda *a: pytest.fail('Studio must not unpickle models'))
    page=client.get(f'/studies/{study.pk}/results/?job={job.pk}')
    assert page.status_code==200
    content=page.content.decode()
    for expected in ['Distribución de estados','Geometría','Pose y etiquetas','result-geometry','result-pose']:
        assert expected in content


def test_distribution_more_than_thirty_states_keeps_all_counts():
    from storm_studio.views import _report_visuals
    result={'predictions': list(range(50)), 'indices':list(range(50)),
            'capabilities':['group'], 'resolved_data':{'inputs':[[1,2]]*50}}
    visuals=_report_visuals(result)
    assert len(visuals['predictions']) == 50
    assert sum(row['count'] for row in visuals['predictions']) == 50


@pytest.mark.django_db
def test_preparation_preview_runs_in_worker_and_keeps_study_graph(client, settings, tmp_path):
    from storm.experiment_examples import tabular_graph
    from storm_studio.services import perform
    settings.ARTIFACT_ROOT=tmp_path
    study=Study.objects.create(project=Project.objects.create(name='P'),name='S')
    graph=tabular_graph()
    response=client.post(f'/studies/{study.pk}/experiment/', data=json.dumps({
        'action':'preview_async','graph':graph,'node':'train_inputs'}),content_type='application/json')
    assert response.status_code==200, response.content
    job=Job.objects.get(pk=response.json()['job'])
    assert job.operation=='experiment_preview'
    perform(str(job.pk))
    result=client.post(f'/studies/{study.pk}/experiment/',data=json.dumps({
        'action':'preview_status','job':str(job.pk)}),content_type='application/json').json()
    assert result['status']=='completed'
    assert result['result']['scope']=='sample_only'
    assert result['result']['outputs']['context']['rows']
    assert not Revision.objects.filter(study=study,kind='experiment').exists()
    rejected=client.post(f'/studies/{study.pk}/experiment/',data=json.dumps({
        'action':'preview_async','graph':graph,'node':'model'}),content_type='application/json')
    assert rejected.status_code==400
    assert Job.objects.count()==1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('size', [(1920,1080),(2560,1440)])
def test_desktop_context_visuals_and_preparation(live_server, settings, tmp_path, size):
    from playwright.sync_api import sync_playwright, expect
    from storm_studio.context_evidence import save_evidence
    from storm_studio.services import perform
    settings.ARTIFACT_ROOT=tmp_path
    study=Study.objects.create(project=Project.objects.create(name='P'),name='Visual study')
    revision=Revision.objects.create(study=study,kind='experiment_run',payload={})
    job=Job.objects.create(revision=revision,operation='experiment',status='completed',result={})
    context=PipelineContext(data=[0,1],metadata={'task':'clustering','observation_ids':['a:9','b:10'],
        'sessions':['a','b'],'frames':[9,10],'embeddings':[[1,2],[3,4]],
        'feature_names':['nose_x','nose_y'],'units':'cm'},state={'model_inputs':[[10,20],[30,40]]})
    ref=save_evidence(job,{'model':{'predictions':context}}, {'nodes':[], 'edges':[]}, {})
    job.result={'evidence_ref':ref,'nodes':{},'metrics':{}};job.save()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':size[0],'height':size[1]})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(f'{live_server.url}/studies/{study.pk}/results/?job={job.pk}')
        expect(page.locator('#result-geometry circle')).to_have_count(2)
        expect(page.locator('#result-pose circle')).to_have_count(1)
        page.get_by_label('Eje X',exact=True).select_option('1')
        page.get_by_label('Sesión de pose',exact=True).select_option('b')
        page.get_by_role('button',name='Ver sesión',exact=True).click()
        expect(page.locator('#pose-readout')).to_contain_text('b:10')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.get_by_role('navigation',name='Navegación del estudio').get_by_role('link',name='Preparación').click()
        expect(page.get_by_role('heading',name='Preparación de datos')).to_be_visible()
        page.get_by_text('Archivo',exact=True).click()
        page.get_by_role('button',name='Ejemplo tabular').click()
        page.get_by_text('Archivo',exact=True).click()
        page.locator('[data-node-id="train_inputs"]').click()
        with page.expect_response(lambda r:r.request.method=='POST' and 'preview_async' in (r.request.post_data or '')) as response:
            page.get_by_role('button',name='Previsualizar nodo').click()
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(perform, response.value.json()['job']).result()
        expect(page.locator('#context-comparison article')).to_have_count(2)
        expect(page.locator('#context-comparison svg circle').first).to_be_visible()
        assert errors==[]
        browser.close()


@pytest.mark.django_db
@pytest.mark.parametrize('matching_source', [True,False])
def test_graph_video_requires_matching_source_and_preserves_frames(client, settings, tmp_path, matching_source):
    from storm_studio.context_evidence import save_evidence
    from storm_studio.models import Dataset,DatasetAsset,DatasetRevision
    settings.WORKSPACE=tmp_path;settings.ARTIFACT_ROOT=tmp_path/'artifacts'
    dataset=Dataset.objects.create(name='Pose and video')
    pose=DatasetAsset.objects.create(dataset=dataset,role='pose',original_name='s.h5',relative_path='s.h5',sha256='a'*64,size_bytes=1,session_id='s')
    video=DatasetAsset.objects.create(dataset=dataset,role='video',original_name='s.mp4',relative_path='s.mp4',sha256='b'*64,size_bytes=1,session_id='s')
    revision=DatasetRevision.objects.create(dataset=dataset,number=1,connector='dlc_h5',status='ready',
        asset_ids=[pose.pk,video.pk],config={'fps':30,'asset_sessions':{str(video.pk):'s'}})
    study=Study.objects.create(project=Project.objects.create(name='P'),name='S',dataset_revision=revision)
    plan=Revision.objects.create(study=study,kind='experiment_run',payload={})
    job=Job.objects.create(revision=plan,operation='experiment',status='completed',result={})
    context=PipelineContext(data=[0,1],metadata={'task':'clustering','observation_ids':['s:9','s:10'],
        'sessions':['s','s'],'frames':[9,10],'partition':'validation'},state={'model_inputs':[[1,2],[3,4]]},
        artifacts={'prepared_sessions':[{'source_sha256':('a' if matching_source else 'c')*64}]})
    job.result={'evidence_ref':save_evidence(job,{'model':{'predictions':context}},{'nodes':[],'edges':[]},{}),'nodes':{},'metrics':{}}
    job.save()
    page=client.get(f'/studies/{study.pk}/results/?job={job.pk}')
    assert page.status_code==200
    if matching_source:
        assert page.context['report_video']['start_frame']==9
        assert b'id="prediction-report-player"' in page.content
    else:
        assert page.context['report_video'] is None
        assert b'id="prediction-report-player"' not in page.content


def test_recovered_pose_uses_bodypart_order_from_original_context_graph():
    from storm_studio.context_evidence import model_evidence
    original = {'nodes': [{'id': 'prepare', 'type': 'pose.prepare_sessions',
        'config': {'bodyparts': ['nose', 'body']}},
        {'id': 'bound', 'type': 'adapter.select', 'config': {}}],
        'edges': [{'source': 'prepare.context', 'target': 'bound.context'}]}
    graph = {'nodes': [{'id': 'saved', 'type': 'model.saved', 'config': {'source': {'source_graph': original}}},
        {'id': 'context', 'type': 'adapter.saved_context', 'config': {'node': 'bound', 'port': 'context'}},
        {'id': 'model', 'type': 'model.infer_saved', 'config': {}}], 'edges': [
        {'source': 'saved.model', 'target': 'context.model'},
        {'source': 'context.context', 'target': 'model.validation'}]}
    context = PipelineContext(data=[2], metadata={'observation_ids': ['s:9']},
        state={'model_inputs': [[[1, 2, 3, 4]]], 'window_parents': [['s:9']]})
    result = model_evidence(context, 'model', graph, {})
    assert result['resolved_data']['feature_names'] == ['nose_x', 'nose_y', 'body_x', 'body_y']
    assert result['resolved_data']['inputs'] == [[1, 2, 3, 4]]
    # Identity on labels must not be misrepresented as pose coordinates.
    context.state['model_inputs'] = [2]
    result = model_evidence(context, 'model', graph, {})
    assert result['resolved_data']['feature_names'] == []


@pytest.mark.django_db
def test_unlabelled_result_does_not_offer_reference_metric_action(client, settings, tmp_path):
    from storm_studio.context_evidence import save_evidence
    settings.ARTIFACT_ROOT = tmp_path
    study = Study.objects.create(project=Project.objects.create(name='P'), name='S')
    revision = Revision.objects.create(study=study, kind='experiment_run', payload={})
    job = Job.objects.create(revision=revision, operation='experiment', status='completed')
    context = PipelineContext(data=[0], metadata={'task': 'clustering', 'partition': 'validation',
        'observation_ids': ['s:0'], 'sessions': ['s'], 'frames': [0]}, state={'model_inputs': [[1, 2]]})
    job.result = {'evidence_ref': save_evidence(job, {'model': {'predictions': context}},
        {'nodes': [], 'edges': []}, {}), 'nodes': {}, 'metrics': {}}
    job.save()
    content = client.get(f'/studies/{study.pk}/results/?job={job.pk}').content.decode()
    assert 'Calcular métricas' not in content
    assert 'No hay observaciones con etiqueta válida' in content
