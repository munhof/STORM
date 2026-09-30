"""Append-only researcher decisions for video continuity findings."""
from storm.config import fingerprint
from storm_studio.models import DatasetAsset, DatasetRevision, Revision


REVIEW_KIND = 'video_timeline_review'


def _review_source(dataset_revision):
    """Follow prepared datasets to the registered video timeline they inherit."""
    current = dataset_revision
    visited = set()
    while current.connector == 'prepared_artifact':
        if current.pk in visited:
            break
        visited.add(current.pk)
        source_id = current.config.get('source_dataset_revision_id')
        if not source_id:
            break
        source = DatasetRevision.objects.filter(pk=source_id).first()
        if source is None:
            break
        current = source
    return current


def _review_items(dataset_revision):
    timeline = dataset_revision.inventory.get('video_timeline') or []
    items = [
        {
            'file': item.get('file'),
            'session_id': item.get('session_id'),
            'frame_count': item.get('frame_count'),
            'pose_frame_count': item.get('pose_frame_count'),
            'fps': item.get('fps'),
            'discontinuity_status': item.get(
                'discontinuity_status', 'unknown_requires_review'),
            'timestamp_status': item.get(
                'timestamp_status', item.get(
                    'discontinuity_status', 'unknown_requires_review')),
            'discontinuity_source': item.get('discontinuity_source', 'timestamps'),
            'discontinuities': item.get('discontinuities'),
            'detected_discontinuities': item.get('detected_discontinuities'),
        }
        for item in timeline
        if item.get('discontinuity_status') != 'verified_contiguous'
    ]
    configured_sessions = dataset_revision.config.get('asset_sessions') or {}
    timeline_assets = {(item.get('file'), item.get('session_id')) for item in timeline}
    for asset in DatasetAsset.objects.filter(
            dataset_id=dataset_revision.dataset_id,
            pk__in=dataset_revision.asset_ids, role='video'):
        session_id = (configured_sessions.get(str(asset.pk)) or asset.session_id or '')
        matched = any(
            name == asset.original_name and (not session_id or session == session_id)
            for name, session in timeline_assets)
        if not matched:
            items.append({
                'file': asset.original_name,
                'session_id': session_id or 'unknown',
                'frame_count': None,
                'fps': dataset_revision.config.get('fps'),
                'discontinuity_status': 'unknown_requires_review',
                'timestamp_status': 'unknown_requires_review',
                'discontinuity_source': 'timestamps',
                'discontinuities': None,
                'detected_discontinuities': None,
            })
    return items


def review_key(dataset_revision):
    source = _review_source(dataset_revision)
    configured_sessions = source.config.get('asset_sessions') or {}
    configured_offsets = source.config.get('video_frame_offsets') or {}
    videos = []
    for asset in DatasetAsset.objects.filter(
            dataset_id=source.dataset_id, pk__in=source.asset_ids,
            role='video').order_by('pk'):
        videos.append({
            'file': asset.original_name,
            'sha256': asset.sha256,
            'session_id': (configured_sessions.get(str(asset.pk))
                           or asset.session_id or ''),
            'frame_offset': configured_offsets.get(str(asset.pk), 0),
        })
    return fingerprint({
        'dataset_id': source.dataset_id,
        'fps': source.config.get('fps', 30),
        'videos': videos,
        'video_timeline': _review_items(source),
    })


def review_state(study, dataset_revision):
    source = _review_source(dataset_revision) if dataset_revision else None
    items = _review_items(source) if source else []
    key = review_key(source) if source and items else None
    decision = None
    if key:
        decision = study.revision_set.filter(
            kind=REVIEW_KIND,
            payload__dataset_id=source.dataset_id,
            payload__review_key=key,
        ).order_by('-pk').first()
    return {
        'required': bool(items),
        'accepted': decision is not None,
        'has_unknown': any(
            item['timestamp_status'] == 'unknown_requires_review'
            for item in items),
        'has_gaps': any(
            item['discontinuity_status'] == 'gaps_detected'
            for item in items),
        'has_manual': any(
            item['discontinuity_source'] == 'manual'
            for item in items),
        'items': items,
        'decision': decision,
        'source_dataset_revision_id': source.pk if source else None,
        'review_key': key,
    }


def require_review(study, dataset_revision):
    state = review_state(study, dataset_revision)
    if state['required'] and not state['accepted']:
        raise ValueError(
            'Revisá la continuidad del video en Datos y guardá una decisión antes de preparar o ejecutar.')
    return state


def record_review(study, dataset_revision, *, reviewer, reason):
    if study.dataset_revision_id != dataset_revision.pk:
        raise ValueError('La revisión de continuidad debe corresponder al dataset activo.')
    if dataset_revision.status != 'ready':
        raise ValueError('Inspeccioná el dataset antes de revisar la continuidad del video.')
    state = review_state(study, dataset_revision)
    if not state['required']:
        raise ValueError('No hay saltos ni timelines desconocidos para revisar.')
    reviewer = str(reviewer or '').strip()
    reason = str(reason or '').strip()
    if not reviewer or len(reviewer) > 160:
        raise ValueError('Indicá quién revisó la continuidad (hasta 160 caracteres).')
    if not reason or len(reason) > 2000:
        raise ValueError('Escribí el motivo de la decisión (hasta 2.000 caracteres).')

    source = _review_source(dataset_revision)
    previous = study.revision_set.filter(kind=REVIEW_KIND).order_by('-pk').first()
    return Revision.objects.create(
        study=study,
        kind=REVIEW_KIND,
        parent=previous,
        payload={
            'dataset_revision_id': source.pk,
            'dataset_id': source.dataset_id,
            'source_fingerprint': source.inventory.get('source_fingerprint'),
            'review_key': state['review_key'],
            'video_timeline': state['items'],
            'decision': 'reviewed_and_accepted',
            'reviewer': reviewer,
            'reason': reason,
        },
    )
