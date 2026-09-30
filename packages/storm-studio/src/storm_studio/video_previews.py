"""Create and locate browser-compatible copies of registered videos."""

import hashlib
import os
from pathlib import Path
import subprocess

from storm_studio.models import DatasetAsset, DatasetRevision, Job


def latest_video_preview_job(study_id, dataset_revision_id, asset_id):
    jobs = Job.objects.filter(
        revision__study_id=study_id, operation='video_preview'
    ).select_related('revision').order_by('-created')
    for job in jobs:
        payload = job.revision.payload
        if (payload.get('dataset_revision') == dataset_revision_id
                and payload.get('asset_id') == asset_id):
            return job
    return None


def preview_path(job, workspace):
    if not job or job.status != 'completed':
        return None
    relative_path = job.result.get('relative_path')
    if not isinstance(relative_path, str) or not relative_path:
        return None
    root = Path(workspace).resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size < 1:
        return None
    return path


def materialize_video_preview(dataset_revision_id, asset_id, workspace):
    revision = DatasetRevision.objects.select_related('dataset').get(pk=dataset_revision_id)
    if asset_id not in revision.asset_ids:
        raise ValueError('El video no pertenece a la revisión de datos seleccionada.')
    asset = DatasetAsset.objects.get(pk=asset_id, dataset_id=revision.dataset_id, role='video')
    root = Path(workspace).resolve()
    source = (root / asset.relative_path).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise FileNotFoundError('No se encontró el video registrado en el workspace.')

    cache_key = hashlib.sha256(
        f'{asset.pk}:{asset.sha256}'.encode('utf-8')).hexdigest()
    relative_path = Path('video_previews') / f'{cache_key}.mp4'
    destination = root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file() or destination.stat().st_size < 1:
        temporary = destination.with_name(f'.{cache_key}.partial.mp4')
        temporary.unlink(missing_ok=True)
        try:
            subprocess.run([
                'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                '-i', str(source), '-map', '0:v:0', '-an',
                '-vf', "scale=w='min(960,iw)':h=-2", '-c:v', 'libx264',
                '-preset', 'veryfast', '-crf', '27', '-pix_fmt', 'yuv420p',
                '-fps_mode', 'passthrough', '-movflags', '+faststart',
                '-f', 'mp4', str(temporary),
            ], check=True, capture_output=True, text=True)
            if not temporary.is_file() or temporary.stat().st_size < 1:
                raise RuntimeError('ffmpeg no produjo una vista previa de video válida.')
            os.replace(temporary, destination)
        except subprocess.CalledProcessError as error:
            details = (error.stderr or '').strip()[-1000:]
            raise RuntimeError(
                'ffmpeg no pudo preparar una copia de revisión.'
                + (f' {details}' if details else '')) from error
        finally:
            temporary.unlink(missing_ok=True)

    return {
        'dataset_revision': revision.pk,
        'asset_id': asset.pk,
        'source_sha256': asset.sha256,
        'relative_path': relative_path.as_posix(),
        'sha256': _sha256_file(destination),
        'size_bytes': destination.stat().st_size,
        'video_codec': 'h264',
        'container': 'mp4',
    }


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
