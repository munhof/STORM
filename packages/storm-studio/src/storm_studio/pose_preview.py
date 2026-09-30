"""Chunked pose-only read model used for frame-level Studio previews."""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from storm.config import json_compatible


_SAFE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
_SAFE_CHUNK = re.compile(r'^[a-f0-9]{24}-[0-9]{8}\.json\.gz$')
DEFAULT_CHUNK_SIZE = 512


def write_pose_preview_store(data, *, root, artifact_id, chunk_size=DEFAULT_CHUNK_SIZE):
    """Persist only pose coordinates in bounded, compressed per-session chunks."""
    if not _SAFE_ID.fullmatch(artifact_id):
        raise ValueError('Invalid pose preview artifact id.')
    if type(chunk_size) is not int or not 1 <= chunk_size <= DEFAULT_CHUNK_SIZE:
        raise ValueError(
            f'Pose preview chunk size must be between 1 and {DEFAULT_CHUNK_SIZE}.')

    inputs = data.get('inputs') or []
    feature_names = list(data.get('feature_names') or [])
    coordinate_indices = []
    coordinate_names = []
    feature_indices = {name: index for index, name in enumerate(feature_names)}
    for index, name in enumerate(feature_names):
        if name.endswith('_x') and f'{name[:-2]}_y' in feature_names:
            y_index = feature_indices[f'{name[:-2]}_y']
            coordinate_indices.extend((index, y_index))
            coordinate_names.extend((name, feature_names[y_index]))
    if not inputs or not coordinate_indices:
        return None

    row_count = len(inputs)
    targets = data.get('targets')
    if targets is None:
        targets = [None] * row_count
    evaluation_mask = data.get('evaluation_mask')
    if evaluation_mask is None:
        evaluation_mask = [json_compatible(target) is not None for target in targets]
    taxonomy = list(data.get('taxonomy') or [])
    fields = {
        'frames': data.get('frames') or list(range(row_count)),
        'video_frames': data.get('video_frames') or data.get('frames') or list(range(row_count)),
        'sessions': data.get('sessions') or ['session'] * row_count,
        'segments': data.get('segments') or data.get('sessions') or ['session'] * row_count,
        'observation_ids': data.get('observation_ids') or [str(i) for i in range(row_count)],
        'targets': targets,
        'evaluation_mask': evaluation_mask,
    }
    if any(len(values) != row_count for values in fields.values()):
        raise ValueError('Pose preview fields must align with input rows.')
    if any(type(value) is not bool for value in evaluation_mask):
        raise ValueError('Pose preview evaluation mask must contain booleans.')
    if any(not isinstance(value, str) for value in taxonomy):
        raise ValueError('Pose preview taxonomy must contain strings.')
    if any(len(row) <= max(coordinate_indices) for row in inputs):
        raise ValueError('Pose preview coordinates do not align with feature names.')

    root = Path(root)
    preview_root = root / 'pose_previews'
    preview_root.mkdir(parents=True, exist_ok=True)
    destination = preview_root / artifact_id
    temporary = Path(tempfile.mkdtemp(prefix=f'.{artifact_id}.', dir=preview_root))
    sessions = {}
    buffers = {}
    try:
        def write_chunk(session_id):
            buffer = buffers[session_id]
            session = sessions[session_id]
            chunk_number = len(session['chunks'])
            file_name = (f"{hashlib.sha256(session_id.encode('utf-8')).hexdigest()[:24]}-"
                         f'{chunk_number:08d}.json.gz')
            chunk_path = temporary / file_name
            payload = json.dumps(buffer, ensure_ascii=False, allow_nan=False,
                                 separators=(',', ':')).encode('utf-8')
            with chunk_path.open('wb') as raw:
                with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as compressed:
                    compressed.write(payload)
                raw.flush()
                os.fsync(raw.fileno())
            frames = [value for row in buffer
                      if (value := _frame_number(row['frame'])) is not None]
            video_frames = [value for row in buffer
                            if (value := _frame_number(row['video_frame'])) is not None]
            session['chunks'].append({
                'file': file_name,
                'offset': buffer[0]['ordinal'],
                'count': len(buffer),
                'min_frame': min(frames) if frames else None,
                'max_frame': max(frames) if frames else None,
                'min_video_frame': min(video_frames) if video_frames else None,
                'max_video_frame': max(video_frames) if video_frames else None,
                'sha256': hashlib.sha256(chunk_path.read_bytes()).hexdigest(),
            })
            buffers[session_id] = []

        for index, row in enumerate(inputs):
            session_id = str(fields['sessions'][index])
            session = sessions.setdefault(session_id, {
                'session_id': session_id, 'row_count': 0, 'chunks': [],
                'first_frame': None, 'last_frame': None,
            })
            segment = str(fields['segments'][index])
            frame = json_compatible(fields['frames'][index])
            video_frame = json_compatible(fields['video_frames'][index])
            if session['row_count'] == 0:
                session['first_frame'] = frame
            session['last_frame'] = frame
            coordinates = []
            for coordinate_index in coordinate_indices:
                try:
                    value = float(row[coordinate_index])
                except (TypeError, ValueError, OverflowError):
                    value = None
                if value is not None and not math.isfinite(value):
                    value = None
                coordinates.append(value)
            item = {
                'ordinal': session['row_count'],
                'observation_id': str(fields['observation_ids'][index]),
                'session_id': session_id,
                'frame': frame,
                'video_frame': video_frame,
                'segment': segment,
                'features': coordinates,
                'target': json_compatible(fields['targets'][index]),
                'evaluation_mask': fields['evaluation_mask'][index],
            }
            buffers.setdefault(session_id, []).append(item)
            session['row_count'] += 1
            if len(buffers[session_id]) >= chunk_size:
                write_chunk(session_id)

        for session_id, buffer in buffers.items():
            if buffer:
                write_chunk(session_id)

        manifest = {
            'schema_version': 1,
            'artifact_id': artifact_id,
            'row_count': row_count,
            'chunk_size': chunk_size,
            'feature_names': coordinate_names,
            'taxonomy': taxonomy,
            'sessions': list(sessions.values()),
        }
        manifest_path = temporary / 'manifest.json'
        manifest_path.write_text(json.dumps(
            manifest, ensure_ascii=False, allow_nan=False, separators=(',', ':')),
            encoding='utf-8')
        with manifest_path.open('rb') as stream:
            os.fsync(stream.fileno())

        backup = None
        if destination.exists():
            backup = preview_root / f'.{artifact_id}.previous-{uuid.uuid4().hex}'
            os.replace(destination, backup)
        try:
            os.replace(temporary, destination)
        except Exception:
            if backup is not None:
                os.replace(backup, destination)
            raise
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)

        return {
            'schema_version': 1,
            'artifact_id': artifact_id,
            'row_count': row_count,
            'chunk_size': chunk_size,
            'feature_names': coordinate_names,
            'sessions': [{key: value for key, value in session.items() if key != 'chunks'}
                         for session in sessions.values()],
        }
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def read_pose_preview_page(*, root, store_ref, session_id, offset=0, limit=DEFAULT_CHUNK_SIZE,
                           frame=None, frame_field='frame'):
    """Read one page or the page nearest an original pose frame."""
    artifact_id = store_ref.get('artifact_id') if isinstance(store_ref, dict) else None
    if not isinstance(artifact_id, str) or not _SAFE_ID.fullmatch(artifact_id):
        raise ValueError('Pose preview reference is invalid.')
    if type(offset) is not int or offset < 0:
        raise ValueError('Pose preview offset must be a nonnegative integer.')
    if type(limit) is not int or not 1 <= limit <= DEFAULT_CHUNK_SIZE:
        raise ValueError(f'Pose preview page size must be between 1 and {DEFAULT_CHUNK_SIZE}.')
    if frame is not None and type(frame) is not int:
        raise ValueError('Pose preview frame must be an integer.')
    if frame_field not in {'frame', 'video_frame'}:
        raise ValueError('Pose preview frame field is invalid.')

    store_path = Path(root) / 'pose_previews' / artifact_id
    manifest_path = store_path / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1 or manifest.get('artifact_id') != artifact_id:
        raise ValueError('Pose preview manifest is invalid.')
    session = next((item for item in manifest.get('sessions', [])
                    if item.get('session_id') == session_id), None)
    if session is None:
        raise KeyError(f'Unknown pose preview session: {session_id}')

    selected_index = None
    if frame is not None:
        chunks = session.get('chunks', [])
        if not chunks:
            rows = []
            offset = 0
        else:
            def frame_distance(chunk):
                minimum = _frame_number(chunk.get(f'min_{frame_field}'))
                maximum = _frame_number(chunk.get(f'max_{frame_field}'))
                if minimum is None or maximum is None:
                    return float('inf')
                if minimum <= frame <= maximum:
                    return 0
                return min(abs(frame - minimum), abs(frame - maximum))

            chunk = min(chunks, key=frame_distance)
            offset = chunk['offset']
            rows = _read_chunk(store_path, chunk)
            selected = min(rows, key=lambda row: (
                abs(_frame_number(row[frame_field]) - frame)
                if _frame_number(row[frame_field]) is not None else float('inf')))
            selected_index = selected['ordinal']
    else:
        stop = min(offset + limit, session['row_count'])
        rows = []
        for chunk in session.get('chunks', []):
            chunk_start = chunk['offset']
            chunk_stop = chunk_start + chunk['count']
            if chunk_start < stop and chunk_stop > offset:
                rows.extend(_read_chunk(store_path, chunk))
        rows = [row for row in rows if offset <= row['ordinal'] < stop]

    return {
        'session_id': session_id,
        'row_count': session['row_count'],
        'offset': offset,
        'selected_index': selected_index,
        'feature_names': manifest['feature_names'],
        'taxonomy': manifest.get('taxonomy', []),
        'rows': rows,
    }


def _read_chunk(store_path, chunk):
    file_name = chunk.get('file', '')
    if not _SAFE_CHUNK.fullmatch(file_name):
        raise ValueError('Pose preview chunk name is invalid.')
    path = store_path / file_name
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != chunk.get('sha256'):
        raise ValueError(f'Pose preview chunk {file_name} failed digest verification.')
    rows = json.loads(gzip.decompress(content).decode('utf-8'))
    if len(rows) != chunk.get('count'):
        raise ValueError(f'Pose preview chunk {file_name} has an invalid row count.')
    return rows


def _frame_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None
