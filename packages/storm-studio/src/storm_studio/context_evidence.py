"""Portable, read-only visual evidence. Model objects stay in the worker."""
import hashlib
import json
import math
from pathlib import Path
from django.conf import settings
from storm.pipeline import PipelineContext


def plain(value):
    if hasattr(value, 'tolist'):
        value = value.tolist()
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError(f'Unsupported visual value: {type(value).__name__}')


def ancestors(graph, node):
    found = {node}
    while True:
        parents = {e['source'].split('.')[0] for e in graph['edges'] if e['target'].split('.')[0] in found}
        if parents <= found:
            return found
        found |= parents


def _recovered_feature_names(graph, node, vectors):
    """Read a saved context's own declaration, never infer coordinate order."""
    nodes = {item['id']: item for item in graph['nodes']}
    edges = {edge['target']: edge['source'] for edge in graph['edges']}
    declarations = set()
    for name in ancestors(graph, node):
        item = nodes.get(name, {})
        if item.get('type') != 'adapter.saved_context':
            continue
        model_endpoint = edges.get(name + '.model', '')
        saved = nodes.get(model_endpoint.split('.')[0], {})
        original = saved.get('config', {}).get('source', {}).get('source_graph', {})
        if not original:
            continue
        required = ancestors(original, item.get('config', {}).get('node', ''))
        for declaration in original.get('nodes', []):
            parts = declaration.get('config', {}).get('bodyparts')
            if declaration['id'] in required and parts:
                declarations.add(tuple(f'{part}_{axis}' for part in parts for axis in ('x', 'y')))
    if len(declarations) != 1:
        return []
    names = next(iter(declarations))
    if not any(vectors) or any(row and len(row) != len(names) for row in vectors):
        return []
    return list(names)


def model_evidence(context, node, graph, metrics):
    n = len(context.data)
    metadata = context.metadata
    data = {}
    for key in ('observation_ids', 'sessions', 'frames', 'segments', 'video_frames', 'evaluation_mask'):
        values = metadata.get(key)
        if values is not None:
            if len(values) != n:
                raise ValueError(f'{key} must align with predictions')
            data[key] = plain(values)
    data['targets'] = plain(context.targets) if context.targets is not None else [None] * n
    if len(data['targets']) != n:
        raise ValueError('Targets must align with predictions')
    inputs = context.state.get('model_inputs')
    if inputs is not None and len(inputs) != n:
        raise ValueError('Model inputs must align with predictions')
    geometry_reason = ''
    vectors = []
    identities = data.get('observation_ids', [None] * n)
    for i in range(n):
        row = plain(inputs[i]) if inputs is not None else []
        if not isinstance(row, list):
            row = [row]
        if row and isinstance(row[0], list):
            # The anchor must be declared by provenance, never assumed at half-window.
            parents = context.state.get('window_parents')
            identity = identities[i]
            lineage = list(parents[i]) if parents is not None else []
            if identity is None or lineage.count(identity) != 1 or len(lineage) != len(row):
                row = []
                geometry_reason = 'La ventana no declara un frame de referencia alineado; no se dibuja una pose supuesta.'
            else:
                row = row[lineage.index(identity)]
        vectors.append(row)
    data['inputs'] = vectors
    configs = [item['config'] for item in graph['nodes'] if item['id'] in ancestors(graph, node)]
    parts = next((c['bodyparts'] for c in reversed(configs) if c.get('bodyparts')), [])
    data['feature_names'] = metadata.get('feature_names') or [f'{part}_{axis}' for part in parts for axis in ('x','y')]
    if not data['feature_names']:
        data['feature_names'] = _recovered_feature_names(graph, node, vectors)
    data['units'] = metadata.get('units')
    mask = plain(metadata.get('prediction_mask', [True] * n))
    if len(mask) != n or any(type(v) is not bool for v in mask):
        raise ValueError('Prediction mask must align with observations')
    output = {k: plain(metadata[k]) for k in ('task', 'semantics', 'discretizer_scope', 'embeddings',
        'category_mapping', 'category_mapping_version', 'confidence', 'confidence_semantics',
        'exploratory', 'training_population', 'model_source', 'timing') if k in metadata}
    if 'embeddings' in output and len(output['embeddings']) != n:
        raise ValueError('Embeddings must align with observations')
    selected_metrics = {}
    for key, metric in metrics.items():
        metric_node = key.split('.')[0]
        if node in ancestors(graph, metric_node):
            selected_metrics[key] = metric['value']
    model = context.artifacts.get('model')
    checkpoint = getattr(model, 'checkpoint', {}) or {}
    history = checkpoint.get('training_history', []) if isinstance(checkpoint, dict) else []
    return {'model': node, 'partition': metadata.get('partition'), 'predictions': plain(context.data),
        'indices': list(range(n)), 'prediction_mask': mask, 'resolved_data': data,
        'output_metadata': output, 'capabilities': ['group'] if metadata.get('task') == 'clustering' else [],
        'metric_definitions': [{'name': key, 'direction': metrics[key].get('direction', 'no declarada')} for key in selected_metrics],
        'metrics': selected_metrics, 'training_history': plain(history), 'geometry_reason': geometry_reason,
        'source_hashes': list(metadata.get('source_hashes', [])) + [v['source_sha256'] for v in context.artifacts.get('prepared_sessions', []) if v.get('source_sha256')]}


def save_evidence(job, outputs, graph, metrics):
    models = {node: model_evidence(ports['predictions'], node, graph, metrics)
              for node, ports in outputs.items() if isinstance(ports.get('predictions'), PipelineContext)}
    payload = json.dumps({'version': 1, 'models': models}, ensure_ascii=False, allow_nan=False).encode()
    digest = hashlib.sha256(payload).hexdigest()
    relative = Path('experiment_evidence') / str(job.pk) / f'{digest}.json'
    path = Path(settings.ARTIFACT_ROOT) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(payload)
    temporary.replace(path)
    return {'path': relative.as_posix(), 'sha256': digest}


def load_evidence(reference):
    root = Path(settings.ARTIFACT_ROOT).resolve()
    path = (root / reference['path']).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Evidence reference outside artifact store')
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != reference['sha256']:
        raise ValueError('Visual evidence checksum mismatch')
    return json.loads(payload)['models']


def recover_evidence(job):
    from storm.artifacts import ArtifactRef, FileArtifactStore
    outputs = FileArtifactStore(settings.ARTIFACT_ROOT).load(ArtifactRef.from_dict(job.result['outputs_ref']))
    reference = save_evidence(job, outputs, job.result.get('resolved') or job.revision.payload['graph'], job.result.get('metrics', {}))
    job.result = {**job.result, 'evidence_ref': reference}
    job.save(update_fields=['result'])
    return reference


def visual_context(request, study, job):
    from django.urls import reverse
    from storm_studio import services
    from storm_studio.views import _report_visuals, _posthoc_metric_analysis, _report_video_context
    from storm_studio.models import DatasetAsset
    if job.operation == 'experiment':
        if not job.result.get('evidence_ref'):
            return {'visual_pending': bool(job.result.get('outputs_ref'))}
        models = load_evidence(job.result['evidence_ref'])
        model = request.GET.get('model') or next(iter(models), '')
        if model not in models:
            raise ValueError('La salida de modelo seleccionada no existe en esta corrida.')
        result = models[model]
    elif job.operation == 'experiment_test':
        return {'visual_reason': 'La evaluación reservada conserva su manifiesto separado.'}
    else:
        models = {}
        result = services.load_execution_result(job)
        model = result.get('model', '')
    data = result.get('resolved_data', {})
    inputs = data.get('inputs') or []
    predictions = result.get('predictions') or []
    indices = result.get('indices', list(range(len(predictions))))
    mask = result.get('prediction_mask', [True] * len(predictions))
    sessions = data.get('sessions') or [None] * len(inputs)
    frames = data.get('frames') or [None] * len(inputs)
    identities = data.get('observation_ids') or list(range(len(inputs)))
    embeddings = result.get('output_metadata', {}).get('embeddings') or []
    valid = [(p, i) for p, i in enumerate(indices) if p < len(mask) and mask[p] and 0 <= i < len(inputs)]
    stride = max(1, math.ceil(len(valid)/2000))
    geometry = [{'id': identities[i], 'label': predictions[p], 'values': embeddings[p] if p < len(embeddings) else inputs[i]}
                for p, i in valid[::stride]]
    available_sessions = list(dict.fromkeys(sessions[i] for _, i in valid))
    chosen_session = request.GET.get('session')
    if chosen_session not in available_sessions:
        chosen_session = next(iter(available_sessions), None)
    selected = [(p,i) for p,i in valid if sessions[i] == chosen_session]
    try:
        offset = max(0, int(request.GET.get('frame_page','0'))) * 500
    except ValueError:
        offset = 0
    offset = min(offset, max(0, (len(selected)-1)//500*500))
    pose = [{'id': identities[i], 'frame': frames[i], 'session': sessions[i], 'label': predictions[p],
             'values': inputs[i], 'target': (data.get('targets') or [None]*len(inputs))[i]}
            for p,i in selected[offset:offset+500]]
    revision = study.dataset_revision
    video = None
    video_reason = 'No hay un video registrado y alineado con la fuente de esta corrida.'
    if revision:
        assets = list(DatasetAsset.objects.filter(pk__in=revision.asset_ids))
        hashes = set(result.get('source_hashes', []))
        if any(asset.role == 'video' for asset in assets):
            video_reason = ('Los videos del dataset activo no corresponden a las fuentes y sesiones de esta corrida: '
                            + ', '.join(str(session) for session in available_sessions if session is not None)
                            + '. Seleccioná una corrida sobre esas fuentes o vinculá los videos de las sesiones de esta corrida.')
        if hashes and hashes <= {asset.sha256 for asset in assets if asset.role == 'pose'}:
            result['spec'] = {'dataset_revision_id': revision.pk}
        video = _report_video_context(study, revision, assets, revision.config.get('asset_sessions', {}),
                                      job, result, request.GET.get('video_session',''))
    analysis = _posthoc_metric_analysis(result, request.GET.getlist('metric'), services.catalog())
    return {'visual_result': result, 'visual_models': list(models), 'visual_model': model,
        'report_visuals': _report_visuals(result), 'analysis_metrics': analysis, 'report_video': video, 'video_reason': video_reason,
        'visual_sessions': available_sessions, 'visual_session': chosen_session,
        'pose_page': offset//500, 'pose_has_next': offset+500 < len(selected),
        'context_visual_data': {'geometry': geometry, 'pose': pose, 'features': data.get('feature_names', []),
            'geometry_kind': 'Espacio latente' if embeddings else 'Variables de entrada',
            'units': data.get('units'), 'sample_count': len(geometry), 'total_count': len(valid)},
        'visual_reason': result.get('geometry_reason', '')}
