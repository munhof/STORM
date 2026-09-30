"""Immutable manifests for selected study runs and their review context."""
from __future__ import annotations

import uuid

from django.utils import timezone

from storm_studio.models import DatasetAsset, DatasetRevision, Job, Revision


def freeze_study_report(study, job_ids, *, snapshot_revision_id=None):
    """Capture report inputs in an immutable study revision."""
    try:
        selected_ids = list(dict.fromkeys(str(uuid.UUID(str(value))) for value in job_ids))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError('Las ejecuciones seleccionadas contienen un ID inválido.') from error
    if not selected_ids:
        raise ValueError('Seleccioná al menos una ejecución completada.')

    selected_jobs = Job.objects.filter(
        pk__in=selected_ids, revision__study=study, status='completed'
    ).select_related('revision')
    jobs_by_id = {str(job.pk): job for job in selected_jobs
                  if job.operation not in {'inventory', 'prepare', 'video_preview'}}
    if len(jobs_by_id) != len(selected_ids):
        raise ValueError('Una ejecución no pertenece a este estudio o no está completada.')

    snapshot = None
    if snapshot_revision_id not in (None, ''):
        try:
            snapshot = Revision.objects.get(
                pk=snapshot_revision_id, study=study, kind='snapshot')
        except (Revision.DoesNotExist, TypeError, ValueError) as error:
            raise ValueError('El snapshot seleccionado no pertenece a este estudio.') from error

    runs = []
    dataset_revisions = {}
    annotation_revisions = {}
    for job_id in selected_ids:
        job = jobs_by_id[job_id]
        result = job.result if isinstance(job.result, dict) else {}
        plan = job.revision
        spec = result.get('spec') if isinstance(result.get('spec'), dict) else {}
        dataset_revision_id = (spec.get('dataset_revision_id')
                               or plan.payload.get('dataset_revision_id'))
        dataset_revision = DatasetRevision.objects.filter(pk=dataset_revision_id).first()
        if dataset_revision:
            dataset_revisions.setdefault(
                dataset_revision.pk, _dataset_revision_record(dataset_revision))
        annotations = list(Revision.objects.filter(
            study=study, kind='annotations', payload__job=job_id).order_by('pk'))
        result_fingerprint = result.get('data_fingerprint')
        annotation_records = [{
            **_revision_record(revision),
            'applies_to_run': (isinstance(result_fingerprint, str)
                               and bool(result_fingerprint)
                               and revision.payload.get('data_fingerprint')
                               == result_fingerprint),
        } for revision in annotations]
        annotation_revisions.update({revision.pk: record for revision, record in
                                     zip(annotations, annotation_records)})
        runs.append({
            'job_id': job_id,
            'operation': job.operation,
            'created': job.created.isoformat(),
            'previous_job_id': str(job.previous_id) if job.previous_id else None,
            'source_job_id': str(job.source_id) if job.source_id else None,
            'plan': _revision_record(plan),
            'dataset_revision_id': dataset_revision.pk if dataset_revision else None,
            'dataset_revision_unknown': (dataset_revision_id
                                         if dataset_revision is None else None),
            'result': result,
            'annotations': annotation_records,
        })

    review_revisions = list(Revision.objects.filter(
        study=study, kind__in=('label_corrections', 'video_timeline_review', 'decision')
    ).order_by('pk'))
    payload = {
        'schema_version': 1,
        'study': {
            'id': study.pk,
            'name': study.name,
            'project_id': study.project_id,
            'project_name': study.project.name,
        },
        'created_at': timezone.now().isoformat(),
        'snapshot_revision_id': snapshot.pk if snapshot else None,
        'visual_state': (snapshot.payload.get('visual_state', {}) if snapshot else {}),
        'runs': runs,
        'dataset_revisions': list(dataset_revisions.values()),
        'annotation_revisions': list(annotation_revisions.values()),
        'review_revisions': [_revision_record(revision) for revision in review_revisions],
        'limitations': [
            'Los artefactos binarios se identifican por referencia y digest; '
            'no se copian a este reporte JSON.'
        ],
    }
    return Revision.objects.create(
        study=study, kind='frozen_report', parent=snapshot, payload=payload)


def _revision_record(revision):
    return {
        'revision_id': revision.pk,
        'kind': revision.kind,
        'parent_revision_id': revision.parent_id,
        'created': revision.created.isoformat(),
        'payload': revision.payload,
    }


def _dataset_revision_record(revision):
    assets = DatasetAsset.objects.filter(
        dataset=revision.dataset, pk__in=revision.asset_ids).order_by('pk')
    return {
        'dataset_id': revision.dataset_id,
        'dataset_name': revision.dataset.name,
        'dataset_revision_id': revision.pk,
        'number': revision.number,
        'connector': revision.connector,
        'status': revision.status,
        'source_fingerprint': revision.inventory.get('source_fingerprint'),
        'config': revision.config,
        'inventory': revision.inventory,
        'artifact_ref': revision.artifact_ref,
        'assets': [{
            'asset_id': asset.pk,
            'role': asset.role,
            'original_name': asset.original_name,
            'relative_path': asset.relative_path,
            'sha256': asset.sha256,
            'size_bytes': asset.size_bytes,
            'session_id': asset.session_id,
            'metadata': asset.metadata,
        } for asset in assets],
    }
