"""Scientific source inspection executed by the persistent Studio worker."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
from fractions import Fraction

from storm.artifacts import ArtifactRef, FileArtifactStore
from storm.config import fingerprint
from storm_studio.models import DatasetAsset, DatasetRevision
from storm_studio.preview_sampling import PREVIEW_STRATEGY, sample_preview_indices
from storm_studio.pose_preview import write_pose_preview_store


def default_asset_sessions(assets) -> dict[str, str]:
    """Infer safe initial links from filenames; Studio lets researchers correct them."""
    assets = list(assets)
    poses = [asset for asset in assets if asset.role == 'pose']
    pose_sessions = {
        str(asset.pk): _adapter_session_id(asset.original_name) for asset in poses
    }
    session_ids = list(dict.fromkeys(pose_sessions.values()))
    rois = [asset for asset in assets if asset.role == 'roi']
    result = dict(pose_sessions)
    for asset in assets:
        if asset.role == 'pose':
            continue
        candidate = (_roi_session_id(asset.original_name) if asset.role == 'roi'
                     else _adapter_session_id(asset.original_name))
        if candidate in session_ids:
            result[str(asset.pk)] = candidate
        elif asset.role == 'roi' and len(rois) == 1:
            result[str(asset.pk)] = 'global'
        elif (asset.role in {'video', 'labels'} and len(session_ids) == 1
              and sum(candidate.role == asset.role for candidate in assets) == 1):
            result[str(asset.pk)] = session_ids[0]
        else:
            result[str(asset.pk)] = ''
    return result


def revision_asset_sessions(revision, assets) -> dict[str, str]:
    defaults = default_asset_sessions(assets)
    configured = revision.config.get('asset_sessions') or {}
    if not isinstance(configured, dict):
        configured = {}
    return {
        str(asset.pk): configured.get(str(asset.pk), defaults[str(asset.pk)])
        for asset in assets
    }


def connector_input(dataset_revision_id: int, *, workspace, artifact_root=None) -> dict:
    """Build the registered adapter input for inventory or model execution."""
    revision = DatasetRevision.objects.select_related('dataset').get(pk=dataset_revision_id)
    if revision.connector == 'prepared_artifact':
        if not revision.artifact_ref:
            raise ValueError('The prepared dataset has no complete artifact.')
        if artifact_root is None:
            from django.conf import settings
            artifact_root = settings.ARTIFACT_ROOT
        return FileArtifactStore(artifact_root).load(
            ArtifactRef.from_dict(revision.artifact_ref))
    assets_by_id = {asset.pk: asset for asset in DatasetAsset.objects.filter(
        dataset=revision.dataset, pk__in=revision.asset_ids)}
    assets = [assets_by_id[asset_id] for asset_id in revision.asset_ids
              if asset_id in assets_by_id]
    pose_assets = [asset for asset in assets if asset.role == 'pose']
    if not pose_assets:
        raise ValueError('La revisión no tiene archivos de pose registrados.')
    asset_sessions = revision_asset_sessions(revision, assets)
    workspace_path = Path(workspace).resolve()
    paths = {asset.pk: _source_path(workspace_path, asset.relative_path) for asset in assets}
    pose_session_ids = [asset_sessions[str(asset.pk)] for asset in pose_assets]
    data = {'pose_paths': [str(paths[asset.pk]) for asset in pose_assets],
            'pose_session_ids': pose_session_ids,
            'fps': float(revision.config.get('fps', 30)),
            'key': revision.config.get('hdf_key') or None}
    if revision.config.get('inference_only') is True:
        data['inference_only'] = True
    for key in ('canonical_taxonomy', 'label_mapping_by_session'):
        if key in revision.config:
            data[key] = revision.config[key]
    video_discontinuities = revision.inventory.get(
        'video_discontinuities_by_session', {})
    if isinstance(video_discontinuities, dict) and video_discontinuities:
        data['video_discontinuities_by_session'] = video_discontinuities
    data.update(_alignment_config(revision, assets, asset_sessions))
    session_partitions = revision.config.get('session_partitions') or {}
    for partition in ('train', 'validation', 'test'):
        data[f'{partition}_session_ids'] = [
            session for session, selected in session_partitions.items()
            if selected == partition]

    video_assets = [asset for asset in assets if asset.role == 'video']
    video_by_session = {}
    for asset in video_assets:
        session_id = asset_sessions.get(str(asset.pk))
        if not session_id:
            continue
        video_by_session[session_id] = str(paths[asset.pk])
    matched_videos = [video_by_session.get(session) for session in pose_session_ids]
    if matched_videos and all(matched_videos):
        data['video_paths'] = matched_videos

    label_assets = [asset for asset in assets if asset.role == 'labels']
    csv_labels = [asset for asset in label_assets if paths[asset.pk].suffix.lower() == '.csv']
    labels_by_session = {
        asset_sessions[str(asset.pk)]: str(paths[asset.pk]) for asset in csv_labels
        if asset_sessions.get(str(asset.pk))
    }
    if len(pose_session_ids) == 1 and pose_session_ids[0] in labels_by_session:
        data['labels_path'] = labels_by_session[pose_session_ids[0]]
    elif labels_by_session:
        data['labels_by_session'] = labels_by_session
    return data


def _configured_integer(config: dict, key: str, default: int) -> int:
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError, OverflowError):
        return default


def _alignment_config(revision, assets, asset_sessions) -> dict:
    configured_offsets = revision.config.get('video_frame_offsets') or {}
    if not isinstance(configured_offsets, dict):
        configured_offsets = {}
    reserved_ranges = revision.config.get('reserved_evaluation_ranges') or {}
    if not isinstance(reserved_ranges, dict):
        reserved_ranges = {}
    return {
        'csv_frame_base': _configured_integer(revision.config, 'csv_frame_base', 1),
        'pose_frame_base': _configured_integer(revision.config, 'pose_frame_base', 0),
        'label_frame_reference': revision.config.get('label_frame_reference', 'pose'),
        'reserved_evaluation_ranges_by_session': reserved_ranges,
        'video_frame_offsets_by_session': {
            asset_sessions[str(asset.pk)]: _configured_integer(
                configured_offsets, str(asset.pk), 0)
            for asset in assets if asset.role == 'video'
            and asset_sessions.get(str(asset.pk))
        },
    }


def inspect_video_asset(path: Path | str, *, fallback_fps: float) -> dict:
    """Read video timestamps and identify gaps without decoding image pixels."""
    try:
        probe = json.loads(subprocess.check_output([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames',
            '-show_entries',
            'stream=nb_frames,r_frame_rate,avg_frame_rate:frame=best_effort_timestamp_time',
            '-of', 'json', str(path),
        ], text=True, timeout=120))
        streams = probe.get('streams') or []
        if not streams:
            raise ValueError('ffprobe returned no video stream')
        stream = streams[0]
        frames = probe.get('frames') or []
        try:
            frame_count = int(stream.get('nb_frames'))
            if frame_count < 1:
                frame_count = len(frames)
        except (TypeError, ValueError):
            frame_count = len(frames)
        rate = fallback_fps
        for candidate in (stream.get('avg_frame_rate'), stream.get('r_frame_rate')):
            try:
                parsed = float(Fraction(candidate))
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            if math.isfinite(parsed) and parsed > 0:
                rate = parsed
                break
        timestamps = [frame.get('best_effort_timestamp_time') for frame in frames]
        timeline = _inspect_timestamps(timestamps, rate, frame_count)
        return {'frame_count': frame_count, 'fps': rate, **timeline}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as error:
        return {
            'frame_count': None, 'fps': fallback_fps,
            'discontinuities': None,
            'discontinuity_status': 'unknown_requires_review',
            'error': str(error)[:240],
        }


def _inspect_timestamps(timestamps, fps, frame_count) -> dict:
    if (not isinstance(timestamps, list) or type(frame_count) is not int
            or frame_count < 1 or len(timestamps) != frame_count
            or not math.isfinite(float(fps)) or fps <= 0):
        return {'discontinuities': None,
                'discontinuity_status': 'unknown_requires_review'}
    try:
        values = [float(value) for value in timestamps]
    except (TypeError, ValueError):
        return {'discontinuities': None,
                'discontinuity_status': 'unknown_requires_review'}
    if any(not math.isfinite(value) for value in values):
        return {'discontinuities': None,
                'discontinuity_status': 'unknown_requires_review'}
    expected_step = 1.0 / fps
    discontinuities = []
    for index, (left, right) in enumerate(zip(values, values[1:])):
        delta = right - left
        if delta <= 0 or delta > expected_step * 1.5:
            discontinuities.append({
                'after_frame': index, 'before_frame': index + 1,
                'delta_seconds': round(delta, 9),
            })
    return {
        'discontinuities': discontinuities,
        'discontinuity_status': ('gaps_detected' if discontinuities
                                 else 'verified_contiguous'),
    }


def inspect_dataset(dataset_revision_id: int, *, workspace, artifact_root, catalog, progress_callback=None) -> dict:
    revision = DatasetRevision.objects.select_related('dataset').get(pk=dataset_revision_id)
    DatasetRevision.objects.filter(pk=revision.pk).update(status='inspecting')
    assets_by_id = {asset.pk: asset for asset in DatasetAsset.objects.filter(
        dataset=revision.dataset, pk__in=revision.asset_ids)}
    assets = [assets_by_id[asset_id] for asset_id in revision.asset_ids
              if asset_id in assets_by_id]
    pose_assets = [asset for asset in assets if asset.role == 'pose']
    if not pose_assets:
        raise ValueError('La revisión no tiene archivos de pose registrados.')
    asset_sessions = revision_asset_sessions(revision, assets)

    workspace_path = Path(workspace).resolve()
    paths = {asset.pk: _source_path(workspace_path, asset.relative_path) for asset in assets}
    connector = catalog.connectors.get(revision.connector)
    if connector is None:
        raise ValueError(f'El adapter {revision.connector!r} no está registrado en el worker.')

    pose_paths = [str(paths[asset.pk]) for asset in pose_assets]
    pose_session_ids = [asset_sessions[str(asset.pk)] for asset in pose_assets]
    data = {'pose_paths': pose_paths,
            'pose_session_ids': pose_session_ids,
            'fps': float(revision.config.get('fps', 30)),
            'key': revision.config.get('hdf_key') or None}
    data.update(_alignment_config(revision, assets, asset_sessions))
    session_partitions = revision.config.get('session_partitions') or {}
    for partition in ('train', 'validation', 'test'):
        data[f'{partition}_session_ids'] = [
            session for session, selected in session_partitions.items()
            if selected == partition]
    warnings = []
    video_assets = [asset for asset in assets if asset.role == 'video']
    video_by_session = {
        asset_sessions[str(asset.pk)]: str(paths[asset.pk]) for asset in video_assets
        if asset_sessions.get(str(asset.pk))
    }
    matched_videos = [video_by_session.get(session) for session in pose_session_ids]
    if matched_videos and all(matched_videos):
        data['video_paths'] = matched_videos
    elif video_assets:
        warnings.append('Vinculá un video a cada sesión de pose para usarlo en el adapter.')

    video_timeline = []
    video_discontinuities_by_session = {}
    manual_overrides = revision.config.get(
        'video_discontinuity_overrides_by_session') or {}
    if not isinstance(manual_overrides, dict):
        raise ValueError('Los límites manuales del video deben mapear sesiones a frames.')
    unknown_override_sessions = set(manual_overrides) - set(pose_session_ids)
    if unknown_override_sessions:
        raise ValueError(
            f'Los límites manuales refieren sesiones sin pose: {sorted(unknown_override_sessions)}')
    for asset in video_assets:
        session_id = asset_sessions.get(str(asset.pk), '')
        timeline = inspect_video_asset(
            paths[asset.pk], fallback_fps=float(revision.config.get('fps', 30)))
        detected = timeline['discontinuities']
        manual = session_id in manual_overrides
        if manual:
            boundaries = manual_overrides[session_id]
            if not isinstance(boundaries, list) or any(
                    type(value) is not int or value <= 0 for value in boundaries):
                raise ValueError(
                    f'Los límites manuales de {session_id} deben ser frames enteros positivos.')
            if (timeline['frame_count'] is not None
                    and any(value >= timeline['frame_count'] for value in boundaries)):
                raise ValueError(
                    f'Los límites manuales de {session_id} deben quedar dentro del video.')
            discontinuities = [{
                'after_frame': value - 1, 'before_frame': value,
                'delta_seconds': None, 'source': 'manual',
            } for value in sorted(set(boundaries))]
            status = 'manual_boundaries_set'
        else:
            discontinuities = detected or []
            status = timeline['discontinuity_status']
        video_timeline.append({
            'file': asset.original_name,
            'session_id': session_id or 'unmatched',
            'frame_count': timeline['frame_count'],
            'fps': timeline['fps'],
            'discontinuity_status': status,
            'timestamp_status': timeline['discontinuity_status'],
            'discontinuity_source': 'manual' if manual else 'timestamps',
            'discontinuity_count': len(discontinuities or []),
            'discontinuities': (discontinuities[:25]
                                if discontinuities is not None else []),
            'detected_discontinuities': (detected[:25]
                                         if detected is not None else None),
            'error': timeline.get('error', ''),
        })
        if session_id and manual:
            video_discontinuities_by_session[session_id] = [
                item['before_frame'] for item in discontinuities]
            warnings.append(
                f"{asset.original_name}: se aplican {len(discontinuities)} límites "
                'manuales; revisá el video antes de usar esta revisión.')
        elif session_id and discontinuities:
            video_discontinuities_by_session[session_id] = sorted(set(
                video_discontinuities_by_session.get(session_id, [])
                + [item['before_frame'] for item in discontinuities]))
        if not manual and timeline['discontinuity_status'] == 'gaps_detected':
            warnings.append(
                f"{asset.original_name}: se detectaron {len(discontinuities)} saltos; "
                'las ventanas se separarán en esos frames.')
        elif not manual and timeline['discontinuity_status'] == 'unknown_requires_review':
            warnings.append(
                f"{asset.original_name}: no se pudieron verificar los timestamps del video; "
                'revisá sus discontinuidades antes de entrenar.')
    if video_discontinuities_by_session:
        data['video_discontinuities_by_session'] = video_discontinuities_by_session

    label_assets = [asset for asset in assets if asset.role == 'labels']
    csv_labels = [asset for asset in label_assets if paths[asset.pk].suffix.lower() == '.csv']
    labels_by_session = {
        asset_sessions[str(asset.pk)]: str(paths[asset.pk]) for asset in csv_labels
        if asset_sessions.get(str(asset.pk))
    }
    if len(pose_session_ids) == 1 and pose_session_ids[0] in labels_by_session:
        data['labels_path'] = labels_by_session[pose_session_ids[0]]
    elif labels_by_session:
        data['labels_by_session'] = labels_by_session
    if csv_labels and len(labels_by_session) != len(csv_labels):
        warnings.append('Vinculá cada CSV de anotaciones a su sesión de pose antes de usar sus etiquetas.')
    if len(csv_labels) != len(label_assets):
        warnings.append('El adapter de pose inspecciona CSV; otros formatos de anotación quedaron registrados sin alinear.')

    from storm.observability import ExecutionObserver
    loaded = ExecutionObserver(progress_callback, phase='inventory').call(connector, '__call__', data)
    if not isinstance(loaded, dict) or not isinstance(loaded.get('inputs'), list):
        raise ValueError('El adapter debe devolver observaciones en una lista llamada inputs.')
    row_count = len(loaded['inputs'])
    feature_names = loaded.get('feature_names') or []
    frames = loaded.get('frames') or list(range(row_count))
    sessions = loaded.get('sessions') or ['session'] * row_count
    segments = loaded.get('segments') or sessions
    partitions = loaded.get('partitions') or ['unassigned'] * row_count
    targets = loaded.get('targets') or [None] * row_count
    evaluation_mask = loaded.get('evaluation_mask')
    if evaluation_mask is None:
        evaluation_mask = [target is not None for target in targets]
    taxonomy = list(loaded.get('taxonomy') or [])
    observations = loaded.get('observation_ids') or [str(index) for index in range(row_count)]
    reserved = loaded.get('reserved_evaluation')
    if reserved is None:
        reserved = [False] * row_count
    for name, values in (('frames', frames), ('sessions', sessions), ('segments', segments),
                         ('partitions', partitions), ('targets', targets),
                         ('observation_ids', observations),
                         ('reserved_evaluation', reserved)):
        if len(values) != row_count:
            raise ValueError(f'El adapter devolvió {name} desalineado con inputs.')
    if any(type(value) is not bool for value in reserved):
        raise ValueError('El adapter debe devolver reservas de evaluación booleanas.')

    missing_values = sum(value is None or (isinstance(value, float) and math.isnan(value))
                         for row in loaded['inputs'] for value in row)
    session_summary = []
    for session in dict.fromkeys(sessions):
        indices = [index for index, value in enumerate(sessions) if value == session]
        session_frames = [frames[index] for index in indices]
        session_segments = list(dict.fromkeys(segments[index] for index in indices))
        session_summary.append({
            'session_id': session,
            'frames': len(indices),
            'first_frame': min(session_frames) if session_frames else None,
            'last_frame': max(session_frames) if session_frames else None,
            'segments': len(session_segments),
        })
    frame_counts_by_session = {
        item['session_id']: item['frames'] for item in session_summary}
    for item in video_timeline:
        item['pose_frame_count'] = frame_counts_by_session.get(item['session_id'])

    roi_assets = [item for item in assets if item.role == 'roi']
    pose_session_ids = {str(item['session_id']) for item in session_summary}
    roi_summaries = []
    for asset in roi_assets:
        try:
            content = json.loads(paths[asset.pk].read_text(encoding='utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f'No se pudo leer el ROI {asset.original_name}: {error}') from error
        roi_session = asset_sessions.get(str(asset.pk), '') or 'unmatched'
        geometry = _roi_geometry(content)
        roi_summaries.append({'file': asset.original_name,
                              'session_id': roi_session,
                              'top_level_type': type(content).__name__,
                              'keys': list(content)[:30] if isinstance(content, dict) else [],
                              'items': len(content) if isinstance(content, (dict, list)) else 1,
                              **geometry})

    video_frames = loaded.get('video_frames')
    if video_frames is None:
        video_frames = frames
    if len(video_frames) != row_count:
        raise ValueError('El adapter devolvió video_frames desalineado con inputs.')
    preview = []
    for index in sample_preview_indices(sessions, segments):
        row = loaded['inputs'][index]
        preview.append({
            'index': index,
            'observation_id': observations[index],
            'session_id': sessions[index],
            'frame': frames[index],
            'video_frame': video_frames[index],
            'segment': segments[index],
            'partition': partitions[index],
            'reserved_evaluation': reserved[index],
            'features': row,
            'target': targets[index],
            'evaluation_mask': evaluation_mask[index],
        })

    identity = fingerprint({
        'connector': revision.connector,
        'config': revision.config,
        'assets': [{'role': asset.role, 'sha256': asset.sha256,
                    'session_id': asset.session_id} for asset in assets],
    })
    pose_preview_store = write_pose_preview_store(
        loaded, root=artifact_root,
        artifact_id=f'pose-preview-dataset-{revision.dataset_id}-r{revision.number}')
    artifact_id = f'dataset-{revision.dataset_id}-r{revision.number}'
    artifact_ref = FileArtifactStore(artifact_root).save(
        kind='datasets', artifact_id=artifact_id, value=loaded,
        metadata={'dataset_revision': revision.pk, 'source_fingerprint': identity})
    summary = {
        'source_fingerprint': identity,
        'frame_count': row_count,
        'feature_names': feature_names,
        'feature_count': len(feature_names),
        'taxonomy': taxonomy,
        'session_count': len(session_summary),
        'sessions': session_summary,
        'segment_count': len(set(segments)),
        'partition_counts': {name: partitions.count(name)
                             for name in dict.fromkeys(partitions)},
        'labeled_frames': sum(value is not None for value in targets),
        'reserved_frames': sum(reserved),
        'missing_values': missing_values,
        'roi': roi_summaries,
        'video_timeline': video_timeline,
        'video_discontinuities_by_session': video_discontinuities_by_session,
        'preview_strategy': PREVIEW_STRATEGY,
        'pose_preview_store': pose_preview_store,
        'warnings': warnings,
        'assets': revision.inventory.get('assets', len(assets)),
        'roles': revision.inventory.get('roles', {}),
        'bytes': revision.inventory.get('bytes', sum(asset.size_bytes for asset in assets)),
        'preview': preview,
    }
    DatasetRevision.objects.filter(pk=revision.pk).update(
        status='ready', inventory=summary, artifact_ref=artifact_ref.to_dict())
    return {'dataset_revision': revision.pk, 'inventory': summary,
            'artifact_ref': artifact_ref.to_dict()}


def _roi_geometry(content: object) -> dict:
    """Keep the documented ROI primitives needed for a pixel-aligned preview."""
    if not isinstance(content, dict):
        return {'frame_shape': None, 'rectangles': [], 'circles': [], 'points': []}
    frame_shape = content.get('frame_shape')
    if (isinstance(frame_shape, (list, tuple)) and len(frame_shape) == 2
            and all(_positive_finite(value) for value in frame_shape)):
        frame_shape = [float(value) for value in frame_shape]
    else:
        frame_shape = None

    geometry = {'frame_shape': frame_shape}
    for kind in ('rectangles', 'circles', 'points'):
        shapes = []
        entries = content.get(kind)
        if isinstance(entries, list):
            for index, entry in enumerate(entries[:1000]):
                if not isinstance(entry, dict):
                    continue
                center = _roi_pair(entry.get('center'))
                if center is None:
                    continue
                shape = {'name': str(entry.get('name') or f'{kind[:-1]} {index + 1}')[:160],
                         'center': center}
                if kind == 'rectangles':
                    width = _positive_finite_value(entry.get('width'))
                    height = _positive_finite_value(entry.get('height'))
                    angle = _finite_value(entry.get('angle', 0))
                    if width is None or height is None or angle is None:
                        continue
                    shape.update(width=width, height=height, angle=angle)
                elif kind == 'circles':
                    radius = _positive_finite_value(entry.get('radius'))
                    if radius is None:
                        continue
                    shape['radius'] = radius
                shapes.append(shape)
        geometry[kind] = shapes
    return geometry


def _roi_pair(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    pair = [_finite_value(item) for item in value]
    return pair if all(item is not None for item in pair) else None


def _finite_value(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_finite(value):
    return _positive_finite_value(value) is not None


def _positive_finite_value(value):
    number = _finite_value(value)
    return number if number is not None and number > 0 else None


def _roi_session_id(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r'(?i)[_-]rois?$', '', stem)
    return _adapter_session_id(stem)


def _source_path(workspace: Path, relative_path: str) -> Path:
    path = (workspace / relative_path).resolve()
    if not path.is_relative_to(workspace):
        raise ValueError('Una fuente está fuera del workspace registrado.')
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _adapter_session_id(filename: str) -> str:
    stem = Path(filename).stem
    for marker in ('DLC_', 'DeepCut_'):
        if marker in stem:
            stem = stem.split(marker, 1)[0].rstrip('_')
            break
    return stem or Path(filename).stem
