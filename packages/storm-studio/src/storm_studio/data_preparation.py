"""Materialize an immutable, prepared dataset from a saved pipeline revision."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from storm.artifacts import ArtifactRef, FileArtifactStore
from storm.config import fingerprint
from storm.suite import transform_aligned
from storm_studio.models import DatasetRevision, Revision
from storm_studio.preview_sampling import PREVIEW_STRATEGY, sample_preview_indices
from storm_studio.pose_preview import write_pose_preview_store


ALIGNED_FIELDS = (
    'targets', 'observation_ids', 'frames', 'sessions', 'segments', 'partitions',
    'video_frames', 'evaluation_mask', 'reserved_evaluation', 'likelihoods', 'groups',
)


def resolve_preparation_steps(steps, feature_names):
    """Compatibility bridge; scientific resolution belongs to the plugin."""
    from storm_studio.services import catalog

    resolver = catalog().preparation_resolver
    if resolver is None:
        return _legacy_resolve_preparation_steps(steps, feature_names)
    return resolver(steps, feature_names)


def _legacy_resolve_preparation_steps(steps, feature_names):
    """Resolve bodypart choices against the features available at each step."""
    current_features = list(feature_names or [])
    resolved = []
    for step in steps:
        item = deepcopy(step)
        step_type = item.get('type')
        config = dict(item.get('config') or {})
        if step_type == 'pose.select_coordinates':
            names = config.get('names')
            if not isinstance(names, list) or not names:
                raise ValueError('Select at least one pose coordinate.')
            missing = [name for name in names if name not in current_features]
            if missing:
                raise ValueError(f'Unknown pose coordinates: {missing}')
            current_features = names
        elif step_type == 'pose.recenter' and 'center_bodypart' in config:
            center = config.get('center_bodypart')
            bodyparts = config.get('bodyparts')
            center_indices = _bodypart_pair(current_features, center)
            pairs = [_bodypart_pair(current_features, name) for name in bodyparts or []]
            config = {'center_indices': center_indices, 'coordinate_pairs': pairs}
            if 'device' in item.get('config', {}):
                config['device'] = item['config']['device']
        elif step_type == 'pose.orient_coordinates' and 'from_bodypart' in config:
            requested_device = config.get('device')
            bodyparts = list(dict.fromkeys(
                name[:-2] for name in current_features
                if name.endswith('_x') and f'{name[:-2]}_y' in current_features))
            reference_pairs = [
                _bodypart_pair(current_features, config.get('from_bodypart')),
                _bodypart_pair(current_features, config.get('toward_bodypart')),
            ]
            coordinate_pairs = [_bodypart_pair(current_features, name)
                                for name in bodyparts]
            config = {
                'reference_pairs': reference_pairs,
                'coordinate_pairs': coordinate_pairs,
                'target_angle_degrees': config.get('target_angle_degrees', 45),
                'degenerate_reference_policy': config.get(
                    'degenerate_reference_policy', 'error'),
            }
            if requested_device is not None:
                config['device'] = requested_device
        elif step_type == 'pose.likelihood_filter' and 'bodyparts' in config:
            pairs = [_bodypart_pair(current_features, name)
                     for name in config.get('bodyparts') or []]
            config = {'threshold': config.get('threshold'), 'coordinate_pairs': pairs}
        if 'config' in item or (isinstance(step_type, str) and step_type.startswith('pose.')):
            item['config'] = config
        resolved.append(item)
    return resolved


def _bodypart_pair(feature_names, bodypart):
    if not isinstance(bodypart, str) or not bodypart:
        raise ValueError('Choose a body part for the pose transformation.')
    names = (f'{bodypart}_x', f'{bodypart}_y')
    if any(name not in feature_names for name in names):
        raise ValueError(f'Body part {bodypart!r} is unavailable at this step.')
    return [feature_names.index(name) for name in names]



def _step_versions(steps, catalog):
    versions = []
    for step in steps:
        step_type = step['type']
        if step_type == 'center':
            version = '1'
        else:
            config = ({'factor': step['factor']} if step_type == 'scale'
                      else step.get('config', {}))
            version = getattr(catalog.steps.build(step_type, config), 'version', '1')
        versions.append({'type': step_type, 'version': str(version)})
    return versions


def materialize_preparation(revision_id, execution_id, *, artifact_root, catalog, progress_callback=None):
    recipe = Revision.objects.select_related('study').get(
        pk=revision_id, kind='preparation')
    source = DatasetRevision.objects.get(
        pk=recipe.payload['dataset_revision_id'], status='ready')
    from storm_studio.video_timeline_reviews import require_review

    require_review(recipe.study, source)
    if not source.artifact_ref:
        raise ValueError('The source dataset has no inspected artifact to prepare.')

    store = FileArtifactStore(artifact_root)
    data = None
    feature_names = source.inventory.get('feature_names', [])
    source_count = source.inventory.get('frame_count')
    if not isinstance(feature_names, list) or not feature_names:
        data = store.load(ArtifactRef.from_dict(source.artifact_ref))
        if (not isinstance(data, dict) or not isinstance(data.get('inputs'), list)
                or not data['inputs']):
            raise ValueError('The source artifact must contain a nonempty inputs list.')
        feature_names = data.get('feature_names', [])
        source_count = len(data['inputs'])
    steps = resolve_preparation_steps(recipe.payload.get('steps', []), feature_names)
    step_versions = _step_versions(steps, catalog)
    recipe_fingerprint = fingerprint({
        'source': source.artifact_ref, 'steps': steps, 'step_versions': step_versions})
    existing = next((item for item in DatasetRevision.objects.filter(
        dataset=source.dataset, connector='prepared_artifact').order_by('number')
        if item.config.get('source_dataset_revision_id') == source.pk
        and item.config.get('preparation_revision_id') == recipe.pk
        and item.config.get('preparation_fingerprint') == recipe_fingerprint), None)
    if existing:
        return {
            'prepared_dataset_revision_id': existing.pk,
            'source_dataset_revision_id': source.pk,
            'preparation_revision_id': recipe.pk,
            'preparation_fingerprint': recipe_fingerprint,
            'frame_count': existing.inventory.get('frame_count', 0),
            'artifact_ref': existing.artifact_ref,
            'reused': True,
        }
    if type(source_count) is not int or source_count < 1:
        if data is None:
            data = store.load(ArtifactRef.from_dict(source.artifact_ref))
        source_count = len(data.get('inputs') or [])
    recovered = _recover_orphan_preparation(
        store, artifact_root, source=source, recipe=recipe, steps=steps,
        step_versions=step_versions, recipe_fingerprint=recipe_fingerprint,
        source_observation_count=source_count)
    if recovered:
        artifact_ref, prepared, recovered_execution = recovered
        dataset = source.dataset
        next_number = (DatasetRevision.objects.filter(dataset=dataset)
                       .order_by('-number').values_list('number', flat=True).first() or 0) + 1
        pose_preview_store = write_pose_preview_store(
            _pose_preview_data(prepared, steps), root=artifact_root,
            artifact_id=f'pose-preview-prepared-{dataset.pk}-r{next_number}')
        summary = _inventory(
            _pose_preview_data(prepared, steps), source.inventory, recipe_fingerprint,
            pose_preview_store=pose_preview_store)
        stage_preview = (prepared.get('preparation') or {}).get('stage_preview', [])
        summary['preparation_stage_preview'] = stage_preview
        derived = DatasetRevision.objects.create(
            dataset=dataset, number=next_number, connector='prepared_artifact',
            asset_ids=source.asset_ids,
            config={
                'source_dataset_revision_id': source.pk,
                'preparation_revision_id': recipe.pk,
                'preparation_fingerprint': recipe_fingerprint,
                'step_versions': step_versions,
                'recovered_artifact': True,
                'recovered_from_execution_id': recovered_execution,
            },
            status='ready', inventory=summary, artifact_ref=artifact_ref.to_dict())
        return {
            'prepared_dataset_revision_id': derived.pk,
            'source_dataset_revision_id': source.pk,
            'preparation_revision_id': recipe.pk,
            'preparation_fingerprint': recipe_fingerprint,
            'frame_count': len(prepared['inputs']),
            'artifact_ref': artifact_ref.to_dict(),
            'recovered': True,
            'recovered_from_execution_id': recovered_execution,
        }
    if data is None:
        data = store.load(ArtifactRef.from_dict(source.artifact_ref))
    if not isinstance(data, dict) or not isinstance(data.get('inputs'), list) or not data['inputs']:
        raise ValueError('The source artifact must contain a nonempty inputs list.')
    inputs = data['inputs']
    count = len(inputs)
    partitions = data.get('partitions') or ['unassigned'] * count
    reserved = data.get('reserved_evaluation') or [False] * count
    if len(partitions) != count or len(reserved) != count:
        raise ValueError('Partition and reservation metadata must align with inputs.')
    for key in ALIGNED_FIELDS:
        if key in data and data[key] is not None and len(data[key]) != count:
            raise ValueError(f'{key} must align with inputs.')
    train_indices = [i for i, partition in enumerate(partitions) if partition == 'train']
    train_set = set(train_indices)
    fit_indices = [i for i in train_indices if not reserved[i]]
    if not fit_indices:
        raise ValueError('Assign at least one unreserved observation to training.')
    evaluation_indices = [i for i in range(count) if i not in train_set or reserved[i]]
    metadata = {key: data[key] for key in (
        'feature_names', 'taxonomy', 'likelihood_bodyparts', 'likelihoods', 'frames',
        'sessions', 'segments', 'partitions', 'reserved_evaluation') if key in data}

    def prepare_partition(name, values, indices, **options):
        prefix = f'preparation-{recipe.pk}-{recipe_fingerprint.removeprefix("sha256:")}-{name}'
        resume = None
        for number in range(len(steps), 0, -1):
            try:
                saved = store.load(store.resolve(kind='preparation_steps',
                                                 artifact_id=f'{prefix}-{number}'))
            except FileNotFoundError:
                continue
            if saved.get('fingerprint') != recipe_fingerprint:
                raise ValueError('Preparation step checkpoint fingerprint differs.')
            resume = saved['state']
            if progress_callback is not None:
                progress_callback({'label': f'Preparación {name}: recuperados {number} de {len(steps)} pasos',
                                   'phase_step': number, 'phase_total': len(steps),
                                   'unit_label': 'pasos de preparación', 'checkpoint_saved': True,
                                   'recovered_from_execution_id': saved['execution_id']})
            break

        def save_step(state):
            number = state['completed_steps']
            store.save(kind='preparation_steps', artifact_id=f'{prefix}-{number}',
                       value={'fingerprint': recipe_fingerprint, 'state': state,
                              'execution_id': execution_id}, metadata={
                           'schema_version': 1, 'source_dataset_revision': source.pk,
                           'preparation_revision': recipe.pk, 'partition': name,
                           'completed_steps': number, 'step_versions': step_versions})
            if progress_callback is not None:
                progress_callback({'label': f'Punto de recuperación guardado: {name}, paso {number} de {len(steps)}',
                                   'checkpoint_saved': True})

        return transform_aligned(values, indices, steps, catalog=catalog,
            context_metadata=metadata, progress_callback=progress_callback,
            checkpoint_callback=save_step, resume_state=resume, **options)

    training_stage_trace = []
    checkpoint_id = f'preparation-{recipe.pk}-{recipe_fingerprint.removeprefix("sha256:")}-training'
    try:
        checkpoint = store.load(store.resolve(kind='preparation_checkpoints',
                                             artifact_id=checkpoint_id))
    except FileNotFoundError:
        checkpoint = None
    if checkpoint is not None:
        if (checkpoint.get('fingerprint') != recipe_fingerprint
                or checkpoint.get('train_indices') != train_indices
                or checkpoint.get('fit_indices') != fit_indices):
            raise ValueError('Preparation checkpoint does not match the training partition.')
        prepared_train = checkpoint['inputs']
        fitted = checkpoint['fitted']
        selected_train = checkpoint['selected']
        training_stage_trace = checkpoint['stage_trace']
        if progress_callback is not None:
            progress_callback({'label': 'Entrenamiento preparado recuperado; continuando con evaluación',
                               'checkpoint_saved': True,
                               'recovered_from_execution_id': checkpoint['execution_id']})
    else:
        prepared_train, fitted, selected_train = prepare_partition(
            'training', [inputs[i] for i in train_indices], train_indices,
            stage_trace=training_stage_trace,
            fit_observation_indices=fit_indices)
        store.save(kind='preparation_checkpoints', artifact_id=checkpoint_id, value={
            'fingerprint': recipe_fingerprint, 'train_indices': train_indices,
            'fit_indices': fit_indices, 'inputs': prepared_train, 'fitted': fitted,
            'selected': selected_train, 'stage_trace': training_stage_trace,
            'execution_id': execution_id}, metadata={
                'schema_version': 1, 'source_dataset_revision': source.pk,
                'preparation_revision': recipe.pk, 'step_versions': step_versions})
        if progress_callback is not None:
            progress_callback({'label': 'Punto de recuperación de preparación guardado: entrenamiento',
                               'checkpoint_saved': True})
    evaluation_stage_trace = []
    if evaluation_indices:
        prepared_evaluation, _, selected_evaluation = prepare_partition(
            'evaluation', [inputs[i] for i in evaluation_indices], evaluation_indices,
            learned=fitted, stage_trace=evaluation_stage_trace)
    else:
        prepared_evaluation, selected_evaluation = [], []
        evaluation_stage_trace = [
            {'type': step['type'], 'row_count': 0, 'rows': []} for step in steps]

    by_index = dict(zip(selected_train, prepared_train))
    by_index.update(zip(selected_evaluation, prepared_evaluation))
    selected = sorted(by_index)
    if not selected:
        raise ValueError('The preparation pipeline removed every observation.')
    prepared = deepcopy(data)
    prepared['inputs'] = [by_index[index] for index in selected]
    for key in ALIGNED_FIELDS:
        if key in data and data[key] is not None:
            prepared[key] = [data[key][index] for index in selected]
    prepared['train'] = [index for index, source_index in enumerate(selected)
                         if partitions[source_index] == 'train' and not reserved[source_index]]
    prepared['validation'] = [index for index, source_index in enumerate(selected)
                              if partitions[source_index] == 'validation']
    prepared['test'] = [index for index, source_index in enumerate(selected)
                        if partitions[source_index] == 'test']
    feature_names = list(data.get('feature_names', []))
    likelihood_bodyparts = list(data.get('likelihood_bodyparts', []))
    for step in steps:
        if step.get('type') == 'pose.select_coordinates':
            feature_names = list(step.get('config', {}).get('names', feature_names))
            selected_bodyparts = [
                name[:-2] for name in feature_names
                if name.endswith('_x') and f'{name[:-2]}_y' in feature_names]
            if prepared.get('likelihoods') and likelihood_bodyparts:
                likelihood_indices = [likelihood_bodyparts.index(name)
                                      for name in selected_bodyparts
                                      if name in likelihood_bodyparts]
                prepared['likelihoods'] = [
                    [row[index] for index in likelihood_indices]
                    for row in prepared['likelihoods']]
                likelihood_bodyparts = [likelihood_bodyparts[index]
                                        for index in likelihood_indices]
    prepared['feature_names'] = feature_names
    if 'likelihood_bodyparts' in data:
        prepared['likelihood_bodyparts'] = likelihood_bodyparts
    prepared['data_fingerprint'] = recipe_fingerprint

    def stage_rows(trace):
        observation_ids = data.get('observation_ids') or [str(i) for i in range(count)]
        sessions = data.get('sessions') or ['session'] * count
        frames = data.get('frames') or list(range(count))
        segments = data.get('segments') or sessions
        reserved = data.get('reserved_evaluation') or [False] * count
        result = []
        for row in trace['rows']:
            index = row['observation_index']
            result.append({
                'observation_index': index,
                'observation_id': observation_ids[index],
                'session_id': sessions[index],
                'frame': frames[index],
                'segment': segments[index],
                'reserved_evaluation': reserved[index],
                'value': row['value'],
            })
        return result

    stage_preview = [{
        'type': step['type'],
        'training_row_count': training_stage_trace[index]['row_count'],
        'evaluation_row_count': evaluation_stage_trace[index]['row_count'],
        'training': stage_rows(training_stage_trace[index]),
        'evaluation': stage_rows(evaluation_stage_trace[index]),
    } for index, step in enumerate(steps)]
    prepared['preparation'] = {
        'revision_id': recipe.pk,
        'name': recipe.payload.get('name', 'Preparación'),
        'fingerprint': recipe_fingerprint,
        'execution_id': str(execution_id),
        'resolved_steps': steps,
        'step_versions': step_versions,
        'source_dataset_revision_id': source.pk,
        'fitted_steps': fitted,
        'source_observation_indices': selected,
        'stage_preview': stage_preview,
    }

    dataset = source.dataset
    next_number = (DatasetRevision.objects.filter(dataset=dataset)
                   .order_by('-number').values_list('number', flat=True).first() or 0) + 1
    artifact_id = f'prepared-dataset-{dataset.pk}-r{next_number}'
    artifact_ref = store.save(
        kind='datasets', artifact_id=artifact_id, value=prepared,
        metadata={'source_dataset_revision': source.pk,
                  'preparation_revision': recipe.pk,
                  'preparation_fingerprint': recipe_fingerprint,
                  'step_versions': step_versions,
                  'execution_id': str(execution_id)})
    preview_data = _pose_preview_data(prepared, steps)
    pose_preview_store = write_pose_preview_store(
        preview_data, root=artifact_root,
        artifact_id=f'pose-preview-prepared-{dataset.pk}-r{next_number}')
    summary = _inventory(preview_data, source.inventory, recipe_fingerprint,
                         pose_preview_store=pose_preview_store)
    summary['preparation_stage_preview'] = stage_preview
    derived = DatasetRevision.objects.create(
        dataset=dataset,
        number=next_number,
        connector='prepared_artifact',
        asset_ids=source.asset_ids,
        config={
            'source_dataset_revision_id': source.pk,
            'preparation_revision_id': recipe.pk,
            'preparation_fingerprint': recipe_fingerprint,
            'step_versions': step_versions,
        },
        status='ready',
        inventory=summary,
        artifact_ref=artifact_ref.to_dict(),
    )
    return {
        'prepared_dataset_revision_id': derived.pk,
        'source_dataset_revision_id': source.pk,
        'preparation_revision_id': recipe.pk,
        'preparation_fingerprint': recipe_fingerprint,
        'frame_count': len(prepared['inputs']),
        'artifact_ref': artifact_ref.to_dict(),
    }


def _recover_orphan_preparation(store, artifact_root, *, source, recipe, steps,
                                step_versions, recipe_fingerprint,
                                source_observation_count):
    """Find and validate a complete artifact written before its DB revision."""
    artifact_root = Path(artifact_root)
    datasets_root = artifact_root / 'datasets'
    prefix = f'prepared-dataset-{source.dataset_id}-r'
    if not datasets_root.is_dir():
        return None
    candidates = []
    for path in datasets_root.glob(f'{prefix}*'):
        try:
            number = int(path.name[len(prefix):])
        except ValueError:
            continue
        candidates.append((number, path.name))
    for _, artifact_id in sorted(candidates, reverse=True):
        try:
            manifest_path = datasets_root / artifact_id / 'manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            metadata = manifest.get('metadata') or {}
            if (metadata.get('source_dataset_revision') != source.pk
                    or metadata.get('preparation_revision') != recipe.pk
                    or metadata.get('preparation_fingerprint') != recipe_fingerprint
                    or metadata.get('step_versions') != step_versions):
                continue
            reference = store.resolve(kind='datasets', artifact_id=artifact_id)
            value = store.load(reference)
            preparation = value.get('preparation') if isinstance(value, dict) else None
            if (not isinstance(value, dict) or not isinstance(value.get('inputs'), list)
                    or not value['inputs'] or not isinstance(preparation, dict)
                    or preparation.get('revision_id') != recipe.pk
                    or preparation.get('source_dataset_revision_id') != source.pk
                    or preparation.get('fingerprint') != recipe_fingerprint
                    or preparation.get('step_versions') != step_versions):
                continue
            count = len(value['inputs'])
            if any(key in value and value[key] is not None and len(value[key]) != count
                   for key in ALIGNED_FIELDS):
                continue
            source_indices = preparation.get('source_observation_indices')
            if (not isinstance(source_indices, list) or len(source_indices) != count
                    or any(type(index) is not int
                           or not 0 <= index < source_observation_count
                           for index in source_indices)):
                continue
            return reference, value, metadata.get('execution_id',
                                                   preparation.get('execution_id', ''))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    return None


def _pose_preview_data(data, steps):
    """Project temporal windows to their center frame for frame-level visuals."""
    temporal = next((step for step in reversed(steps)
                     if step.get('type') == 'pose.temporal_windows'), None)
    if temporal is None:
        return data
    inputs = data.get('inputs') or []
    if not inputs or not isinstance(inputs[0], (list, tuple)) or not inputs[0] \
            or not isinstance(inputs[0][0], (list, tuple)):
        return data
    offsets = (temporal.get('config') or {}).get('offsets') or []
    center = offsets.index(0) if 0 in offsets else len(inputs[0]) // 2
    if any(not isinstance(row, (list, tuple)) or center >= len(row)
           or not isinstance(row[center], (list, tuple)) for row in inputs):
        return data
    projected = dict(data)
    projected['inputs'] = [row[center] for row in inputs]
    return projected


def _inventory(data, source_inventory, source_fingerprint, *, pose_preview_store=None):
    inputs = data['inputs']
    frames = data.get('frames') or list(range(len(inputs)))
    video_frames = data.get('video_frames') or frames
    sessions = data.get('sessions') or ['session'] * len(inputs)
    segments = data.get('segments') or sessions
    partitions = data.get('partitions') or ['unassigned'] * len(inputs)
    targets = data.get('targets') or [None] * len(inputs)
    evaluation_mask = data.get('evaluation_mask')
    if evaluation_mask is None:
        evaluation_mask = [target is not None for target in targets]
    observation_ids = data.get('observation_ids') or [str(index) for index in range(len(inputs))]
    preview = [{
        'index': index,
        'observation_id': observation_ids[index],
        'session_id': sessions[index],
        'frame': frames[index],
        'video_frame': video_frames[index],
        'segment': segments[index],
        'partition': partitions[index],
        'features': inputs[index],
        'target': targets[index],
        'evaluation_mask': evaluation_mask[index],
    } for index in sample_preview_indices(sessions, segments)
    ]
    return {
        **{key: value for key, value in source_inventory.items()
           if key not in {'preview', 'frame_count', 'feature_names', 'feature_count',
                          'partition_counts', 'labeled_frames'}},
        'source_fingerprint': source_fingerprint,
        'frame_count': len(inputs),
        'preview_strategy': PREVIEW_STRATEGY,
        'pose_preview_store': pose_preview_store,
        'feature_names': data.get('feature_names', []),
        'feature_count': len(data.get('feature_names', [])),
        'taxonomy': list(data.get('taxonomy') or source_inventory.get('taxonomy') or []),
        'partition_counts': {name: partitions.count(name)
                             for name in dict.fromkeys(partitions)},
        'labeled_frames': sum(target is not None for target in targets),
        'sessions': [
            {'session_id': session,
             'frames': sum(value == session for value in sessions),
             'segments': len(dict.fromkeys(
                 segments[index] for index, value in enumerate(sessions) if value == session))}
            for session in dict.fromkeys(sessions)
        ],
        'session_count': len(set(sessions)),
        'segment_count': len(set(segments)),
        'preview': preview,
    }
