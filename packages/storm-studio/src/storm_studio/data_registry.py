"""Content-addressed storage for source files registered by Studio."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile


ROLE_EXTENSIONS = {
    'roi': {'.json'},
    'video': {'.mp4', '.avi', '.mov', '.mkv', '.m4v', '.mpeg', '.mpg', '.webm'},
    'labels': {'.csv', '.tsv', '.json'},
}
POSE_ADAPTER_EXTENSIONS = {
    'dlc_h5': {'.h5', '.hdf', '.hdf5'},
    'dlc_csv': {'.csv'},
}
GENERIC_POSE_EXTENSIONS = {'.csv', '.h5', '.hdf', '.hdf5', '.nwb', '.parquet'}


def validate_uploads(pose_adapter: str, files_by_role: dict[str, list]) -> None:
    """Check the selected adapters and source file types before writing files."""
    poses = files_by_role.get('pose', [])
    if not poses:
        raise ValueError('Seleccioná al menos un archivo de pose.')

    accepted_pose_extensions = POSE_ADAPTER_EXTENSIONS.get(
        pose_adapter, GENERIC_POSE_EXTENSIONS)
    for role, files in files_by_role.items():
        allowed = accepted_pose_extensions if role == 'pose' else ROLE_EXTENSIONS[role]
        for uploaded in files:
            name = str(getattr(uploaded, 'name', '')).replace('\\', '/')
            suffix = Path(name).suffix.lower()
            if not Path(name).name or not suffix or suffix not in allowed:
                formats = ', '.join(sorted(allowed))
                raise ValueError(f'Formato no admitido para {role}: {name or "archivo sin nombre"}. Usá {formats}.')


def store_upload(workspace: Path, uploaded) -> dict:
    """Write an uploaded file atomically and return its source manifest."""
    source_root = Path(workspace).resolve() / 'data_sources'
    source_root.mkdir(parents=True, exist_ok=True)
    safe_name = Path(str(uploaded.name).replace('\\', '/')).name
    suffix = Path(safe_name).suffix.lower()
    descriptor, temporary_name = tempfile.mkstemp(prefix='.upload-', dir=source_root)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, 'wb') as destination:
            for chunk in uploaded.chunks():
                digest.update(chunk)
                destination.write(chunk)
                size += len(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if size == 0:
            raise ValueError(f'El archivo {safe_name} está vacío.')

        sha256 = digest.hexdigest()
        relative_path = Path('data_sources') / sha256[:2] / f'{sha256}{suffix}'
        stored_path = Path(workspace).resolve() / relative_path
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        if stored_path.exists():
            Path(temporary_name).unlink()
        else:
            os.replace(temporary_name, stored_path)
        return {
            'original_name': safe_name,
            'relative_path': relative_path.as_posix(),
            'sha256': sha256,
            'size_bytes': size,
            'session_id': Path(safe_name).stem[:160],
        }
    finally:
        temporary = Path(temporary_name)
        if temporary.exists():
            temporary.unlink()
