from types import SimpleNamespace
from datetime import datetime, timedelta, timezone


def profile(name, ids, vectors, labels, partition='validation', hashes=('same',)):
    job=SimpleNamespace(pk=name, result={'nodes': {name: {'duration_seconds': 3.5}}},
        started=datetime(2026,1,1,tzinfo=timezone.utc), finished=datetime(2026,1,1,tzinfo=timezone.utc)+timedelta(seconds=10))
    return {'job':job,'node':name,'model':name,'partition':partition,'source_hashes':list(hashes),
        'valid':dict(zip(ids,labels)), 'evidence': {'predictions':labels,'prediction_mask':[True]*len(labels),
        'resolved_data':{'observation_ids':[i[1] for i in ids],'sessions':[i[0] for i in ids],
            'inputs':vectors,'feature_names':['nose_x','nose_y'],'units':'cm'},
        'output_metadata': {'task':'clustering'},'training_history':[]}}


def test_geometry_aligns_by_identity_not_row_and_never_crosses_partition():
    from storm_studio.comparison_dashboard import build_dashboard
    a=profile('a',[('s','1'),('s','2')],[[1,2],[3,4]],[0,1])
    b=profile('b',[('s','2'),('s','1')],[[30,40],[10,20]],[7,8])
    c=profile('c',[('s','1'),('s','2')],[[1,2],[3,4]],[0,1],partition='test')
    result=build_dashboard([a,b,c])
    rows=result['spaces'][0]['points']
    assert rows[0]['labels']==[0,8,None]
    assert rows[1]['labels']==[1,7,None]
    pair=result['pairs'][0]
    assert pair['count']==2
    assert pair['cells']==[{'a':'0','b':'8','count':1},{'a':'1','b':'7','count':1}]
    assert result['models'][0]['timing']['node_seconds']==3.5
    assert result['models'][0]['timing']['training_seconds'] is None
    assert result['jobs'][0]['seconds']==10


def test_geometry_sample_is_bounded_but_contingency_uses_entire_cohort():
    from storm_studio.comparison_dashboard import build_dashboard
    ids=[('s',str(i)) for i in range(2501)]
    a=profile('a',ids,[[i,i] for i in range(len(ids))],[0]*len(ids))
    b=profile('b',ids,[[i,i] for i in range(len(ids))],[1]*len(ids))
    result=build_dashboard([a,b])
    assert len(result['spaces'][0]['points'])<=2000
    assert result['pairs'][0]['count']==2501


def test_different_source_hashes_do_not_align_geometry():
    from storm_studio.comparison_dashboard import build_dashboard
    a=profile('a',[('s','1')],[[1,2]],[0])
    b=profile('b',[('s','1')],[[1,2]],[1],hashes=('other',))
    result=build_dashboard([a,b])
    assert result['spaces'][0]['points'][0]['labels']==[0,None]
    assert not result['pairs']


def test_legacy_profiles_align_selected_indices_and_have_geometry():
    from storm_studio.experiments import _comparison_profiles
    job=SimpleNamespace(operation='infer', result={'model':'legacy', 'partition':'test',
        'predictions':[7,8], 'indices':[2,0], 'prediction_mask':[True,True],
        'resolved_data':{'inputs':[[1,2],[3,4],[5,6]], 'sessions':['s']*3,'frames':[10,11,12]},
        'output_metadata':{'task':'classification'}})
    result=_comparison_profiles([job])
    assert len(result)==1
    assert result[0]['evidence']['resolved_data']['inputs']==[[5,6],[1,2]]
    assert list(result[0]['valid'].values())==[7,8]


def test_original_training_node_duration_is_separate_from_current_inference():
    from storm_studio.comparison_dashboard import build_dashboard
    a=profile('inference',[('s','1')],[[1,2]],[0])
    a['job'].result['resolved']={'nodes':[{'id':'saved','config':{'source':{'job':'old','model_node':'trained'}}}],
        'edges':[{'source':'saved.model','target':'inference.model'}]}
    old=SimpleNamespace(pk='old',result={'nodes':{'trained':{'duration_seconds':6.1}}})
    timing=build_dashboard([a],{'old':old})['models'][0]['timing']
    assert timing['source_node_seconds']==6.1
    assert timing['node_seconds']==3.5
    assert timing['training_seconds'] is None


def test_legacy_window_axes_are_explicit_flattened_input_coordinates():
    from storm_studio.comparison_dashboard import build_dashboard
    a=profile('wide',[('s','1')],[[[1,2],[3,4]]],[0])
    space=build_dashboard([a])['spaces'][0]
    assert space['points'][0]['values']==[1,2,3,4]
    assert 'ventana' in space['kind']


import pytest


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('size', [(1920,1080),(2560,1440)])
def test_desktop_comparison_geometry_times_and_relations(live_server, settings, tmp_path, size):
    from playwright.sync_api import sync_playwright, expect
    from storm_studio.models import Project, Study, Revision, Job
    from storm_studio.context_evidence import save_evidence
    from storm.pipeline import PipelineContext
    settings.ARTIFACT_ROOT=tmp_path
    study=Study.objects.create(project=Project.objects.create(name='Compare'),name='Compare')
    revision=Revision.objects.create(study=study,kind='experiment_run',payload={})
    start=datetime.now(timezone.utc)
    job=Job.objects.create(revision=revision,operation='experiment',status='completed',started=start,finished=start+timedelta(seconds=12))
    outputs={}
    for name,labels in [('a',[0,1]),('b',[5,6])]:
        outputs[name]={'predictions':PipelineContext(data=labels,metadata={'partition':'validation','task':'clustering',
            'observation_ids':['s:1','s:2'],'sessions':['s','s'],'frames':[1,2],
            'source_hashes':['same'],'feature_names':['x','y'],'embeddings':[[0.,1.],[2.,3.]],
            'timing':{'training_seconds':1.25,'inference_seconds':.5}},
            state={'model_inputs':[[0.,1.],[2.,3.]]},
            artifacts={'model':SimpleNamespace(checkpoint={'training_history':[{'epoch':1,'total_loss':2.}]})})}
    ref=save_evidence(job,outputs,{'nodes':[],'edges':[]},{})
    job.result={'evidence_ref':ref,'nodes':{name:{'type':'model.constant','duration_seconds':2.} for name in outputs},'metrics':{}}
    job.save()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':size[0],'height':size[1]});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        response=page.goto(f'{live_server.url}/studies/{study.pk}/comparisons/?jobs={job.pk}')
        assert response.status==200
        expect(page.locator('#comparison-scatter canvas')).to_have_count(2)
        expect(page.locator('#comparison-scatter canvas').first).to_have_attribute('data-points','2')
        page.get_by_label('Vista geométrica',exact=True).select_option('own')
        expect(page.locator('#comparison-scatter canvas')).to_have_count(2)
        expect(page.locator('#comparison-pair-note')).to_contain_text('2 observaciones comunes')
        page.get_by_label('Tiempo a comparar',exact=True).select_option('training_seconds')
        expect(page.locator('#comparison-timings svg')).to_have_count(1)
        expect(page.locator('#comparison-losses circle')).to_have_count(2)
        assert not errors
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('#comparison-geometry').screenshot(path=f'/tmp/storm-comparison-geometry-test-{size[0]}.png')
        browser.close()
