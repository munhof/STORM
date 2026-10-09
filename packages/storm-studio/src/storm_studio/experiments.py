"""Studio transport for the same experiment contracts used by Python."""
from copy import deepcopy
import json
import random
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods
from storm.artifacts import FileArtifactStore
from storm.experiment_nodes import experiment_registry
from storm.experiments import ExperimentExecutor, expand_study, scientific_spec, scientific_fingerprint
from storm.pipeline import PipelineContext
from storm_studio.models import Job, Revision, Study
from storm_studio.services import catalog


@require_http_methods(['GET'])
def asset(request, name):
    # Public Studio assets, independent of DEBUG/static deployment.
    types = {'comparison.js': 'text/javascript', 'comparison.css': 'text/css', 'recipe-blocks.css': 'text/css', 'experiment.js': 'text/javascript', 'experiment.css': 'text/css', 'studio.css': 'text/css', 'studio.js': 'text/javascript', 'context-visuals.js': 'text/javascript', 'context-visuals.css': 'text/css'}
    if name not in types:
        raise Http404
    path = Path(__file__).parent / 'static' / 'storm_studio' / name
    return FileResponse(path.open('rb'), content_type=types[name])


def _model_source_options(study):
    """List local completed model artifacts and installed inference bundles."""
    options=[]
    model_catalog=catalog()
    descriptors = {item["name"]: item for item in model_catalog.describe()}
    jobs=Job.objects.filter(status='completed').exclude(operation__in=(
        'inventory','prepare','video_preview','experiment_evidence','experiment_preview','experiment_test'
    )).select_related('revision','revision__study').order_by('-created')[:1000]
    for job in jobs:
        result=job.result or {}
        if job.operation=='experiment' and result.get('outputs_ref'):
            graph=result.get('resolved') or result.get('requested') or {}
            specs={node['id']:node for node in graph.get('nodes',[])}
            for node_id,record in result.get('nodes',{}).items():
                node_type=record.get('type','')
                if record.get('status')!='completed' or not node_type.startswith('model.'):
                    continue
                model_name=node_type.removeprefix('model.')
                try: descriptor=descriptors[model_name]
                except KeyError: descriptor={}
                capabilities=list(descriptor.get('capabilities',[]))
                if 'infer' not in capabilities:
                    continue
                options.append({'id':f'run:{job.pk}:{node_id}','label':
                    f"{job.revision.study.name} · {job.pk.hex[:8]} · {node_id}",
                    'job':str(job.pk),'study':job.revision.study_id,'model_node':node_id,
                    'model':model_name,'model_version':record.get('version',descriptor.get('version','1')),
                    'capabilities':capabilities,'outputs_ref':result['outputs_ref'],
                    'source_graph':graph,'config':specs.get(node_id,{}).get('config',{}),
                    'input_contract':descriptor.get('input_contract')})
        elif result.get('model_ref') and 'infer' in result.get('capabilities',[]):
            model=result.get('model') or job.revision.payload.get('model','')
            options.append({'id':f'run:{job.pk}','label':
                f"{job.revision.study.name} · {job.pk.hex[:8]} · {model}",
                'job':str(job.pk),'study':job.revision.study_id,'model':model,
                'model_version':result.get('model_version','1'),'capabilities':result['capabilities'],
                'model_ref':result['model_ref'],'config':result.get('spec',{}).get('config',{}),
                'input_contract':result.get('input_contract')})
    for descriptor in model_catalog.describe():
        if 'infer' not in descriptor['capabilities'] or any(
                capability in descriptor['capabilities'] for capability in ('train', 'group', 'update')):
            continue
        props=descriptor.get('schema',{}).get('properties',{})
        config={key:value['default'] for key,value in props.items() if 'default' in value}
        options.append({'id':f"bundle:{descriptor['name']}",
            'label':f"Bundle instalado · {descriptor['name']}",'model':descriptor['name'],
            'model_version':descriptor['version'],'capabilities':descriptor['capabilities'],
            'bundle':True,'config':config,'input_contract':descriptor.get('input_contract')})
    return options


def _load(study):
    revision = Revision.objects.filter(study=study, kind='experiment').order_by('-pk').first()
    payload = deepcopy(revision.payload) if revision else {
        'graph': {'version': '1', 'nodes': [], 'edges': []}, 'seeds': [156],
        'variants': [{'id': 'default', 'parameters': {}}], 'sweep': {}}
    visual = Revision.objects.filter(study=study, kind='experiment_visual').order_by('-pk').first()
    payload['visual'] = visual.payload if visual else {'positions': {}, 'zoom': 1}
    return payload


def _study_spec(payload):
    return {'graph': scientific_spec(payload['graph']),
            **{k: payload[k] for k in ('variants', 'sweep', 'seeds') if k in payload}}


def _preview_value(value):
    if hasattr(value, 'tolist'):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return [_preview_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _preview_value(item) for key, item in value.items()}
    return value


def _python_export(payload):
    spec = _study_spec(payload)
    return ("import importlib, json\n"
            "from storm.suite import default_catalog\n"
            "from storm.experiment_nodes import experiment_registry, run_study\n"
            f"study = json.loads({json.dumps(spec)!r})\n"
            "catalog = default_catalog()\n"
            f"for plugin in {tuple(settings.STORM_PLUGINS)!r}:\n"
            "    importlib.import_module(plugin).register(catalog)\n"
            "runs = run_study(study, experiment_registry(catalog))\n"
            "for run in runs:\n"
            "    print(run['run_id'], run['result'].manifest())\n")


def _context_preview(value):
    if not isinstance(value, PipelineContext):
        return value
    return _preview_value({**value.summarize(), 'rows': list(value.data[:5]),
            'targets': list(value.targets[:5]) if value.targets is not None else None,
            'metadata': {k: v[:5] if isinstance(v, list) and k not in {'feature_names', 'prepared_steps'} else v for k, v in value.metadata.items()},
            'alignment_mask': value.state.get('alignment_mask', [])[:5],
            'alignment_parents': value.state.get('alignment_parents', [])[:5],
            'window_parents': value.state.get('window_parents', [])[:5],
            'source_validity_mask': value.state.get('source_validity_mask', [])[:5],
            'preparation_audit': value.state.get('preparation_audit', [])[:5],
            'source_preview': value.state.get('preview_input_context')})


def preview_node(graph, node, registry):
    required = {node}
    while True:
        parents = {edge['source'].split('.')[0] for edge in graph['edges']
                   if edge['target'].split('.')[0] in required}
        if parents <= required:
            break
        required |= parents
    subset = deepcopy(graph)
    subset['nodes'] = [n for n in graph['nodes'] if n['id'] in required]
    subset['edges'] = [e for e in graph['edges'] if e['target'].split('.')[0] in required]
    if node not in {n['id'] for n in subset['nodes']}:
        raise ValueError('Choose an existing node')
    for item in subset['nodes']:
        operation = registry.operations[item['type']]
        if operation.kind in ('model', 'evaluate') or item['type'] == 'model.saved':
            raise ValueError('Preview cannot train or evaluate a model; inspect run evidence')
        if operation.kind == 'load' and item['type'] != 'data.inline' and operation.preview is None:
            raise ValueError('This adapter must declare a bounded preview before sampling')
        if item['type'] == 'data.inline' and len(item['config'].get('inputs', [])) > 2000:
            raise ValueError('Inline preview is limited to 2000 observations; use a bounded data adapter')
    result = ExperimentExecutor(registry).run(subset, preview=True)
    if result.status != 'complete':
        return {'scope': 'sample_only', 'nodes': result.records, 'outputs': {}}
    parents = {edge['target'].split('.')[1]: _context_preview(
        result.outputs[edge['source'].split('.')[0]][edge['source'].split('.')[1]])
        for edge in subset['edges'] if edge['target'].split('.')[0] == node}
    return {'scope': 'sample_only', 'inputs': parents,
            'outputs': {key: _context_preview(v) for key, v in result.outputs[node].items()},
            'nodes': result.records}


@require_http_methods(['GET', 'POST'])
def editor(request, study_id):
    study = get_object_or_404(Study, pk=study_id)
    editor_catalog = catalog()
    editor_catalog.experiment_artifact_root = settings.ARTIFACT_ROOT
    registry = experiment_registry(editor_catalog)
    if request.method == 'GET':
        payload = _load(study)
        if request.GET.get('format') == 'python':
            return HttpResponse(_python_export(payload), content_type='text/x-python')
        if request.GET.get('format') == 'json':
            return JsonResponse(payload)
        from storm.experiment_examples import tabular_graph
        from storm_studio.views import ADVANCED_PAGES, _execution_summary
        jobs = Job.objects.filter(revision__study=study, operation='experiment').order_by('-created')
        return render(request, 'storm_studio/experiment.html', {
            'study': study, 'title': 'Preparación de datos' if request.GET.get('mode') == 'prepare' else 'Experimento',
            'preparation_mode': request.GET.get('mode') == 'prepare',
            'section': 'preparation' if request.GET.get('mode') == 'prepare' else 'experiment', 'document': payload,
            'latest': Revision.objects.filter(study=study, kind='experiment').order_by('-pk').first(),
            'dataset_summary': {'connector': study.dataset_revision.connector if study.dataset_revision else None},
            'advanced_pages': ADVANCED_PAGES,
            'execution_summary': _execution_summary(list(jobs[:20]), jobs.filter(status='completed').count()),
            'descriptors': registry.describe(), 'example': tabular_graph(),
            'model_sources': _model_source_options(study),
            'experiment_jobs': jobs[:20],
            'selections': Revision.objects.filter(study=study, kind='experiment_selection').order_by('-pk')[:20]})
    try:
        payload = json.loads(request.body)
        action = payload['action']
        if action == 'model_sources':
            return JsonResponse({'sources': _model_source_options(study)})
        if action == 'copy_model_parameters':
            source = next((item for item in _model_source_options(study)
                           if item['id'] == payload.get('source')), None)
            if source is None:
                raise ValueError('Choose an available model source')
            if not {'train', 'group'} & set(source['capabilities']):
                raise ValueError('This model only supports inference, not new training')
            component = editor_catalog.get(source['model'])
            if component.version != source['model_version']:
                raise ValueError('Model version changed; explicit migration is required')
            return JsonResponse({'node': {'type': 'model.' + source['model'],
                'version': component.version,
                'config': editor_catalog.normalize(source['model'], source['config'])}})
        if action == 'export_python':
            return JsonResponse({'source': _python_export(payload)})
        if action == 'preview_status':
            job = get_object_or_404(Job, pk=payload['job'], revision__study=study, operation='experiment_preview')
            return JsonResponse({'status': job.status, 'result': job.result, 'error': job.error})
        if action == 'recover_visuals':
            source = get_object_or_404(Job, pk=payload['job'], revision__study=study, operation='experiment', status='completed')
            revision = Revision.objects.create(study=study, kind='visual_evidence', payload={'job': str(source.pk)})
            job = Job.objects.create(revision=revision, operation='experiment_evidence')
            return JsonResponse({'job': str(job.pk)})
        if action == 'freeze':
            job = get_object_or_404(Job, pk=payload['job'], revision__study=study,
                                   operation='experiment', status='completed')
            model_node = payload['model_node']
            node = job.result.get('nodes', {}).get(model_node, {})
            operation = registry.operations.get(node.get('type'))
            if operation is None or operation.kind != 'model' or operation.outputs.get('predictions') != 'context' or job.result.get('status') != 'complete':
                raise ValueError('Select a model from a completed validation run')
            revision = Revision.objects.create(study=study, kind='experiment_selection',
                parent=job.revision, payload={'job': str(job.pk), 'model_node': model_node,
                    'fingerprint': job.result['fingerprint'], 'outputs_ref': job.result['outputs_ref']})
            return JsonResponse({'selection': revision.pk})
        if action == 'evaluate_test':
            selection = get_object_or_404(Revision, pk=payload['selection'], study=study,
                                         kind='experiment_selection')
            job = Job.objects.create(revision=selection, operation='experiment_test')
            return JsonResponse({'job': str(job.pk)})
        spec = _study_spec(payload)
        if action == 'validate':
            return JsonResponse({'problems': [p.to_dict() for p in registry.validate(spec['graph'])],
                                 'data_status': 'pending'})
        if action == 'preview_async':
            from storm_studio.context_evidence import ancestors
            node = payload['node']
            if node not in {item['id'] for item in spec['graph']['nodes']}:
                raise ValueError('Choose an existing node')
            required = ancestors(spec['graph'], node)
            for item in spec['graph']['nodes']:
                if item['id'] in required and (registry.operations[item['type']].kind in ('model', 'evaluate') or item['type'] == 'model.saved'):
                    raise ValueError('Preview cannot train or evaluate a model')
            revision = Revision.objects.create(study=study, kind='context_preview', payload={'graph': spec['graph'], 'node': node})
            job = Job.objects.create(revision=revision, operation='experiment_preview')
            return JsonResponse({'job': str(job.pk)})
        if action == 'preview':
            return JsonResponse(preview_node(spec['graph'], payload['node'], registry))
        if action == 'save':
            # Incomplete drafts can be saved, but never enqueued before validation.
            with transaction.atomic():
                parent = Revision.objects.filter(study=study, kind='experiment').order_by('-pk').first()
                revision = Revision.objects.create(study=study, kind='experiment', payload=spec, parent=parent)
                Revision.objects.create(study=study, kind='experiment_visual', payload=payload.get('visual', {}))
            return JsonResponse({'revision': revision.pk, 'fingerprint': scientific_fingerprint(spec['graph'])})
        runs = expand_study(spec, registry)
        if action == 'expand':
            return JsonResponse({'runs': runs, 'count': len(runs), 'data_status': 'pending'})
        if action == 'enqueue':
            with transaction.atomic():
                jobs = []
                for run in runs:
                    revision = Revision.objects.create(study=study, kind='experiment_run', payload=run)
                    jobs.append(str(Job.objects.create(revision=revision, operation='experiment').pk))
            return JsonResponse({'jobs': jobs, 'count': len(jobs)})
        raise ValueError('Unknown experiment action')
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        return JsonResponse({'error': str(error), 'problems': [p.to_dict() for p in
            getattr(error, 'problems', [])]}, status=400)


def perform_experiment(job):
    payload = job.revision.payload
    worker_catalog = catalog()
    worker_catalog.experiment_artifact_root = settings.ARTIFACT_ROOT
    random.seed(payload['seed'])
    for seeder in worker_catalog.seeders:
        seeder(payload['seed'])
    result = ExperimentExecutor(experiment_registry(worker_catalog)).run(payload['graph'])
    store = FileArtifactStore(settings.ARTIFACT_ROOT)
    outputs = store.save(kind='experiment_outputs', artifact_id=str(job.pk), value=result.outputs,
        metadata={'fingerprint': result.fingerprint, 'run_id': payload['run_id']})
    manifest = result.manifest()
    manifest.update(outputs_ref=outputs.to_dict(), run_id=payload['run_id'],
                    variant_id=payload['variant_id'], seed=payload['seed'])
    manifest['metrics'] = {f'{node}.{port}': value for node, ports in result.outputs.items()
                          for port, value in ports.items() if port == 'metric'}
    from storm_studio.context_evidence import save_evidence
    manifest['evidence_ref'] = save_evidence(job, result.outputs, payload['graph'], manifest['metrics'])
    return manifest


def perform_reserved(job):
    from storm.artifacts import ArtifactRef
    from storm.experiments import ExperimentResult
    from storm.selection import freeze_selection, evaluate_reserved
    payload = job.revision.payload
    source = Job.objects.get(pk=payload['job'], revision__study=job.revision.study)
    if source.result['fingerprint'] != payload['fingerprint']:
        raise ValueError('Frozen source fingerprint changed')
    store = FileArtifactStore(settings.ARTIFACT_ROOT)
    outputs = store.load(ArtifactRef.from_dict(payload['outputs_ref']))
    result = ExperimentResult(source.result['status'], source.result['requested'],
        source.result['resolved'], source.result['fingerprint'], source.result['nodes'], outputs)
    return evaluate_reserved(freeze_selection(result, payload['model_node']))


@require_http_methods(['GET'])
def results(request, study_id):
    from django.core.paginator import Paginator
    from storm_studio.views import _execution_summary
    study = get_object_or_404(Study, pk=study_id)
    jobs = Job.objects.filter(revision__study=study).exclude(
        operation__in=['inventory', 'prepare', 'video_preview', 'experiment_evidence', 'experiment_preview']).select_related('revision').order_by('-created')
    selected = (get_object_or_404(jobs, pk=request.GET['job']) if request.GET.get('job') else jobs.first())
    visuals = {}
    if selected and selected.status == 'completed':
        from storm_studio.context_evidence import visual_context
        try:
            visuals = visual_context(request, study, selected)
        except (ValueError, OSError, KeyError) as error:
            visuals = {'visual_reason': str(error)}
    return render(request, 'storm_studio/results.html', {
        **visuals,
        'study': study, 'section': 'results', 'selected': selected,
        'scientific_revision': study.revision_set.filter(kind__in=['experiment', 'plan']).order_by('-pk').first(),
        'graph_result': selected and selected.operation in {'experiment', 'experiment_test'},
        'jobs_page': Paginator(jobs, 20).get_page(request.GET.get('page')),
        'execution_summary': _execution_summary(list(jobs), jobs.filter(status='completed').count()),
        'selections': Revision.objects.filter(study=study, kind='experiment_selection').order_by('-pk'),
    })


def perform_preview(job):
    worker_catalog = catalog()
    worker_catalog.experiment_artifact_root = settings.ARTIFACT_ROOT
    payload = job.revision.payload
    return preview_node(payload['graph'], payload['node'], experiment_registry(worker_catalog))


def _comparison_outputs(job):
    from storm_studio.context_evidence import load_evidence
    from storm_studio.services import load_execution_result
    result = job.result or {}
    if job.operation == 'experiment':
        return load_evidence(result['evidence_ref']) if result.get('evidence_ref') else {}
    result = load_execution_result(job)
    data = result.get('resolved_data') or {}
    predictions = result.get('predictions', [])
    indices = result.get('indices', [])
    inputs = data.get('inputs', [])
    if len(indices) != len(predictions) or any(type(i) is not int or not 0 <= i < len(inputs) for i in indices):
        return {}
    aligned = {}
    for key in ('inputs', 'sessions', 'frames', 'observation_ids', 'targets', 'evaluation_mask'):
        values = data.get(key)
        if isinstance(values, list) and len(values) == len(inputs):
            aligned[key] = [values[i] for i in indices]
    if not aligned.get('observation_ids') and aligned.get('sessions') and aligned.get('frames'):
        aligned['observation_ids'] = [f'{session}:{frame}' for session, frame in zip(aligned['sessions'], aligned['frames'])]
    aligned.update(feature_names=data.get('feature_names', []), units=data.get('units'))
    name = result.get('model', 'legacy')
    return {name: {**result, 'resolved_data': aligned,
        'prediction_mask': result.get('prediction_mask', [True] * len(predictions))}}


def _comparison_profiles(jobs):
    from collections import Counter
    profiles=[]
    for job in jobs:
        result=job.result or {}
        for node, model in _comparison_outputs(job).items():
            data=model.get('resolved_data',{})
            predictions=model.get('predictions',[])
            ids=data.get('observation_ids',[])
            mask=model.get('prediction_mask',[])
            if len(ids)!=len(predictions) or len(mask)!=len(predictions):
                continue
            sessions = data.get('sessions', [''] * len(ids))
            if len(sessions) != len(ids):
                continue
            identities = list(zip(sessions, ids))
            if len(set(identities)) != len(identities):
                continue
            valid = {identity: prediction for identity, prediction, ok in
                     zip(identities, predictions, mask) if ok}
            counts = Counter(str(value) for value in valid.values())
            distribution = [{'label': label, 'count': count, 'percent': 100 * count / len(valid)}
                            for label, count in counts.most_common(50)]
            graph = result.get('resolved', {})
            descendants = {node}
            while True:
                children = {edge['target'].split('.')[0] for edge in graph.get('edges', [])
                            if edge['source'].split('.')[0] in descendants}
                if children <= descendants:
                    break
                descendants |= children
            metrics=[]
            for key,metric in (result.get('metrics') or {}).items():
                metric_node=key.split('.')[0]
                if metric_node not in descendants or not isinstance(metric,dict):continue
                metrics.append({'name':metric.get('name',key),'value':metric.get('value'),
                    'version':metric.get('version'),'direction':metric.get('direction'),
                    'task':metric.get('task'),'partition':metric.get('partition'),
                    'parameters':metric.get('parameters',{}),
                    'observation_ids':metric.get('observation_ids',[])})
            profiles.append({'job':job,'node':node,'model':model.get('model',node),'task':model.get('output_metadata',{}).get('task'),
                'partition':model.get('partition'),'feature_names':data.get('feature_names',[]),
                'units':data.get('units'),'source_hashes':sorted(model.get('source_hashes',[])),
                'valid':valid,'metrics':metrics, 'distribution': distribution, 'evidence': model,
                'label_count': len(counts), 'discarded': len(predictions) - len(valid),
                'targets': {identity: target for identity, target in zip(identities, data.get('targets') or [])},
                'category_mapping': model.get('output_metadata', {}).get('category_mapping'),
                'category_mapping_version': model.get('output_metadata', {}).get('category_mapping_version'),
                'exploratory': model.get('output_metadata', {}).get('exploratory', False)})
    return profiles


@require_http_methods(['GET'])
def compare_runs(request, study_id):
    study=get_object_or_404(Study,pk=study_id)
    jobs=list(Job.objects.filter(status='completed',operation__in=('experiment','train','infer','apply'),
        revision__study__archived_at__isnull=True).select_related('revision','revision__study').order_by('-created')[:300])
    ids=list(dict.fromkeys(value for item in request.GET.getlist('jobs') for value in item.split(',') if value))
    selected=[job for job in jobs if str(job.pk) in ids]
    profiles=_comparison_profiles(selected)
    reasons=[];metric_comparisons=[];prediction_pairs=[]
    if len(profiles)<2:reasons.append('Elegí corridas que contengan al menos dos salidas de modelo.')
    if any(not any(profile['job'] == job for profile in profiles) for job in selected):reasons.append('Alguna corrida no conserva evidencia portable para comparar salidas del grafo.')
    if profiles:
        base=profiles[0]
        if any(not profile['task'] or not profile['partition'] for profile in profiles):
            reasons.append('Falta declarar la tarea o la partición de alguna salida.')
        if any(profile['exploratory'] for profile in profiles):
            reasons.append('La inferencia exploratoria no habilita un ranking científico.')
        if any(not profile['source_hashes'] for profile in profiles):
            reasons.append('Faltan hashes de las fuentes para verificar la identidad de los datos.')
        for profile in profiles[1:]:
            for key,label in [('partition','partición'),('task','tarea')]:
                if profile[key]!=base[key]:reasons.append(f'No coincide {label} entre {base["model"]} y {profile["model"]}.')
            if (profile['category_mapping'], profile['category_mapping_version']) != (base['category_mapping'], base['category_mapping_version']):
                reasons.append('No coincide el significado o la versión de las categorías.')
            if profile['targets'] != base['targets']:
                reasons.append('No coinciden los objetivos alineados por sesión e identidad.')
            if base['source_hashes'] and profile['source_hashes'] and base['source_hashes']!=profile['source_hashes']:
                reasons.append('Las fuentes tienen hashes distintos; se inspeccionan aparte.')
        common=set.intersection(*(set(profile['valid']) for profile in profiles)) if len(profiles)>1 else set()
        cohorts=[set(profile['valid']) for profile in profiles]
        exact=all(cohort==cohorts[0] for cohort in cohorts[1:]) if cohorts else False
        if not exact:reasons.append(f'Las máscaras dejan {len(common)} identidades comunes; el ranking requiere la misma cohorte válida en todas las corridas.')
        if len(profiles)==2:
            first,second=profiles
            ids_in_order=[identity for identity in first['valid'] if identity in second['valid']]
            prediction_pairs=[{'id':identity,'first':first['valid'][identity],
                'second':second['valid'][identity]} for identity in ids_in_order[:500]]
        signatures=[]
        for profile in profiles:
            signatures.append({(m['name'],m['version'],m['direction'],m['task'],m['partition'],json.dumps(m['parameters'],sort_keys=True)):m
                               for m in profile['metrics']})
        if signatures:
            shared=set.intersection(*(set(items) for items in signatures)) if len(signatures)>1 else set()
            for signature in sorted(shared,key=str):
                metric_rows=[items[signature] for items in signatures]
                cohorts_for_metric=[set(item['observation_ids']) for item in metric_rows]
                if not all(cohort==cohorts_for_metric[0] for cohort in cohorts_for_metric[1:]):
                    continue
                metric_comparisons.append({'name':signature[0],'direction':signature[2],
                    'task':signature[3],'partition':signature[4],
                    'values':[{'model':profile['model'],'job':str(profile['job'].pk),'value':metric['value']}
                              for profile,metric in zip(profiles,metric_rows)],
                    'observations':len(cohorts_for_metric[0])})
        if not metric_comparisons:reasons.append('No hay métricas con la misma definición y los mismos IDs evaluados en todas las corridas.')
    comparable=len(profiles)>=2 and not reasons and bool(metric_comparisons)
    from storm_studio.comparison_dashboard import build_dashboard
    source_ids = {n.get('config', {}).get('source', {}).get('job') for job in selected
                  for n in (job.result.get('resolved') or {}).get('nodes', [])}
    from uuid import UUID
    valid_source_ids = []
    for value in source_ids:
        try:
            valid_source_ids.append(str(UUID(value)))
        except (ValueError, TypeError, AttributeError):
            continue
    source_ids = valid_source_ids
    sources = {str(job.pk): job for job in Job.objects.filter(pk__in=source_ids)}
    dashboard = build_dashboard(profiles, sources)
    dashboard['metrics'] = metric_comparisons
    return render(request,'storm_studio/comparisons.html',{
        'study':study,'section':'compare','title':'Comparar corridas',
        'jobs':jobs,'selected_ids':ids,'selected':selected,'profiles':profiles,
        'dashboard': dashboard, 'unavailable': [job for job in selected if not any(p['job'] == job for p in profiles)],
        'comparisons':metric_comparisons,'prediction_pairs':prediction_pairs,
        'reasons':list(dict.fromkeys(reasons)),'comparable':comparable})
