"""Streaming export of the files referenced by a frozen study report."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import uuid
import zipfile

from storm.artifacts import ArtifactRef, FileArtifactStore


class FrozenReportBundleError(ValueError):
    """The frozen report references a missing, unsafe, or changed file."""


_SAFE_COMPONENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_CHUNK_SIZE = 1024 * 1024


def write_frozen_report_bundle(report, output, *, workspace, artifact_root):
    """Write a ZIP archive without loading source or artifact binaries in memory."""
    workspace = Path(workspace).resolve()
    artifact_root = Path(artifact_root).resolve()
    files = []
    members = set()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED,
                         allowZip64=True) as archive:
        _add_bytes(archive, files, members, 'README.txt', _readme())
        report_payload = {'report_revision_id': report.pk, **report.payload}
        _add_json(archive, files, members, 'report.json', report_payload)

        artifact_refs = {}
        for dataset in report.payload.get('dataset_revisions', []):
            dataset_id = dataset.get('dataset_id')
            revision_id = dataset.get('dataset_revision_id')
            for asset in dataset.get('assets', []):
                relative_path = asset.get('relative_path')
                source = _safe_file(workspace, relative_path)
                asset_id = asset.get('asset_id')
                member = (f'sources/dataset-{dataset_id}/revision-{revision_id}/'
                          f'asset-{asset_id}/{_safe_basename(relative_path)}')
                digest = asset.get('sha256')
                size = asset.get('size_bytes')
                if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                    raise FrozenReportBundleError('El hash de un archivo fuente no es válido.')
                if type(size) is not int or size < 0:
                    raise FrozenReportBundleError('El tamaño de un archivo fuente no es válido.')
                _add_file(archive, files, members, member, source,
                          expected_digest=digest, expected_size=size)
            _collect_artifact_refs(dataset.get('artifact_ref'), artifact_refs)
            preview = (dataset.get('inventory') or {}).get('pose_preview_store') or {}
            preview_id = preview.get('artifact_id')
            if preview_id:
                if not isinstance(preview_id, str) or not _SAFE_COMPONENT.fullmatch(preview_id):
                    raise FrozenReportBundleError('La referencia de la vista de pose no es válida.')
                preview_root = artifact_root / 'pose_previews' / preview_id
                if not preview_root.is_dir():
                    raise FrozenReportBundleError(
                        f'No se encontró la vista de pose {preview_id} referenciada.')
                _add_tree(archive, files, members, preview_root, artifact_root,
                          f'pose_previews/{preview_id}')

        for run in report.payload.get('runs', []):
            _collect_artifact_refs(run.get('plan'), artifact_refs)
            _collect_artifact_refs(run.get('result'), artifact_refs)
            job_id = run.get('job_id')
            try:
                job_id = str(uuid.UUID(str(job_id)))
            except (TypeError, ValueError, AttributeError) as error:
                raise FrozenReportBundleError('El reporte contiene un ID de ejecución inválido.') from error
            checkpoint_root = artifact_root / 'checkpoints' / job_id
            if checkpoint_root.is_dir():
                _add_tree(archive, files, members, checkpoint_root, artifact_root,
                          f'checkpoints/{job_id}')

        _add_artifacts(archive, files, members, artifact_refs, artifact_root)
        _add_json(archive, files, members, 'bundle_manifest.json', {
            'schema_version': 1,
            'report_revision_id': report.pk,
            'files': files,
            'limitations': [
                'Los artefactos pickle se copian como archivos opacos; no se cargan ni ejecutan.',
                'Deserializar pickle puede ejecutar código. Importá sólo paquetes confiables.',
                'Este paquete no restaura automáticamente el estudio en otra instalación.',
            ],
        })


def _readme():
    return (
        'Paquete de evidencia de STORM Studio\n\n'
        'report.json congela las corridas, sus datos y el estado visual seleccionado. '
        'bundle_manifest.json lista los archivos incluidos y sus SHA-256. Las fuentes, '
        'artefactos y checkpoints se copian por bloques; se verifican contra el digest '
        'registrado cuando está disponible.\n\n'
        'Los archivos payload.pkl se incluyen como datos opacos; esta exportación no los '
        'deserializa. Deserializar pickle puede ejecutar código, por lo que sólo deben '
        'importarse desde una fuente confiable. El paquete todavía no ofrece restauración '
        'automática del estudio.\n'
    ).encode('utf-8')


def _add_json(archive, files, members, name, value):
    _claim_member(name, members)
    digest = hashlib.sha256()
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    with archive.open(name, 'w') as destination:
        for chunk in encoder.iterencode(value):
            content = chunk.encode('utf-8')
            destination.write(content)
            digest.update(content)
            size += len(content)
    files.append({'path': name, 'sha256': digest.hexdigest(), 'size_bytes': size})


def _add_bytes(archive, files, members, name, content):
    _claim_member(name, members)
    archive.writestr(name, content)
    files.append({
        'path': name,
        'sha256': hashlib.sha256(content).hexdigest(),
        'size_bytes': len(content),
    })


def _add_file(archive, files, members, name, path, *, expected_digest=None,
              expected_size=None):
    _claim_member(name, members)
    digest = hashlib.sha256()
    size = 0
    try:
        with Path(path).open('rb') as source, archive.open(name, 'w') as destination:
            while chunk := source.read(_CHUNK_SIZE):
                destination.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        raise FrozenReportBundleError(f'No se pudo leer el archivo {name}.') from error
    actual_digest = digest.hexdigest()
    if expected_digest is not None and actual_digest != expected_digest:
        raise FrozenReportBundleError(f'El hash del archivo {name} cambió desde el reporte.')
    if expected_size is not None and size != expected_size:
        raise FrozenReportBundleError(f'El tamaño del archivo {name} cambió desde el reporte.')
    files.append({'path': name, 'sha256': actual_digest, 'size_bytes': size})


def _add_tree(archive, files, members, source_root, allowed_root, destination_root):
    source_root = _ensure_inside(source_root, allowed_root)
    if not source_root.is_dir():
        raise FrozenReportBundleError('Una carpeta vinculada al reporte no está disponible.')
    for path in sorted(source_root.rglob('*')):
        if path.is_symlink():
            raise FrozenReportBundleError('El reporte referencia un enlace simbólico no exportable.')
        if path.is_file():
            resolved = _ensure_inside(path, allowed_root)
            relative = path.relative_to(source_root).as_posix()
            _add_file(archive, files, members,
                      f'{destination_root}/{relative}', resolved)


def _add_artifacts(archive, files, members, references, artifact_root):
    store = FileArtifactStore(artifact_root)
    for (kind, artifact_id), requested in sorted(references.items()):
        try:
            actual = store.resolve(kind=kind, artifact_id=artifact_id)
            reference = ArtifactRef.from_dict(requested)
        except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
            raise FrozenReportBundleError(
                f'No se pudo validar el artefacto {kind}/{artifact_id}.') from error
        if actual != reference:
            raise FrozenReportBundleError(
                f'La referencia del artefacto {kind}/{artifact_id} cambió desde el reporte.')
        location = _ensure_inside(artifact_root / actual.uri, artifact_root)
        base = f'artifacts/{kind}/{artifact_id}'
        _add_file(archive, files, members, f'{base}/manifest.json', location / 'manifest.json')
        _add_file(archive, files, members, f'{base}/payload.pkl', location / 'payload.pkl',
                  expected_digest=actual.digest.removeprefix('sha256:'))


def _collect_artifact_refs(value, references):
    if isinstance(value, dict):
        if {'artifact_id', 'kind', 'digest', 'uri'} <= value.keys():
            kind, artifact_id = value.get('kind'), value.get('artifact_id')
            if (not isinstance(kind, str) or not _SAFE_COMPONENT.fullmatch(kind)
                    or not isinstance(artifact_id, str)
                    or not _SAFE_COMPONENT.fullmatch(artifact_id)
                    or value.get('uri') != f'{kind}/{artifact_id}'
                    or not isinstance(value.get('digest'), str)
                    or not re.fullmatch(r'sha256:[0-9a-f]{64}', value['digest'])):
                raise FrozenReportBundleError('El reporte contiene una referencia de artefacto inválida.')
            key = (kind, artifact_id)
            if key in references and references[key] != value:
                raise FrozenReportBundleError(
                    f'El reporte contiene referencias conflictivas a {kind}/{artifact_id}.')
            references[key] = value
        else:
            for item in value.values():
                _collect_artifact_refs(item, references)
    elif isinstance(value, list):
        for item in value:
            _collect_artifact_refs(item, references)


def _safe_file(root, relative_path):
    if (not isinstance(relative_path, str) or '\\' in relative_path
            or PurePosixPath(relative_path).is_absolute()
            or '..' in PurePosixPath(relative_path).parts):
        raise FrozenReportBundleError('El reporte contiene una ruta de archivo inválida.')
    path = _ensure_inside(Path(root) / relative_path, root)
    if not path.is_file():
        raise FrozenReportBundleError(f'No se encontró el archivo fuente {relative_path}.')
    return path


def _safe_basename(relative_path):
    name = PurePosixPath(relative_path).name
    if not name or name in {'.', '..'}:
        raise FrozenReportBundleError('El nombre de un archivo fuente no es válido.')
    return name


def _ensure_inside(path, root):
    try:
        resolved = Path(path).resolve(strict=True)
        resolved.relative_to(Path(root).resolve())
    except (OSError, ValueError) as error:
        raise FrozenReportBundleError('Una ruta vinculada al reporte no está disponible o es insegura.') from error
    return resolved


def _claim_member(name, members):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or name in members:
        raise FrozenReportBundleError('El reporte produciría una ruta ZIP inválida o duplicada.')
    members.add(name)
