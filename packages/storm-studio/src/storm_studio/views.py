import csv
import hashlib
import io
import json
import mimetypes
import math
from datetime import datetime
from pathlib import Path
import re
import statistics
import tempfile
import uuid
import zipfile

from django.contrib import messages
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import (FileResponse, HttpResponse, JsonResponse, Http404,
                         StreamingHttpResponse)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from storm_studio.forms import (AssetSessionForm, DatasetUploadForm, PlanForm,
                                PreparationForm, SessionPartitionForm)
from storm_studio.models import (ArchivedPreparation, Dataset, DatasetAsset,
                                 DatasetRevision, Project, Study, Revision, Job)
from storm_studio.data_registry import store_upload, validate_uploads
from storm_studio.preview_sampling import PREVIEW_STRATEGY
from storm_studio import services
from storm.suite import missing_required_pipeline_steps

PAGES = [('data', 'Datos'), ('prepare', 'Preparar'), ('components', 'Componentes'), ('flow', 'Flujo'),
         ('models', 'Modelos'), ('jobs', 'Ejecuciones'), ('evidence', 'Evidencia'),
         ('review', 'Revisión'), ('compare', 'Comparar'), ('reports', 'Reportes'),
         ('lineage', 'Lineage'), ('history', 'Historial')]
ADVANCED_PAGES = [item for item in PAGES if item[0] in {'components', 'lineage', 'history'}]


def _integer_or_default(value, *, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _format_eta(seconds):
    if seconds is None:
        return ''
    seconds = max(0, math.ceil(seconds))
    if seconds < 60:
        return 'menos de 1 min'
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f'{minutes} min' + (f' {seconds} s' if seconds else '')
    hours, minutes = divmod(minutes, 60)
    return f'{hours} h' + (f' {minutes} min' if minutes else '')


def _execution_summary(jobs, completed_count):
    running = sum(job.status == 'running' for job in jobs)
    pending = sum(job.status == 'pending' for job in jobs)
    active = running + pending
    if active:
        parts = []
        if running:
            parts.append(f'{running} en curso')
        if pending:
            parts.append(f'{pending} en cola')
        label = ' · '.join(parts)
    elif completed_count:
        label = f'{completed_count} ejecuciones completadas'
    else:
        label = 'Sin ejecuciones'
    return {
        'running': running, 'pending': pending, 'active': active,
        'completed': completed_count, 'label': label,
    }


def _execution_eta_key(job):
    payload = job.revision.payload
    if job.operation in {'train', 'resume', 'update', 'infer', 'apply'}:
        model = payload.get('model')
        if not model:
            return None
        operation = ('model_training' if job.operation in {'train', 'resume', 'update'}
                     else 'model_inference')
        return (operation, model, payload.get('dataset_revision_id'))
    if job.operation == 'prepare':
        return ('prepare', payload.get('name'), payload.get('dataset_revision_id'))
    if job.operation == 'inventory':
        return ('inventory', payload.get('connector'), payload.get('dataset_revision'))
    if job.operation == 'video_preview':
        return ('video_preview', payload.get('asset_id'), payload.get('dataset_revision'))
    return (job.operation, job.revision_id, None)


def _decorate_execution_progress(jobs):
    """Prepare truthful step and ETA details for execution cards and polling."""
    now = timezone.now()
    completed_durations = {}
    for previous in jobs:
        if (previous.status != 'completed' or previous.started is None
                or previous.finished is None):
            continue
        key = _execution_eta_key(previous)
        if key is None:
            continue
        duration = (previous.finished - previous.started).total_seconds()
        if duration > 0:
            completed_durations.setdefault(key, []).append(duration)
    labels = {
        'pending': 'En cola', 'running': 'En curso', 'completed': 'Completada',
        'failed': 'Fallida', 'interrupted': 'Interrumpida', 'cancelled': 'Cancelada',
    }
    for job in jobs:
        progress = dict(job.progress) if isinstance(job.progress, dict) else {}
        if job.status == 'pending':
            progress.setdefault('phase', 'queued')
            progress.setdefault('label', 'En cola; esperando a que el worker la tome')
            progress.setdefault('stage_index', 0)
            progress.setdefault('stage_total', 5)
        elif job.status == 'running':
            progress.setdefault('phase', 'starting')
            progress.setdefault('label', 'El worker inició; preparando la ejecución')
            progress.setdefault('stage_index', 1)
            progress.setdefault('stage_total', 5)

        eta_seconds = progress.get('eta_seconds')
        if (isinstance(eta_seconds, (int, float)) and not isinstance(eta_seconds, bool)
                and progress.get('updated_at')):
            try:
                updated_at = datetime.fromisoformat(progress['updated_at'])
                eta_seconds = max(0, eta_seconds - (now - updated_at).total_seconds())
            except (TypeError, ValueError):
                pass
        elif not isinstance(eta_seconds, (int, float)) or isinstance(eta_seconds, bool):
            eta_seconds = None
        eta_basis = 'avance informado por el modelo'
        if job.status == 'running' and eta_seconds is None:
            key = _execution_eta_key(job)
            comparable_durations = completed_durations.get(key, []) if key else []
            if comparable_durations and job.started:
                typical_duration = statistics.median(comparable_durations)
                elapsed = max(0, (now - job.started).total_seconds())
                eta_seconds = max(0, typical_duration - elapsed)
                eta_basis = 'duración mediana de ejecuciones anteriores comparables'

        job.progress_for_ui = progress
        job.status_label = labels.get(job.status, job.status)
        job.stages_after_current = max(
            0, progress.get('stage_total', 0) - progress.get('stage_index', 0))
        step = progress.get('phase_step')
        total = progress.get('phase_total')
        job.progress_units_remaining = (
            max(0, total - step)
            if type(step) is int and type(total) is int else None)
        job.progress_percent = None
        fraction = progress.get('fraction')
        if isinstance(fraction, (int, float)) and not isinstance(fraction, bool):
            job.progress_percent = round(min(1.0, max(0.0, fraction)) * 100)
        job.progress_is_measurable = job.progress_percent is not None
        job.eta_seconds = eta_seconds
        job.eta_text = _format_eta(eta_seconds)
        job.eta_basis = eta_basis if eta_seconds is not None else ''
        job.eta_updated_at = progress.get('updated_at') or ''


MAX_POSE_PREDICTION_RUNS = 4


def _pose_prediction_run_options(study, dataset_revision, completed, selected_ids=()):
    """Expose only completed runs with frame-level identity on this data lineage."""
    if not dataset_revision or not dataset_revision.inventory.get('pose_preview_store'):
        return [], []
    try:
        source = services._root_dataset_revision(dataset_revision)
    except (DatasetRevision.DoesNotExist, ValueError):
        return [], []
    options = []
    for job in completed:
        result = job.result
        spec = result.get('spec') or {}
        try:
            job_dataset = DatasetRevision.objects.get(
                pk=spec['dataset_revision_id'], status='ready')
            same_source = services._root_dataset_revision(job_dataset).pk == source.pk
        except (KeyError, TypeError, ValueError, DatasetRevision.DoesNotExist):
            same_source = False
        if not same_source:
            continue
        data = result.get('resolved_data') or {}
        inputs = data.get('inputs')
        observation_ids = data.get('observation_ids')
        sessions = data.get('sessions')
        frames = data.get('frames')
        indices = result.get('indices')
        predictions = result.get('predictions')
        mask = result.get('prediction_mask')
        if (not isinstance(inputs, list) or not isinstance(observation_ids, list)
                or not isinstance(sessions, list) or not isinstance(frames, list)
                or any(len(values) != len(inputs)
                       for values in (observation_ids, sessions, frames))
                or any(not isinstance(value, str) for value in observation_ids)
                or len(set(observation_ids)) != len(observation_ids)
                or not isinstance(indices, list) or not isinstance(predictions, list)
                or len(indices) != len(predictions)
                or any(type(index) is not int or not 0 <= index < len(inputs)
                       for index in indices)
                or len(set(indices)) != len(indices)):
            continue
        if mask is not None and (
                not isinstance(mask, list) or len(mask) != len(indices)
                or any(type(value) is not bool for value in mask)):
            continue
        options.append({
            'job_id': str(job.pk),
            'model': result.get('model', spec.get('model', 'Modelo')),
            'semantics': (result.get('output_metadata') or {}).get(
                'semantics', 'Semántica no declarada'),
            'selected': str(job.pk) in selected_ids,
        })
    return options, [option['job_id'] for option in options
                     if option['job_id'] in selected_ids][:MAX_POSE_PREDICTION_RUNS]


# The public workflow is task-oriented; the existing section routes remain the
# compatibility layer for detailed views.
STAGES = [
    ('data', 'Datos', '◉'),
    ('prepare', 'Preparar', '◇'),
    ('flow', 'Configurar', '△'),
    ('models', 'Modelar', '△'),
    ('jobs', 'Ejecuciones', '▶'),
    ('evidence', 'Analizar', '≈'),
    ('review', 'Revisar', '✓'),
    ('compare', 'Comparar', '⇄'),
    ('reports', 'Reportar', '▤'),
]
STAGE_GROUPS = [
    ('Datos y preparación', ('data', 'prepare')),
    ('Entrenamiento', ('flow', 'models', 'jobs')),
    ('Análisis y revisión', ('evidence', 'review', 'compare')),
    ('Reportes', ('reports',)),
]

PREPARATION_STEP_UI = {
    'pose.select_coordinates': {'label': 'Elegir coordenadas', 'ui': 'coordinates'},
    'pose.recenter': {'label': 'Centrar en punto corporal', 'ui': 'recenter'},
    'pose.orient_coordinates': {'label': 'Alinear orientación', 'ui': 'orientation'},
    'pose.likelihood_filter': {'label': 'Filtrar baja confianza', 'ui': 'likelihood'},
    'pose.temporal_windows': {'label': 'Crear ventanas temporales', 'ui': 'windows'},
}


def preparation_step_catalog():
    available = set(services.catalog().steps.available)
    steps = [
        {'type': 'center', 'label': 'Centrar por media del entrenamiento', 'ui': 'center'},
        {'type': 'scale', 'label': 'Multiplicar coordenadas', 'ui': 'scale'},
    ]
    steps.extend(
        {'type': name, **details}
        for name, details in PREPARATION_STEP_UI.items() if name in available)
    return steps


def _pose_motion_preview(dataset_revision):
    if not dataset_revision or dataset_revision.status != 'ready':
        return []
    feature_names = dataset_revision.inventory.get('feature_names', [])
    if not any(name.endswith('_x') and f'{name[:-2]}_y' in feature_names
               for name in feature_names):
        return []
    preview = []
    taxonomy = dataset_revision.inventory.get('taxonomy', [])
    for row in dataset_revision.inventory.get('preview', []):
        coordinates = []
        for value in row.get('features', []):
            try:
                coordinate = float(value)
            except (TypeError, ValueError):
                coordinate = None
            coordinates.append(coordinate if coordinate is not None and math.isfinite(coordinate) else None)
        target = row.get('target')
        target_label = (taxonomy[target] if type(target) is int and 0 <= target < len(taxonomy)
                        else target)
        preview.append({
            'observation_id': row.get('observation_id', ''),
            'session_id': row.get('session_id', ''),
            'frame': row.get('frame'),
            'video_frame': row.get('video_frame'),
            'segment': row.get('segment', ''),
            'features': coordinates,
            'target': target,
            'target_label': target_label,
            'evaluation_mask': row.get('evaluation_mask', target is not None),
        })
    return preview


def _label_corrections_for_revision(study, dataset_revision):
    if not dataset_revision:
        return {}
    revision = Revision.objects.filter(
        study=study, kind='label_corrections',
        payload__dataset_revision_id=dataset_revision.pk).order_by('-pk').first()
    if (not revision or revision.payload.get('source_fingerprint')
            != dataset_revision.inventory.get('source_fingerprint')):
        return {}
    return revision.payload


def _video_discontinuity_override_rows(dataset_revision):
    if not dataset_revision or dataset_revision.status != 'ready':
        return []
    configured = dataset_revision.config.get(
        'video_discontinuity_overrides_by_session') or {}
    if not isinstance(configured, dict):
        configured = {}
    grouped = {}
    for video in dataset_revision.inventory.get('video_timeline', []):
        session_id = video.get('session_id')
        if not session_id or session_id == 'unmatched':
            continue
        row = grouped.setdefault(session_id, {
            'session_id': session_id, 'files': [], 'frame_counts': [],
            'detected_boundaries': [],
        })
        row['files'].append(video.get('file', 'video'))
        frame_count = video.get('frame_count')
        if type(frame_count) is int and frame_count > 0:
            row['frame_counts'].append(frame_count)
        detected = video.get('detected_discontinuities')
        if detected is None:
            detected = video.get('discontinuities') or []
        row['detected_boundaries'].extend(
            item['before_frame'] for item in detected
            if isinstance(item, dict) and type(item.get('before_frame')) is int)
    rows = []
    for session_id, row in grouped.items():
        boundaries = configured.get(session_id)
        if boundaries is None:
            boundaries = sorted(set(row['detected_boundaries']))
        else:
            boundaries = sorted(set(boundaries))
        rows.append({
            'session_id': session_id,
            'files': list(dict.fromkeys(row['files'])),
            'frame_count': min(row['frame_counts']) if row['frame_counts'] else None,
            'boundaries_text': ', '.join(str(value) for value in boundaries),
        })
    return rows


@require_http_methods(['GET', 'POST'])
def home(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()[:160]
        if name:
            project = Project.objects.create(name=name)
            study = Study.objects.create(project=project, name=name)
            return redirect('page', study.pk, 'flow')
    return render(request, 'storm_studio/home.html', {
        'studies': Study.objects.filter(archived_at__isnull=True).order_by('-created'),
        'archived_studies': Study.objects.filter(
            archived_at__isnull=False).order_by('-archived_at'),
    })


@require_POST
def archive_study(request, study_id):
    study = get_object_or_404(Study, pk=study_id)
    if study.archived_at:
        study.archived_at = None
        study.save(update_fields=['archived_at'])
        messages.success(request, 'Estudio restaurado en la lista activa.')
    elif Job.objects.filter(
            revision__study=study, status__in=('pending', 'running')).exists():
        messages.error(request, 'No se puede archivar mientras haya trabajos en curso.')
    else:
        from django.utils import timezone

        study.archived_at = timezone.now()
        study.save(update_fields=['archived_at'])
        messages.success(request, 'Estudio archivado. Sus datos e historial se conservaron.')
    return redirect('home')


@require_POST
def archive_preparation(request, revision_id):
    recipe = get_object_or_404(Revision, pk=revision_id, kind='preparation')
    marker = ArchivedPreparation.objects.filter(preparation=recipe)
    if marker.exists():
        marker.delete()
        messages.success(request, 'Receta restaurada en la lista activa.')
    else:
        latest_plan = recipe.study.revision_set.filter(kind='plan').order_by('-pk').first()
        active_job = Job.objects.filter(
            revision=recipe, operation='prepare', status__in=('pending', 'running')).exists()
        if (latest_plan and latest_plan.payload.get('preparation_revision_id') == recipe.pk) or active_job:
            messages.error(request, 'No se puede archivar una receta usada por el plan activo o un trabajo en curso.')
        else:
            ArchivedPreparation.objects.create(preparation=recipe)
            messages.success(request, 'Receta archivada; la revisión y los datasets procesados se conservaron.')
    return redirect('page', recipe.study_id, 'prepare')


@require_http_methods(['GET', 'POST'])
def page(request, study_id, section):
    study = get_object_or_404(Study, pk=study_id)
    if section not in dict(PAGES):
        raise Http404
    ui_catalog = services.catalog()
    recipe_presets = list(ui_catalog.recipe_presets.values())
    latest = study.revision_set.filter(kind='plan').order_by('-pk').first()
    restored_snapshot = None
    snapshot_id = request.GET.get('snapshot')
    if snapshot_id:
        restored_snapshot = get_object_or_404(Revision, pk=snapshot_id, study=study, kind='snapshot')
    active_dataset_revision = study.dataset_revision if study.dataset_revision_id else None
    dataset_revisions = []
    if active_dataset_revision:
        dataset_revisions = list(DatasetRevision.objects.filter(
            dataset_id=active_dataset_revision.dataset_id, status='ready'
        ).select_related('dataset').order_by('-number'))
    previous_dataset_id = latest.payload.get('dataset_revision_id') if latest else None
    if previous_dataset_id and all(str(item.pk) != str(previous_dataset_id)
                                   for item in dataset_revisions):
        previous_dataset = DatasetRevision.objects.filter(
            pk=previous_dataset_id, status='ready').select_related('dataset').first()
        if previous_dataset:
            dataset_revisions.append(previous_dataset)
    form_initial = latest.payload if latest else None
    if not latest and active_dataset_revision and active_dataset_revision.status == 'ready':
        form_initial = {
            'operation': 'train', 'model': 'identity', 'metrics': [],
            'connector': active_dataset_revision.connector,
            'dataset_revision_id': str(active_dataset_revision.pk),
            'config': {}, 'data': {}, 'steps': [], 'branch_models': [],
            'branch_configs': {}, 'seed': 42,
        }
    if section == 'flow' and request.method == 'GET' and request.GET.get('dataset_revision_id'):
        selected_source = next((item for item in dataset_revisions
                                if str(item.pk) == request.GET['dataset_revision_id']), None)
        if selected_source:
            form_initial = dict(form_initial or {})
            form_initial.update({
                'dataset_revision_id': str(selected_source.pk),
                'connector': selected_source.connector,
                'preparation_revision_id': '',
                'steps': [],
                'data': {},
            })
    selected_preset = None
    if section == 'flow' and request.method == 'GET':
        selected_preset = ui_catalog.recipe_presets.get(
            request.GET.get('recipe_preset', ''))
        if selected_preset:
            form_initial = dict(form_initial or {})
            form_initial.update({
                'operation': 'train',
                'model': selected_preset['model'],
                'config': dict(selected_preset.get('config', {})),
                'steps': [dict(step) for step in selected_preset.get('steps', [])],
                'preparation_revision_id': '',
                'branch_models': [],
                'branch_configs': {},
                'seed': 156,
            })
    all_preparation_revisions = list(Revision.objects.filter(
        study=study, kind='preparation').order_by('-pk'))
    archived_preparation_ids = set(ArchivedPreparation.objects.filter(
        preparation__study=study).values_list('preparation_id', flat=True))
    preparation_revisions = [item for item in all_preparation_revisions
                             if item.pk not in archived_preparation_ids]
    archived_preparations = list(ArchivedPreparation.objects.filter(
        preparation__study=study).select_related('preparation').order_by('-archived_at'))
    active_preparation_job_ids = set(Job.objects.filter(
        revision__study=study, revision__kind='preparation', operation='prepare',
        status__in=('pending', 'running')).values_list('revision_id', flat=True))
    active_recipe_id = (latest.payload.get('preparation_revision_id')
                        if latest else None)
    for recipe in preparation_revisions:
        recipe.in_use = recipe.pk == active_recipe_id or recipe.pk in active_preparation_job_ids
    label_correction_revisions = list(Revision.objects.filter(
        study=study, kind='label_corrections').order_by('-pk'))
    if section == 'prepare':
        latest_preparation = preparation_revisions[0] if preparation_revisions else None
        selected_preparation = None
        if request.method == 'GET' and request.GET.get('recipe_revision'):
            selected_preparation = get_object_or_404(
                Revision, pk=request.GET['recipe_revision'],
                study=study, kind='preparation')
        else:
            selected_preparation = latest_preparation
        preparation_initial = (dict(selected_preparation.payload)
                               if selected_preparation else None)
        if not preparation_initial and active_dataset_revision:
            preparation_initial = {
                'name': 'Preparación de pose',
                'dataset_revision_id': str(active_dataset_revision.pk), 'steps': [], 'data': {},
            }
        if preparation_initial is not None:
            preparation_initial['base_revision_id'] = (
                selected_preparation.pk if selected_preparation else '')
        form = PreparationForm(
            request.POST if request.method == 'POST' else None,
            initial=preparation_initial, dataset_revisions=dataset_revisions)
    else:
        form = PlanForm(request.POST if request.method == 'POST' else None,
                        initial=form_initial, dataset_revisions=dataset_revisions,
                        preparation_revisions=preparation_revisions,
                        label_correction_revisions=label_correction_revisions)
    try:
        editor_data = form['data'].value() or {}
        if isinstance(editor_data, str):
            editor_data = json.loads(editor_data)
        if not isinstance(editor_data, dict):
            editor_data = {}
    except (TypeError, json.JSONDecodeError):
        editor_data = {}
    partition_fields = {
        'train': ', '.join(str(index) for index in editor_data.get('train', [])),
        'validation': ', '.join(str(index) for index in editor_data.get('validation', [])),
        'test': ', '.join(str(index) for index in editor_data.get('test', [])),
    }
    partition_rows = [
        {'key': key, 'label': label, 'value': partition_fields[key]}
        for key, label in (('train', 'Entrenamiento'),
                           ('validation', 'Validación'), ('test', 'Evaluación'))
    ]
    if request.method == 'POST':
        if section == 'prepare' and request.POST.get('run_preparation_revision_id'):
            recipe = get_object_or_404(
                Revision, pk=request.POST['run_preparation_revision_id'],
                study=study, kind='preparation')
            try:
                job = services.enqueue_preparation(study, recipe)
            except ValueError as error:
                messages.error(request, str(error))
                return redirect('page', study.pk, 'data')
            messages.success(request, f'Procesamiento en cola · ejecución {job.pk}.')
            return redirect('page', study.pk, 'jobs')
        if section not in ('flow', 'data', 'prepare'):
            return HttpResponse(status=405)
        if form.is_valid():
            payload = dict(form.cleaned_data)
            if section == 'prepare':
                source = get_object_or_404(
                    DatasetRevision, pk=payload['dataset_revision_id'], status='ready')
                if study.dataset_revision_id and source.dataset_id != study.dataset_revision.dataset_id:
                    form.add_error('dataset_revision_id',
                                   'Elegí una revisión del dataset asociado al estudio.')
                else:
                    base_revision_id = payload.pop('base_revision_id', None)
                    parent_preparation = (get_object_or_404(
                        Revision, pk=base_revision_id, study=study, kind='preparation')
                        if base_revision_id else Revision.objects.filter(
                            study=study, kind='preparation').order_by('-pk').first())
                    if (parent_preparation and parent_preparation.payload.get(
                            'dataset_revision_id') != source.pk):
                        form.add_error(
                            'base_revision_id',
                            'La receta seleccionada pertenece a otra revisión de datos.')
                        parent_preparation = None
                if not form.errors:
                    payload['dataset_revision_id'] = source.pk
                    payload['steps'] = [dict(step) for step in payload.get('steps', [])]
                    payload['source_fingerprint'] = source.inventory.get('source_fingerprint')
                    revision = Revision.objects.create(
                        study=study, kind='preparation', parent=parent_preparation,
                        payload=payload)
                    messages.success(request, f'Receta guardada como revisión {revision.pk}.')
                    return redirect('page', study.pk, 'prepare')
            else:
                preparation_id = payload.get('preparation_revision_id')
                if preparation_id:
                    preparation = get_object_or_404(
                        Revision, pk=preparation_id, study=study, kind='preparation')
                    source_id = payload.get('dataset_revision_id')
                    if source_id and int(source_id) != preparation.payload.get('dataset_revision_id'):
                        form.add_error('preparation_revision_id',
                                       'La receta pertenece a otra revisión del dataset.')
                    else:
                        payload['dataset_revision_id'] = preparation.payload['dataset_revision_id']
                        payload['connector'] = DatasetRevision.objects.get(
                            pk=payload['dataset_revision_id']).connector
                        payload['steps'] = preparation.payload['steps']
                        payload['data'] = {}
                        payload['preparation_revision_id'] = preparation.pk
                selected_dataset_id = payload.get('dataset_revision_id')
                if not form.errors:
                    selected_dataset = None
                    if selected_dataset_id:
                        selected_dataset = get_object_or_404(
                            DatasetRevision, pk=selected_dataset_id, status='ready')
                        payload['dataset_revision_id'] = selected_dataset.pk
                        payload['connector'] = selected_dataset.connector
                        payload['data'] = {}
                    correction_revision_id = payload.get('label_correction_revision_id')
                    if correction_revision_id:
                        if selected_dataset is None:
                            form.add_error(
                                'label_correction_revision_id',
                                'Seleccioná una fuente registrada para usar sus correcciones.')
                        else:
                            correction_revision = get_object_or_404(
                                Revision, pk=correction_revision_id,
                                study=study, kind='label_corrections')
                            try:
                                services.validate_label_correction_scope(
                                    study, selected_dataset, correction_revision)
                            except ValueError as error:
                                form.add_error('label_correction_revision_id', str(error))
                if not form.errors:
                    revision = Revision.objects.create(
                        study=study, kind='plan', parent=latest, payload=payload)
                    messages.success(request, f'Plan guardado como revisión {revision.pk}.')
                    return redirect('page', study.pk, 'jobs')
    jobs = list(Job.objects.filter(revision__study=study).select_related('revision').order_by('-created'))
    _decorate_execution_progress(jobs)
    plan_has_active_jobs = bool(latest and any(
        job.status in ('pending', 'running')
        and (job.revision_id == latest.pk or job.revision.parent_id == latest.pk)
        for job in jobs))
    dataset_revision = study.dataset_revision if study.dataset_revision_id else None
    if dataset_revision and dataset_revision.status in ('queued', 'inspecting'):
        active_inventory = any(
            job.operation == 'inventory' and job.status in ('pending', 'running')
            and job.revision.payload.get('dataset_revision') == dataset_revision.pk
            for job in jobs)
        if not active_inventory:
            DatasetRevision.objects.filter(pk=dataset_revision.pk).update(status='interrupted')
            dataset_revision.refresh_from_db()
    model_jobs = [job for job in jobs
                  if job.operation not in {'inventory', 'prepare', 'video_preview'}]
    completed = [job for job in model_jobs if job.status == 'completed']
    execution_summary = _execution_summary(jobs, len(completed))
    for job in completed:
        connector = job.result.get('spec', {}).get('connector', 'numeric_json')
        job.inference_dataset_revisions = [
            item for item in dataset_revisions if item.connector == connector]
    from storm.artifacts import FileArtifactStore
    store = FileArtifactStore(settings.ARTIFACT_ROOT)
    resumable = []
    for job in jobs:
        if job.status not in ('pending', 'running'):
            try:
                store.resolve(kind='checkpoints', artifact_id=str(job.pk))
                resumable.append(job.pk)
            except (FileNotFoundError, KeyError):
                pass
    selected_id = request.GET.get('job')
    if not selected_id and restored_snapshot:
        selected_jobs = restored_snapshot.payload.get('visual_state', {}).get('selected_jobs', [])
        selected_id = selected_jobs[0] if selected_jobs else None
    selected = next((j for j in completed if str(j.pk) == selected_id), completed[0] if completed else None)
    comparison_requested = section == 'compare' and 'jobs' in request.GET
    requested_ids = [value for item in request.GET.getlist('jobs') for value in item.split(',') if value] if comparison_requested else []
    selected_comparison = [job for job in completed if str(job.pk) in requested_ids]
    if comparison_requested:
        compared_jobs = selected_comparison
    else:
        compared_jobs = completed
    comparison_rows = []
    if len(compared_jobs) == 2:
        first, second = compared_jobs
        first_fingerprint = first.result.get('data_fingerprint')
        if first_fingerprint and first_fingerprint == second.result.get('data_fingerprint'):
            other = {
                index: prediction
                for index, prediction, valid in zip(
                    second.result['indices'], second.result['predictions'],
                    second.result.get('prediction_mask',
                                      [True] * len(second.result['indices'])))
                if valid
            }
            comparison_rows = [
                {'index': index, 'first': prediction, 'second': other[index],
                 'different': prediction != other[index]}
                for index, prediction, valid in zip(
                    first.result['indices'], first.result['predictions'],
                    first.result.get('prediction_mask',
                                     [True] * len(first.result['indices'])))
                if valid and index in other
            ]
    comparison_metrics = []
    if compared_jobs:
        metric_names = set.intersection(*(set(job.result.get('metrics', {})) for job in compared_jobs))
        palette = ('#1754ad', '#7b3fb5', '#bd6400', '#16806a', '#a32929')
        for metric_name in sorted(metric_names):
            values = [job.result['metrics'][metric_name] for job in compared_jobs]
            if not all(type(value) in (int, float) for value in values):
                continue
            low, high = min(values), max(values)
            definitions = {item.get('name'): item for item in compared_jobs[0].result.get('metric_definitions', [])}
            points = []
            for index, (job, value) in enumerate(zip(compared_jobs, values)):
                x = 400 if high == low else 70 + (value - low) * 660 / (high - low)
                points.append({'x': round(x, 2), 'y': 20 + index * 28,
                               'value': value, 'model': job.result.get('model'),
                               'job': str(job.pk), 'color': palette[index % len(palette)]})
            comparison_metrics.append({'name': metric_name,
                                       'direction': definitions.get(metric_name, {}).get('direction', 'no declarada'),
                                       'low': low, 'high': high, 'height': max(100, 32 + len(points) * 28),
                                       'points': points})
    evidence = []
    annotation = None
    output_metadata = {}
    if selected:
        data = selected.result['resolved_data']
        output_metadata = selected.result.get('output_metadata', {})
        annotation = study.revision_set.filter(kind='annotations', payload__job=str(selected.pk)).order_by('-pk').first()
        prediction_mask = selected.result.get(
            'prediction_mask', [True] * len(selected.result['indices']))
        for index, prediction, prediction_valid in zip(
                selected.result['indices'], selected.result['predictions'], prediction_mask):
            confidence = output_metadata.get('confidence', [])
            output_position = len(evidence)
            evidence.append({'index': index, 'raw': data['inputs'][index], 'prediction': prediction,
                             'prediction_valid': prediction_valid,
                             'target': (data.get('targets') or [None] * len(data['inputs']))[index],
                             'confidence': confidence[output_position] if output_position < len(confidence)
                             and output_metadata.get('confidence_semantics') else None})
    output_semantics = output_metadata.get('semantics')
    group_output = isinstance(output_semantics, str) and 'group' in output_semantics.lower()
    numeric_evidence = bool(evidence) and not group_output and all(
        type(value) in (int, float) and (not isinstance(value, float) or value == value and abs(value) != float('inf'))
        for row in evidence for value in (row['raw'], row['prediction'], row['target']) if value is not None
    )
    review_revisions = list(study.revision_set.filter(kind='review', payload__status='pending'))
    pending_reviews = sum(
        not study.revision_set.filter(kind='decision', parent=review).exists()
        for review in review_revisions
    )
    completed_count = len(completed)
    lifecycle = [
        ('flow', 'Plan', f'Plan activo · revisión {latest.pk}' if latest else 'Sin plan guardado'),
        ('jobs', 'Ejecutar', f'{completed_count} ejecución' + (' completada' if completed_count == 1 else 'es completadas')),
        ('evidence', 'Analizar', 'Evidencia disponible' if selected else 'Sin evidencia todavía'),
        ('review', 'Revisar', f'{pending_reviews} lote' + (' pendiente' if pending_reviews == 1 else 's pendientes')),
        ('jobs', 'Adoptar', f'Adoptado · {study.adopted.result.get("model")}' if study.adopted_id else 'Sin candidato adoptado'),
        ('reports', 'Reportar', 'Reportes disponibles' if completed else 'Sin resultados para reportar'),
    ]
    if not latest and not study.dataset_revision_id:
        next_action = 'Registrar archivos de pose, ROI, video o etiquetas'
    elif not latest and study.dataset_revision_id:
        next_action = 'Preparar los datos o configurar el estudio del modelo'
    elif not completed:
        next_action = 'Ejecutar el plan activo'
    elif not selected:
        next_action = 'Seleccionar una ejecución para analizar'
    elif pending_reviews:
        next_action = 'Revisar el lote pendiente'
    else:
        next_action = 'Comparar ejecuciones y documentar una decisión'
    stage_states = []
    for key, label, icon in STAGES:
        if key == 'data':
            status = 'registrado' if study.dataset_revision_id else ('listo' if latest else 'pendiente')
            count = study.dataset_revision.inventory.get('assets', 0) if study.dataset_revision_id else (1 if latest else 0)
        elif key == 'flow':
            status = 'listo' if latest else 'pendiente'
            count = 1 if latest else 0
        elif key == 'prepare':
            status = 'listo' if preparation_revisions else (
                'disponible' if study.dataset_revision_id else 'pendiente')
            count = len(preparation_revisions)
        elif key == 'review':
            status = f'{pending_reviews} pendiente' if pending_reviews else 'listo'
            count = pending_reviews
        elif key == 'models':
            status = 'listo' if latest else 'pendiente'
            count = 1 if latest else 0
        elif key == 'evidence':
            status = 'disponible' if selected else 'pendiente'
            count = completed_count
        else:
            status = 'disponible' if completed else 'pendiente'
            count = completed_count
        stage_states.append({'key': key, 'label': label, 'icon': icon,
                             'status': status, 'count': count})
    stages_by_key = {item['key']: item for item in stage_states}
    stage_tree = [{'label': label,
                   'items': [stages_by_key[key] for key in keys]}
                  for label, keys in STAGE_GROUPS]
    dataset = (latest.payload.get('data') or {}) if latest else {}
    dataset_count = len(dataset.get('inputs', [])) if isinstance(dataset, dict) else 0
    canonical_data = json.dumps(dataset, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
    fingerprint = hashlib.sha256(canonical_data.encode()).hexdigest() if latest else 'sin validar'
    input_rows = dataset.get('inputs', []) if isinstance(dataset, dict) else []
    fields = sorted({key for row in input_rows if isinstance(row, dict) for key in row})
    if not fields and input_rows:
        fields = ['valor']
    missing = sum((row.get(field) if isinstance(row, dict) else row) is None
                  for row in input_rows for field in fields)
    field_types = {}
    for field in fields:
        values = [row.get(field) if isinstance(row, dict) else row for row in input_rows]
        first_value = next((value for value in values if value is not None), None)
        field_types[field] = ('boolean' if isinstance(first_value, bool) else
                              'number' if isinstance(first_value, (int, float)) else
                              'object' if isinstance(first_value, (dict, list)) else
                              'text' if first_value is not None else 'unknown')
    preview_rows = []
    targets = dataset.get('targets') or [] if isinstance(dataset, dict) else []
    for index, row in enumerate(input_rows[:10]):
        values = row if isinstance(row, dict) else {'valor': row}
        preview_rows.append({'index': index, 'values': [values.get(field) for field in fields],
                             'target': targets[index] if index < len(targets) else None,
                             'partition': ('train' if index in dataset.get('train', []) else
                                           'test' if index in dataset.get('test', []) else 'sin asignar')})
    lineage = []
    for revision in study.revision_set.order_by('pk'):
        related_jobs = [job for job in jobs if job.revision_id == revision.pk]
        lineage.append({'revision': revision, 'jobs': related_jobs,
                        'parent': revision.parent_id, 'kind': revision.kind})
    dataset_assets = (list(dataset_revision.dataset.assets.filter(
        pk__in=dataset_revision.asset_ids).order_by('role', 'session_id', 'original_name'))
        if dataset_revision else [])
    asset_session_form = None
    asset_session_rows = []
    asset_sessions = {}
    if dataset_revision:
        from storm_studio.dataset_inventory import revision_asset_sessions

        asset_sessions = revision_asset_sessions(dataset_revision, dataset_assets)
        pose_sessions = {
            str(asset.pk): asset_sessions[str(asset.pk)]
            for asset in dataset_assets if asset.role == 'pose'
        }
        video_frame_offsets = dataset_revision.config.get('video_frame_offsets') or {}
        if not isinstance(video_frame_offsets, dict):
            video_frame_offsets = {}
        asset_session_form = AssetSessionForm(
            assets=dataset_assets, pose_sessions=pose_sessions,
            initial=asset_sessions,
            video_frame_offsets=video_frame_offsets)
        for asset in dataset_assets:
            field_name = f'asset_session_{asset.pk}'
            asset_session_rows.append({
                'asset': asset,
                'session_id': asset_sessions.get(str(asset.pk), ''),
                'field': (asset_session_form[field_name]
                          if field_name in asset_session_form.fields else None),
                'video_frame_offset_field': (
                    asset_session_form[f'asset_video_frame_offset_{asset.pk}']
                    if f'asset_video_frame_offset_{asset.pk}' in asset_session_form.fields
                    else None),
            })
        if not asset_session_form.fields:
            asset_session_form = None
            asset_session_rows = []
    derived_dataset_revisions = [
        item for item in dataset_revisions
        if item.connector == 'prepared_artifact']
    inspected_prepared_dataset = None
    requested_prepared_revision = request.GET.get('prepared_revision')
    if section == 'data' and requested_prepared_revision:
        inspected_prepared_dataset = next(
            (item for item in derived_dataset_revisions
             if str(item.pk) == requested_prepared_revision), None)
        if inspected_prepared_dataset is None:
            raise Http404
    session_partition_form = None
    session_partition_rows = []
    if dataset_revision and dataset_revision.status == 'ready':
        session_records = dataset_revision.inventory.get('sessions', [])
        reserved_ranges = dataset_revision.config.get('reserved_evaluation_ranges') or {}
        if not isinstance(reserved_ranges, dict):
            reserved_ranges = {}
        session_partition_form = SessionPartitionForm(
            sessions=[item['session_id'] for item in session_records],
            initial=dataset_revision.config.get('session_partitions', {}),
            reserved_ranges=reserved_ranges)
        session_partition_rows = [
            {'session_id': item['session_id'], 'frames': item.get('frames', 0),
             'segments': item.get('segments', 0),
             'field': session_partition_form[f'session_partition_{index}'],
             'reserved_start': session_partition_form[f'reserved_start_{index}'],
             'reserved_stop': session_partition_form[f'reserved_stop_{index}']}
            for index, item in enumerate(session_records)
        ]
    dataset_summary = {
        'connector': (dataset_revision.connector if dataset_revision else
                      latest.payload.get('connector', 'numeric_json') if latest else 'sin seleccionar'),
        'rows': (dataset_revision.inventory.get('frame_count', 0)
                 if dataset_revision and dataset_revision.status == 'ready' else dataset_count),
        'fingerprint': (dataset_revision.inventory.get('source_fingerprint', 'pendiente')
                        if dataset_revision and dataset_revision.status == 'ready' else
                        latest.payload.get('data_fingerprint', fingerprint) if latest else 'sin validar'),
    }
    pose_feature_names = (dataset_revision.inventory.get('feature_names', [])
                          if dataset_revision else [])
    pose_feature_names = [name for name in pose_feature_names if isinstance(name, str)]
    pose_coordinate_parts = sorted({name[:-2] for name in pose_feature_names
                                    if name.endswith('_x')
                                    and f'{name[:-2]}_y' in pose_feature_names})
    video_previews = []
    if dataset_revision:
        from storm_studio.video_previews import latest_video_preview_job, preview_path

        video_frame_offsets = dataset_revision.config.get('video_frame_offsets') or {}
        if not isinstance(video_frame_offsets, dict):
            video_frame_offsets = {}
        for asset in dataset_assets:
            if asset.role != 'video':
                continue
            preview_job = latest_video_preview_job(
                study.pk, dataset_revision.pk, asset.pk)
            video_previews.append({
                'asset_id': asset.pk,
                'session_id': asset_sessions.get(str(asset.pk), ''),
                'name': asset.original_name,
                'content_type': mimetypes.guess_type(asset.original_name)[0] or '',
                'frame_offset': _integer_or_default(
                    video_frame_offsets.get(str(asset.pk), 0), default=0),
                'url': reverse('video-preview', args=(study.pk, asset.pk)),
                'prepare_url': reverse('prepare-video-preview', args=(study.pk, asset.pk)),
                'preview_available': bool(preview_path(preview_job, settings.WORKSPACE)),
                'preview_status': preview_job.status if preview_job else '',
            })
    from storm_studio.video_timeline_reviews import review_state

    video_timeline_review = review_state(study, dataset_revision)
    label_corrections = _label_corrections_for_revision(study, dataset_revision)
    requested_prediction_ids = [
        value for item in request.GET.getlist('predictions')
        for value in item.split(',') if value]
    if not requested_prediction_ids and restored_snapshot:
        snapshot_predictions = restored_snapshot.payload.get(
            'visual_state', {}).get('selected_predictions', [])
        if isinstance(snapshot_predictions, list):
            requested_prediction_ids = [value for value in snapshot_predictions
                                        if isinstance(value, str)]
    pose_prediction_runs, selected_pose_prediction_ids = _pose_prediction_run_options(
        study, dataset_revision, completed, requested_prediction_ids)
    frozen_reports = list(study.revision_set.filter(
        kind='frozen_report').order_by('-pk')[:50])
    return render(request, 'storm_studio/study.html', {
        'study': study, 'section': section, 'title': dict(PAGES)[section],
        'advanced_pages': ADVANCED_PAGES,
        'form': form, 'latest': latest, 'jobs': jobs, 'completed': completed,
        'execution_summary': execution_summary,
        'recipe_presets': recipe_presets,
        'selected_recipe_preset': selected_preset,
        'required_pipeline_steps': [
            {'type': step, 'label': PREPARATION_STEP_UI.get(step, {}).get('label', step)}
            for step in request.GET.getlist('required_step')
            if step in ui_catalog.steps.available
        ],
        'required_model_label': {
            'vame_native': 'VAME nativo', 'vame_official': 'VAME oficial',
        }.get(request.GET.get('required_model'), request.GET.get('required_model', 'el modelo')),
        'required_recipe_preset': ui_catalog.recipe_presets.get({
            'vame_native': 'vame_native_pose_ego',
            'vame_official': 'vame_official_ego_roi',
        }.get(request.GET.get('required_model'), '')),
        'requires_temporal_windows': (
            'pose.temporal_windows' in request.GET.getlist('required_step')
        ),
        'plan_has_active_jobs': plan_has_active_jobs,
        'preparation_revisions': preparation_revisions,
        'archived_preparations': archived_preparations,
        'derived_dataset_revisions': derived_dataset_revisions,
        'inspected_prepared_dataset': inspected_prepared_dataset,
        'preparation_step_catalog': preparation_step_catalog(),
        'dataset_feature_names': (dataset_revision.inventory.get('feature_names', [])
                                  if dataset_revision else []),
        'pose_coordinate_parts': pose_coordinate_parts,
        'pose_motion_preview': _pose_motion_preview(dataset_revision),
        'pose_preview_store': (dataset_revision.inventory.get('pose_preview_store')
                               if dataset_revision else None),
        'pose_preview_endpoint': (reverse('pose-preview', args=(
            study.pk, dataset_revision.pk))
            if dataset_revision and dataset_revision.inventory.get('pose_preview_store')
            else ''),
        'pose_preview_is_balanced': bool(dataset_revision and (
            dataset_revision.inventory.get('preview_strategy') == PREVIEW_STRATEGY
            or dataset_revision.inventory.get('frame_count', 0)
            <= len(dataset_revision.inventory.get('preview', [])))),
        'pose_motion_rois': (dataset_revision.inventory.get('roi', [])
                             if dataset_revision else []),
        'pose_preview_fps': dataset_revision.config.get('fps', 30) if dataset_revision else 30,
        'pose_preview_frame_base': (dataset_revision.config.get('pose_frame_base', 0)
                                    if dataset_revision else 0),
        'label_corrections': label_corrections,
        'pose_prediction_runs': pose_prediction_runs,
        'pose_prediction_job_ids': selected_pose_prediction_ids,
        'frozen_reports': frozen_reports,
        'label_correction_taxonomy': (dataset_revision.inventory.get('taxonomy', [])
                                      if dataset_revision else []),
        'label_correction_url': reverse('label-correction', args=(study.pk,)),
        'video_previews': video_previews,
        'video_timeline_review': video_timeline_review,
        'video_discontinuity_override_rows': _video_discontinuity_override_rows(
            dataset_revision),
        'upload_form': DatasetUploadForm(initial={
            'pose_adapter': dataset_revision.connector if dataset_revision else None,
            'fps': dataset_revision.config.get('fps', 30) if dataset_revision else 30,
            'hdf_key': dataset_revision.config.get('hdf_key', '') if dataset_revision else '',
            'csv_frame_base': (dataset_revision.config.get('csv_frame_base', 1)
                               if dataset_revision else 1),
            'pose_frame_base': (dataset_revision.config.get('pose_frame_base', 0)
                                if dataset_revision else 0),
            'label_frame_reference': (dataset_revision.config.get(
                'label_frame_reference', 'pose') if dataset_revision else 'pose')}),
        'dataset_revision': dataset_revision, 'dataset_assets': dataset_assets,
        'asset_session_form': asset_session_form,
        'asset_session_rows': asset_session_rows,
        'session_partition_form': session_partition_form,
        'session_partition_rows': session_partition_rows,
        'dataset_adapter_map': {str(item.pk): item.connector for item in dataset_revisions},
        'editor_data': editor_data, 'partition_fields': partition_fields,
        'partition_rows': partition_rows,
        'resumable': resumable,
        'selected': selected, 'evidence': evidence, 'catalog': ui_catalog.describe(),
        'evidence_kind': 'numeric' if numeric_evidence else 'categorical',
        'output_metadata': output_metadata,
        'annotation': annotation, 'visualizers': ui_catalog.visualizations.available,
        'registered_steps': ui_catalog.steps.available,
        'registered_metrics': ui_catalog.metrics.available,
        'registered_connectors': list(ui_catalog.connectors),
        'comparable': services.comparable(compared_jobs) if compared_jobs else False,
        'comparison_reasons': services.compare_reasons(compared_jobs) if compared_jobs else ['Select completed executions'],
        'comparison_requested': comparison_requested,
        'selected_comparison_ids': [str(job.pk) for job in selected_comparison],
        'compared_jobs': compared_jobs,
        'comparison_metrics': comparison_metrics,
        'visual_state': restored_snapshot.payload.get('visual_state', {}) if restored_snapshot else {},
        'visual_state_json': json.dumps(restored_snapshot.payload.get('visual_state', {}) if restored_snapshot else {}),
        'restored_snapshot': restored_snapshot,
        'comparison_rows': comparison_rows,
        'lifecycle': lifecycle,
        'stage_states': stage_states,
        'stage_tree': stage_tree,
        'next_action': next_action,
        'dataset_summary': {
            **dataset_summary,
        },
        'dataset_fields': fields, 'dataset_types': field_types,
        'dataset_preview': preview_rows, 'dataset_missing': missing,
        'lineage': lineage,
        'revisions': study.revision_set.order_by('-pk'),
        'reviews': study.revision_set.filter(kind='review').order_by('-pk')})


@require_POST
def preview_pipeline(request, study_id):
    study = get_object_or_404(Study, pk=study_id)
    try:
        from storm.suite import transform_aligned, validate_data

        dataset_revision_id = request.POST.get('dataset_revision_id')
        if dataset_revision_id:
            from storm.artifacts import ArtifactRef, FileArtifactStore

            source = get_object_or_404(
                DatasetRevision, pk=dataset_revision_id,
                dataset_id=study.dataset_revision.dataset_id if study.dataset_revision_id else None,
                status='ready')
            from storm_studio.video_timeline_reviews import require_review

            require_review(study, source)
            if not source.artifact_ref:
                raise ValueError('El inventario no tiene un artefacto de datos para previsualizar.')
            data = FileArtifactStore(settings.ARTIFACT_ROOT).load(
                ArtifactRef.from_dict(source.artifact_ref))
            if not isinstance(data, dict) or not isinstance(data.get('inputs'), list):
                raise ValueError('El artefacto no contiene observaciones preparables.')
            inputs = data['inputs']
            observation_ids = data.get('observation_ids') or [str(i) for i in range(len(inputs))]
            partitions = data.get('partitions') or ['unassigned'] * len(inputs)
            if len(observation_ids) != len(inputs) or len(partitions) != len(inputs):
                raise ValueError('Los metadatos del inventario no están alineados con las observaciones.')
            training_indices = [i for i, partition in enumerate(partitions)
                                if partition == 'train']
            reserved = data.get('reserved_evaluation') or [False] * len(inputs)
            if len(reserved) != len(inputs):
                raise ValueError('La reserva de evaluación no está alineada con las observaciones.')
            fit_indices = [index for index in training_indices if not reserved[index]]
            evaluation_indices = [i for i, partition in enumerate(partitions)
                                  if partition != 'train' or reserved[i]]
            if not training_indices:
                raise ValueError('Asigná al menos una sesión a entrenamiento antes de previsualizar.')
            context_metadata = {
                key: data[key] for key in (
                    'feature_names', 'likelihoods', 'likelihood_bodyparts', 'frames',
                    'sessions', 'segments', 'partitions', 'reserved_evaluation')
                if key in data
            }
            connector = source.connector
        else:
            data = json.loads(request.POST.get('data', '{}'))
            connector = request.POST.get('connector', 'numeric_json')
            inputs, _, training_indices, _ = validate_data(
                data, numeric=connector == 'numeric_json')
            evaluation_indices = data.get('validation') or data.get('test', [])
            fit_indices = training_indices
            observation_ids = data.get('observation_ids') or [str(i) for i in range(len(inputs))]
            context_metadata = {}

        steps = json.loads(request.POST.get('steps', '[]'))
        catalog = services.catalog()
        if dataset_revision_id:
            from storm_studio.data_preparation import resolve_preparation_steps

            steps = resolve_preparation_steps(steps, data.get('feature_names', []))
        if not dataset_revision_id and connector not in catalog.connectors:
            raise ValueError('Conector desconocido')
        if not isinstance(steps, list):
            raise ValueError('Los pasos deben formar una lista')
        for step in steps:
            if (not isinstance(step, dict) or
                    step.get('type') not in (*catalog.steps.available, 'center')):
                raise ValueError('La pipeline contiene un paso desconocido')
            if step['type'] == 'scale':
                from math import isfinite
                if not isfinite(float(step['factor'])):
                    raise ValueError('El factor de escala debe ser un número finito')
        training_stage_trace = []
        evaluation_stage_trace = []
        training, fitted, training_indices = transform_aligned(
            [inputs[index] for index in training_indices], training_indices, steps,
            catalog=catalog, context_metadata=context_metadata,
            stage_trace=training_stage_trace,
            fit_observation_indices=fit_indices)
        evaluation, _, evaluation_indices = transform_aligned(
            [inputs[index] for index in evaluation_indices], evaluation_indices,
            steps, learned=fitted, catalog=catalog,
            context_metadata=context_metadata, stage_trace=evaluation_stage_trace)

        def rows(indices, prepared):
            return [{'index': index,
                     'observation_id': observation_ids[index],
                     'input': inputs[index], 'prepared': value}
                    for index, value in list(zip(indices, prepared))[:5]]

        def stage_rows(trace):
            result = []
            reserved_rows = data.get('reserved_evaluation') or [False] * len(inputs)
            if len(reserved_rows) != len(inputs):
                reserved_rows = [False] * len(inputs)
            for row in trace['rows']:
                index = row['observation_index']
                result.append({
                    'observation_id': observation_ids[index],
                    'session_id': (data.get('sessions') or [None] * len(inputs))[index],
                    'frame': (data.get('frames') or list(range(len(inputs))))[index],
                    'reserved_evaluation': reserved_rows[index],
                    'value': row['value'],
                })
            return result

        stages = [{
            'type': step['type'],
            'training_row_count': training_stage_trace[index]['row_count'],
            'evaluation_row_count': evaluation_stage_trace[index]['row_count'],
            'training': stage_rows(training_stage_trace[index]),
            'evaluation': stage_rows(evaluation_stage_trace[index]),
        } for index, step in enumerate(steps)]

        return JsonResponse({
            'fit_partition': 'train',
            'training': rows(training_indices, training),
            'evaluation': rows(evaluation_indices, evaluation),
            'stages': stages,
        })
    except (ValueError, KeyError, TypeError, OverflowError, OSError) as error:
        return JsonResponse({'error': str(error)}, status=400)


@transaction.atomic
@require_POST
def run(request, revision_id):
    revision = get_object_or_404(
        Revision.objects.select_for_update(), pk=revision_id, kind='plan')
    dataset_revision_id = revision.payload.get('dataset_revision_id')
    if dataset_revision_id is not None:
        from storm_studio.video_timeline_reviews import require_review

        dataset_revision = DatasetRevision.objects.filter(pk=dataset_revision_id).first()
        if dataset_revision is not None:
            try:
                require_review(revision.study, dataset_revision)
            except ValueError as error:
                messages.error(request, str(error))
                return redirect('page', revision.study_id, 'data')
    active_jobs = Job.objects.filter(
        revision__study_id=revision.study_id,
        status__in=('pending', 'running'),
        operation__in=('train', 'infer'),
    ).filter(Q(revision_id=revision.pk) | Q(revision__parent_id=revision.pk))
    if active_jobs.exists():
        messages.info(
            request,
            'Este plan ya tiene una corrida en cola o en curso. Revisá Ejecuciones antes de iniciarlo de nuevo.',
        )
        return redirect('page', revision.study_id, 'jobs')

    branch_models = list(dict.fromkeys(
        [revision.payload.get('model'), *revision.payload.get('branch_models', [])]))
    selected_models = request.POST.getlist('models')
    planned_models = (branch_models if request.POST.get('run_branches') else
                      selected_models or [revision.payload.get('model')])
    worker_catalog = services.catalog()
    for model_name in planned_models:
        missing_steps = missing_required_pipeline_steps(
            model_name, revision.payload.get('steps', []), worker_catalog)
        if missing_steps:
            model_label = {
                'vame_native': 'VAME nativo',
                'vame_official': 'VAME oficial',
            }.get(model_name, model_name)
            missing_labels = [
                PREPARATION_STEP_UI.get(step, {}).get('label', step)
                for step in missing_steps
            ]
            if model_name == 'vame_native' and 'pose.temporal_windows' in missing_steps:
                explanation = (
                    'Las ventanas agrupan frames vecinos para que el modelo pueda '
                    'aprender patrones de movimiento.'
                )
            else:
                explanation = 'El modelo no puede iniciar sin estos pasos de entrada.'
            messages.error(
                request,
                f"No se inició la corrida de {model_label}. Falta "
                f"{', '.join(missing_labels)} en la configuración del modelo. "
                f"{explanation} No se creó una ejecución ni se modificaron los datos. "
                'Cargá la receta del modelo en Configurar, guardá el plan y volvé a iniciarlo.',
            )
            target = reverse('page', args=(revision.study_id, 'prepare'))
            target += '?' + urlencode({
                'required_step': missing_steps,
                'required_model': model_name,
            }, doseq=True)
            return redirect(target)
    if request.POST.get('run_branches'):
        primary_model = revision.payload.get('model')
        try:
            worker_catalog.validate(primary_model, revision.payload.get('config', {}))
            for model in branch_models[1:]:
                worker_catalog.validate(
                    model, revision.payload.get('branch_configs', {}).get(model, {}))
        except (KeyError, ValueError) as error:
            return HttpResponse(f'Unknown or incompatible model: {error}', status=400)
        from django.db import transaction
        with transaction.atomic():
            services.submit(revision)
            for model in branch_models[1:]:
                config = revision.payload.get('branch_configs', {}).get(model, {})
                payload = dict(revision.payload, model=model, config=config)
                variant = Revision.objects.create(study=revision.study, kind='plan',
                                                  parent=revision, payload=payload)
                services.submit(variant)
        return redirect('page', revision.study_id, 'jobs')
    if selected_models:
        from django.db import transaction
        with transaction.atomic():
            for model in selected_models:
                try:
                    worker_catalog.validate(model, {})
                except (KeyError, ValueError):
                    return HttpResponse('Unknown or incompatible model', status=400)
            for model in selected_models:
                payload = dict(revision.payload, model=model, config={})
                variant = Revision.objects.create(study=revision.study, kind='plan', parent=revision, payload=payload)
                services.submit(variant)
    else:
        services.submit(revision)
    return redirect('page', revision.study_id, 'jobs')


@require_POST
def action(request, job_id, operation):
    job = get_object_or_404(Job.objects.select_related('revision__study'), pk=job_id)
    try:
        if operation == 'apply':
            if (job.status != 'completed'
                    or 'infer' not in job.result.get('capabilities', [])
                    or not job.result.get('model_ref')):
                raise ValueError('Elegí una corrida completada cuyo modelo admita inferencia.')
            study = job.revision.study
            target = get_object_or_404(
                DatasetRevision, pk=request.POST.get('dataset_revision_id'), status='ready')
            if (not study.dataset_revision_id or
                    target.dataset_id != study.dataset_revision.dataset_id):
                raise ValueError('Elegí una revisión del dataset de este estudio.')
            source_spec = job.result.get('spec', {})
            source_connector = source_spec.get('connector', 'numeric_json')
            if target.connector != source_connector or not target.artifact_ref:
                raise ValueError('La revisión seleccionada no coincide con el adapter del modelo.')
            payload = dict(job.revision.payload)
            payload.update({
                'operation': 'infer', 'dataset_revision_id': target.pk,
                'connector': target.connector, 'data': {},
                'steps': source_spec.get('steps', payload.get('steps', [])),
            })
            payload.pop('preparation_revision_id', None)
            payload.pop('branch_models', None)
            payload.pop('branch_configs', None)
            from django.db import transaction
            with transaction.atomic():
                revision = Revision.objects.create(
                    study=study, kind='plan', parent=job.revision, payload=payload)
                services.submit(revision, operation='apply', source=job)
            messages.success(request, f'Inferencia en cola sobre dataset r{target.number}.')
            return redirect('page', study.pk, 'jobs')
        if operation == 'infer':
            predictions = services.predict(job, json.loads(request.POST.get('inputs', '[]')))
            return render(request, 'storm_studio/prediction.html', {'job': job, 'predictions': predictions})
        if operation == 'annotate':
            author = (request.user.get_username() if request.user.is_authenticated
                      else request.POST.get('author', ''))
            services.annotate(job, taxonomy=json.loads(request.POST.get('taxonomy', '[]')),
                              intervals=json.loads(request.POST.get('intervals', '[]')),
                              mapping=json.loads(request.POST.get('mapping', '{}')),
                              author=author, reason=request.POST.get('reason', ''))
            return redirect('page', job.revision.study_id, 'evidence')
        if operation == 'update':
            from storm.suite import validate_data
            if 'update' not in job.result.get('capabilities', []):
                raise ValueError('This model does not support incremental updates')
            data = json.loads(request.POST.get('data', '{}'))
            validate_data(data, numeric=job.result['spec'].get('connector', 'numeric_json') == 'numeric_json')
            spec = dict(job.revision.payload, data=data)
            revision = Revision.objects.create(study=job.revision.study, kind='plan', parent=job.revision, payload=spec)
            services.submit(revision, operation='update', source=job)
            return redirect('page', job.revision.study_id, 'jobs')
        if operation == 'resume':
            from storm.artifacts import FileArtifactStore
            FileArtifactStore(settings.ARTIFACT_ROOT).resolve(kind='checkpoints', artifact_id=str(job.pk))
            services.submit(job.revision, previous=job, operation='resume', source=job)
            return redirect('page', job.revision.study_id, 'jobs')
        if operation == 'review':
            services.propose(job, seed=42, strategy=request.POST.get('strategy', 'random'))
            return redirect('page', job.revision.study_id, 'review')
        if operation == 'cancel':
            from django.utils import timezone
            Job.objects.filter(pk=job.pk, status__in=['pending', 'running']).update(status='cancelled', finished=timezone.now())
            if job.operation == 'inventory':
                DatasetRevision.objects.filter(
                    pk=job.revision.payload.get('dataset_revision')).update(status='cancelled')
        elif operation == 'evaluate':
            from storm.suite import evaluate
            result = evaluate(job.result, json.loads(request.POST.get('inputs', '[]')),
                              json.loads(request.POST.get('targets', '[]')), settings.ARTIFACT_ROOT, services.catalog())
            Revision.objects.create(study=job.revision.study, kind='evaluation', parent=job.revision, payload=result)
            return JsonResponse(result)
        elif operation == 'adopt':
            if job.status != 'completed':
                raise ValueError('Only a completed candidate may be adopted')
            reason = request.POST.get('reason', '').strip()[:2000]
            if not reason:
                raise ValueError('Provide a human justification before adopting this candidate')
            study = job.revision.study
            Revision.objects.create(study=study, kind='adoption', parent=job.revision,
                                    payload={'job': str(job.pk), 'previous': str(study.adopted_id) if study.adopted_id else None,
                                             'reason': reason})
            study.adopted = job
            study.save(update_fields=['adopted'])
        elif operation == 'retry':
            if job.operation == 'inventory':
                dataset_revision = get_object_or_404(
                    DatasetRevision, pk=job.revision.payload.get('dataset_revision'))
                services.enqueue_inventory(job.revision.study, dataset_revision)
            elif job.operation == 'prepare':
                services.enqueue_preparation(job.revision.study, job.revision)
            else:
                services.submit(job.revision, previous=job, operation=job.operation, source=job.source)
        else:
            raise Http404
    except (ValueError, KeyError, TypeError, OSError, ImportError) as error:
        return HttpResponse(str(error), status=400, content_type='text/plain')
    return redirect('page', job.revision.study_id, 'jobs')


@require_POST
def revise(request, revision_id):
    revision = get_object_or_404(Revision, pk=revision_id)
    try:
        if revision.kind == 'review':
            services.review(revision, json.loads(request.POST.get('labels', '{}')),
                            json.loads(request.POST.get('constraints', '[]')))
        elif revision.kind == 'plan':
            Revision.objects.create(study=revision.study, kind='plan', parent=revision, payload=revision.payload)
        elif revision.kind == 'snapshot':
            restored = Revision.objects.create(study=revision.study, kind='snapshot', parent=revision, payload=revision.payload)
            plan_id = revision.payload.get('plan_revision')
            if plan_id:
                plan = get_object_or_404(Revision, pk=plan_id, study=revision.study, kind='plan')
                Revision.objects.create(study=plan.study, kind='plan', parent=plan, payload=plan.payload)
            return redirect(f'/studies/{revision.study_id}/{revision.payload["section"]}/?snapshot={restored.pk}')
        else:
            return HttpResponse(status=400)
    except (ValueError, TypeError, KeyError) as error:
        return HttpResponse(str(error), status=400, content_type='text/plain')
    return redirect('page', revision.study_id, 'flow')


def report(request, job_id, format):
    job = get_object_or_404(Job, pk=job_id, status='completed')
    if format == 'json':
        response = JsonResponse(job.result, json_dumps_params={'indent': 2})
    elif format == 'csv':
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['index', 'prediction'])
        writer.writerows(zip(job.result['indices'], job.result['predictions']))
        response = HttpResponse(stream.getvalue(), content_type='text/csv')
    elif format == 'html':
        response = render(request, 'storm_studio/report.html', {'job': job, 'manifest': json.dumps(job.result, indent=2)})
    else:
        raise Http404
    response['Content-Disposition'] = f'attachment; filename="storm-{job.pk}.{format}"'
    return response


def aggregate_report(request, study_id, format):
    study = get_object_or_404(Study, pk=study_id)
    jobs = list(Job.objects.filter(revision__study=study, status='completed').order_by('created'))
    payload = {'study': study.pk, 'jobs': [
        {'execution_id': str(job.pk), 'model': job.result.get('model'),
         'metrics': job.result.get('metrics', {}), 'partition': job.result.get('partition'),
         'data_fingerprint': job.result.get('data_fingerprint')}
        for job in jobs]}
    if format == 'json':
        response = JsonResponse(payload, json_dumps_params={'indent': 2})
    elif format == 'csv':
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['execution_id', 'model', 'partition', 'metrics'])
        for row in payload['jobs']:
            writer.writerow([row['execution_id'], row['model'], row['partition'], json.dumps(row['metrics'])])
        response = HttpResponse(stream.getvalue(), content_type='text/csv')
    elif format == 'html':
        response = HttpResponse('<html><body><h1>STORM aggregate report</h1><pre>'
                                + json.dumps(payload, indent=2) + '</pre></body></html>')
    else:
        raise Http404
    response['Content-Disposition'] = f'attachment; filename="storm-study-{study.pk}.{format}"'
    return response


@require_POST
def freeze_study_report(request, study_id):
    study = get_object_or_404(Study, pk=study_id)
    job_ids = [value for item in request.POST.getlist('jobs')
               for value in item.split(',') if value]
    from storm_studio.study_reports import freeze_study_report as freeze_report

    try:
        frozen = freeze_report(
            study, job_ids,
            snapshot_revision_id=request.POST.get('snapshot_revision_id') or None)
    except ValueError as error:
        return HttpResponse(str(error), status=400, content_type='text/plain')
    return redirect(f'{reverse("page", args=(study.pk, "reports"))}'
                    f'?frozen_report={frozen.pk}')


@require_GET
def frozen_study_report(request, revision_id, format):
    frozen = get_object_or_404(Revision, pk=revision_id, kind='frozen_report')
    payload = frozen.payload
    if format == 'json':
        response = JsonResponse(
            {'report_revision_id': frozen.pk, **payload},
            json_dumps_params={'indent': 2, 'ensure_ascii': False})
    elif format == 'csv':
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow([
            'record_type', 'execution_id', 'model', 'partition', 'index',
            'observation_id', 'target', 'prediction', 'prediction_valid',
            'metric', 'metric_value', 'interval_annotations', 'group_interpretation',
        ])
        for run in payload.get('runs', []):
            result = run.get('result') or {}
            for name, value in (result.get('metrics') or {}).items():
                writer.writerow([
                    'metric', run.get('job_id'), result.get('model'),
                    result.get('partition'), '', '', '', '', '', name,
                    json.dumps(value, ensure_ascii=False), '', '',
                ])
            data = result.get('resolved_data') or {}
            inputs = data.get('inputs') or []
            targets = data.get('targets') or []
            observation_ids = data.get('observation_ids') or []
            annotations = run.get('annotations') or []
            predictions = result.get('predictions', [])
            prediction_mask = result.get('prediction_mask')
            if not isinstance(prediction_mask, list) or len(prediction_mask) != len(predictions):
                prediction_mask = [True] * len(predictions)
            for index, prediction, prediction_valid in zip(
                    result.get('indices', []), predictions, prediction_mask):
                valid_index = type(index) is int and 0 <= index < len(inputs)
                interval_labels = []
                group_interpretation = None
                for annotation in annotations:
                    if not annotation.get('applies_to_run'):
                        continue
                    annotation_payload = annotation.get('payload') or {}
                    for interval in annotation_payload.get('intervals', []):
                        if (valid_index and isinstance(interval, dict)
                                and type(interval.get('start')) is int
                                and type(interval.get('stop')) is int
                                and interval['start'] <= index < interval['stop']
                                and isinstance(interval.get('label'), str)
                                and interval['label'] not in interval_labels):
                            interval_labels.append(interval['label'])
                    mapping = annotation_payload.get('mapping') or {}
                    if (prediction_valid is True and isinstance(mapping, dict)
                            and str(prediction) in mapping):
                        group_interpretation = mapping[str(prediction)]
                writer.writerow([
                    'prediction', run.get('job_id'), result.get('model'),
                    result.get('partition'), index,
                    observation_ids[index] if valid_index and index < len(observation_ids) else '',
                    targets[index] if valid_index and index < len(targets) else '',
                    prediction, prediction_valid, '', '',
                    json.dumps(interval_labels, ensure_ascii=False), group_interpretation or '',
                ])
        response = HttpResponse(stream.getvalue(), content_type='text/csv; charset=utf-8')
    elif format == 'html':
        response = render(request, 'storm_studio/frozen_report.html', {
            'report': frozen,
            'manifest': json.dumps(payload, indent=2, ensure_ascii=False),
        })
    else:
        raise Http404
    response['Content-Disposition'] = (
        f'attachment; filename="storm-study-report-r{frozen.pk}.{format}"')
    return response


@require_GET
def frozen_study_bundle(request, revision_id):
    frozen = get_object_or_404(Revision, pk=revision_id, kind='frozen_report')
    from storm_studio.study_bundles import (
        FrozenReportBundleError, write_frozen_report_bundle,
    )

    workspace = Path(settings.WORKSPACE)
    workspace.mkdir(parents=True, exist_ok=True)
    bundle = tempfile.TemporaryFile(mode='w+b', dir=workspace)
    try:
        write_frozen_report_bundle(
            frozen, bundle, workspace=workspace,
            artifact_root=settings.ARTIFACT_ROOT)
        size = bundle.tell()
        bundle.seek(0)
    except FrozenReportBundleError as error:
        bundle.close()
        return HttpResponse(str(error), status=409, content_type='text/plain; charset=utf-8')
    except Exception:
        bundle.close()
        raise
    response = FileResponse(
        bundle, as_attachment=True,
        filename=f'storm-study-report-r{frozen.pk}.zip',
        content_type='application/zip')
    response['Content-Length'] = str(size)
    return response


def status(request, study_id):
    get_object_or_404(Study, pk=study_id)
    jobs = list(Job.objects.filter(revision__study_id=study_id)
                .select_related('revision').order_by('-created'))
    _decorate_execution_progress(jobs)
    completed_count = sum(
        job.status == 'completed'
        and job.operation not in {'inventory', 'prepare', 'video_preview'}
        for job in jobs)
    return JsonResponse({
        'summary': _execution_summary(jobs, completed_count),
        'jobs': [{
        'id': str(job.pk), 'status': job.status,
        'status_label': job.status_label,
        'progress': job.progress_for_ui,
        'progress_percent': job.progress_percent,
        'eta_seconds': job.eta_seconds,
        'eta_text': job.eta_text,
        'eta_basis': job.eta_basis,
    } for job in jobs]})


def video_preview(request, study_id, asset_id):
    study = get_object_or_404(Study.objects.select_related('dataset_revision'), pk=study_id)
    if not study.dataset_revision_id:
        raise Http404
    revision = study.dataset_revision
    if asset_id not in revision.asset_ids:
        raise Http404
    asset = get_object_or_404(
        DatasetAsset, pk=asset_id, dataset_id=revision.dataset_id, role='video')
    workspace = Path(settings.WORKSPACE).resolve()
    path = (workspace / asset.relative_path).resolve()
    if not path.is_relative_to(workspace) or not path.is_file():
        raise Http404
    from storm_studio.video_previews import latest_video_preview_job, preview_path

    preview_job = latest_video_preview_job(study.pk, revision.pk, asset.pk)
    compatible_path = preview_path(preview_job, workspace)
    content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    if compatible_path:
        path = compatible_path
        content_type = 'video/mp4'
    size = path.stat().st_size
    range_header = request.headers.get('Range')
    if range_header:
        selected = _parse_video_range(range_header, size)
        if selected is None:
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
            return response
        start, end = selected
        status_code = 206
    else:
        start, end = 0, size - 1
        status_code = 200
    length = max(0, end - start + 1)
    source = path.open('rb')
    source.seek(start)

    def chunks():
        remaining = length
        try:
            while remaining:
                chunk = source.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            source.close()

    response = StreamingHttpResponse(
        chunks(), status=status_code,
        content_type=content_type)
    response['Accept-Ranges'] = 'bytes'
    response['Content-Length'] = str(length)
    response['X-Content-Type-Options'] = 'nosniff'
    if status_code == 206:
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
    return response


@require_GET
def pose_preview_data(request, study_id, dataset_revision_id):
    study = get_object_or_404(Study.objects.select_related('dataset_revision'), pk=study_id)
    if not study.dataset_revision_id:
        raise Http404
    revision = get_object_or_404(
        DatasetRevision, pk=dataset_revision_id,
        dataset_id=study.dataset_revision.dataset_id, status='ready')
    store_ref = revision.inventory.get('pose_preview_store')
    if not isinstance(store_ref, dict):
        raise Http404
    session_id = request.GET.get('session_id', '')
    if not session_id:
        return JsonResponse({'error': 'Select a registered pose session.'}, status=400)
    try:
        offset = int(request.GET.get('offset', '0'))
        limit = int(request.GET.get('limit', str(store_ref.get('chunk_size', 512))))
        frame_value = request.GET.get('frame')
        frame = int(frame_value) if frame_value not in (None, '') else None
        frame_field = request.GET.get('frame_field', 'frame')
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Offset, limit and frame must be integers.'}, status=400)
    from storm_studio.pose_preview import read_pose_preview_page

    try:
        payload = read_pose_preview_page(
            root=settings.ARTIFACT_ROOT, store_ref=store_ref,
            session_id=session_id, offset=offset, limit=limit, frame=frame,
            frame_field=frame_field)
    except KeyError:
        raise Http404
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
        return JsonResponse({'error': str(error)}, status=409)
    requested_ids = [
        value for item in request.GET.getlist('predictions')
        for value in item.split(',') if value]
    if requested_ids:
        try:
            requested_ids = list(dict.fromkeys(
                str(uuid.UUID(value)) for value in requested_ids))
        except (TypeError, ValueError, AttributeError):
            return JsonResponse({'error': 'A selected prediction run ID is invalid.'}, status=400)
        if len(requested_ids) > MAX_POSE_PREDICTION_RUNS:
            return JsonResponse({
                'error': f'Select no more than {MAX_POSE_PREDICTION_RUNS} prediction runs.'},
                status=400)
        run_ids = [uuid.UUID(value) for value in requested_ids]
        runs = list(Job.objects.filter(
            pk__in=run_ids, revision__study=study, status='completed'))
        by_id = {str(job.pk): job for job in runs}
        if len(by_id) != len(requested_ids):
            raise Http404
        available, _ = _pose_prediction_run_options(
            study, revision, runs, requested_ids)
        if len(available) != len(requested_ids):
            return JsonResponse({
                'error': 'A selected run has no verified alignment with this pose dataset.'},
                status=409)
        for job_id in requested_ids:
            job = by_id[job_id]
            result = job.result
            data = result['resolved_data']
            inputs = data['inputs']
            observation_ids = data['observation_ids']
            sessions = data['sessions']
            frames = data['frames']
            identity_index = {observation_id: index
                              for index, observation_id in enumerate(observation_ids)}
            indices = result['indices']
            predictions = result['predictions']
            mask = result.get('prediction_mask', [True] * len(indices))
            output_by_index = {
                index: {'prediction': prediction, 'valid': valid}
                for index, prediction, valid in zip(indices, predictions, mask)
            }
            annotation = Revision.objects.filter(
                study=study, kind='annotations', payload__job=job_id
            ).order_by('-pk').first()
            annotation_payload = {}
            fingerprint = result.get('data_fingerprint')
            if (annotation and isinstance(fingerprint, str) and fingerprint
                    and annotation.payload.get('data_fingerprint') == fingerprint):
                annotation_payload = annotation.payload
            intervals = [interval for interval in annotation_payload.get('intervals', [])
                         if isinstance(interval, dict)
                         and type(interval.get('start')) is int
                         and type(interval.get('stop')) is int
                         and 0 <= interval['start'] < interval['stop'] <= len(inputs)
                         and isinstance(interval.get('label'), str)]
            interpretation_map = annotation_payload.get('mapping', {})
            if not isinstance(interpretation_map, dict):
                interpretation_map = {}
            semantics = (result.get('output_metadata') or {}).get(
                'semantics', 'Semántica no declarada')
            for row in payload['rows']:
                index = identity_index.get(row['observation_id'])
                if (index is None or str(sessions[index]) != row['session_id']
                        or frames[index] != row['frame']):
                    continue
                output = output_by_index.get(index)
                model = result.get('model', (result.get('spec') or {}).get(
                    'model', 'Modelo'))
                interval_labels = list(dict.fromkeys(
                    interval['label'] for interval in intervals
                    if interval['start'] <= index < interval['stop']))
                interpretation = (interpretation_map.get(str(output['prediction']))
                                  if output and output['valid'] else None)
                if interval_labels or interpretation is not None:
                    row.setdefault('run_annotations', []).append({
                        'job_id': job_id, 'model': model,
                        'revision_id': str(annotation.pk),
                        'interval_labels': interval_labels,
                        'group_interpretation': interpretation,
                        'author': annotation_payload.get('author', ''),
                        'reason': annotation_payload.get('reason', ''),
                    })
                if output is not None:
                    row.setdefault('model_predictions', []).append({
                        'job_id': job_id,
                        'model': model,
                        'prediction': output['prediction'],
                        'valid': output['valid'],
                        'semantics': semantics,
                    })
    return JsonResponse(payload)


@require_POST
def correct_dataset_label(request, study_id):
    study = get_object_or_404(Study.objects.select_related('dataset_revision'), pk=study_id)
    if not study.dataset_revision_id:
        raise Http404
    author = (request.user.get_username() if request.user.is_authenticated
              else request.POST.get('author', ''))
    try:
        revision = services.correct_dataset_label(
            study, study.dataset_revision,
            session_id=request.POST.get('session_id', ''),
            observation_id=request.POST.get('observation_id', ''),
            frame=int(request.POST.get('frame', '')),
            label=request.POST.get('label', ''),
            author=author,
            reason=request.POST.get('reason', ''),
        )
    except (ValueError, TypeError, KeyError) as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request, f'Corrección guardada como revisión de anotaciones {revision.pk}. '
            'Los datos fuente no cambiaron.')
    requested_prediction_ids = request.POST.getlist('predictions')
    try:
        requested_prediction_ids = list(dict.fromkeys(
            str(uuid.UUID(value)) for value in requested_prediction_ids))
    except (TypeError, ValueError, AttributeError):
        requested_prediction_ids = []
    if requested_prediction_ids:
        selected_runs = list(Job.objects.filter(
            pk__in=requested_prediction_ids, revision__study=study, status='completed'))
        _, selected_prediction_ids = _pose_prediction_run_options(
            study, study.dataset_revision, selected_runs, requested_prediction_ids)
    else:
        selected_prediction_ids = []
    target = reverse('page', args=(study.pk, 'data'))
    if selected_prediction_ids:
        target += '?' + urlencode({'predictions': selected_prediction_ids}, doseq=True)
    return redirect(target)


@require_POST
def prepare_video_preview(request, study_id, asset_id):
    study = get_object_or_404(Study.objects.select_related('dataset_revision'), pk=study_id)
    if not study.dataset_revision_id or asset_id not in study.dataset_revision.asset_ids:
        raise Http404
    asset = get_object_or_404(
        DatasetAsset, pk=asset_id, dataset_id=study.dataset_revision.dataset_id,
        role='video')
    try:
        job = services.enqueue_video_preview(study, study.dataset_revision, asset)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        if job.status == 'completed':
            messages.success(request, 'Vista compatible lista; se conserva el video original.')
        elif job.status in ('pending', 'running'):
            messages.info(request, f'Preparación de video en curso: {job.pk}.')
        else:
            messages.success(request, f'Preparación de video encolada: {job.pk}.')
    return redirect('page', study.pk, 'data')


def _parse_video_range(header, size):
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header.strip())
    if not match or size < 1:
        return None
    first, last = match.groups()
    if not first:
        suffix_length = int(last or 0)
        if suffix_length < 1:
            return None
        return max(0, size - suffix_length), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        return None
    return start, end


@require_POST
def upload_data(request, study_id):
    study = get_object_or_404(Study, pk=study_id)
    form = DatasetUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        errors = '; '.join(str(error) for values in form.errors.values() for error in values)
        messages.error(request, errors or 'Revisá los archivos y la configuración de carga.')
        return redirect('page', study.pk, 'data')

    files_by_role = {
        'pose': form.cleaned_data['pose_files'] or [],
        'roi': form.cleaned_data['roi_files'] or [],
        'video': form.cleaned_data['video_files'] or [],
        'labels': form.cleaned_data['label_files'] or [],
    }
    try:
        validate_uploads(form.cleaned_data['pose_adapter'], files_by_role)
        stored = [(role, store_upload(settings.WORKSPACE, uploaded))
                  for role, files in files_by_role.items() for uploaded in files]
    except (OSError, ValueError) as error:
        messages.error(request, str(error))
        return redirect('page', study.pk, 'data')

    with transaction.atomic():
        basis_revision = study.dataset_revision if study.dataset_revision_id else None
        dataset = (basis_revision.dataset if basis_revision
                   else Dataset.objects.create(name=study.name))
        for role, source in stored:
            DatasetAsset.objects.get_or_create(
                dataset=dataset, role=role, sha256=source['sha256'],
                defaults={
                    'original_name': source['original_name'],
                    'relative_path': source['relative_path'],
                    'size_bytes': source['size_bytes'],
                    'session_id': '' if role == 'roi' else source['session_id'],
                    'metadata': {},
                })
        previous = dataset.revisions.order_by('-number').first()
        assets = list(dataset.assets.order_by('pk'))
        from storm_studio.dataset_inventory import (
            default_asset_sessions, revision_asset_sessions,
        )

        config = dict(basis_revision.config) if basis_revision else {}
        asset_sessions = (revision_asset_sessions(basis_revision, assets)
                          if basis_revision else default_asset_sessions(assets))
        config.update({
            'fps': form.cleaned_data['fps'],
            'hdf_key': form.cleaned_data['hdf_key'].strip(),
            'csv_frame_base': _integer_or_default(
                form.cleaned_data.get('csv_frame_base'),
                default=_integer_or_default(config.get('csv_frame_base'), default=1)),
            'pose_frame_base': _integer_or_default(
                form.cleaned_data.get('pose_frame_base'),
                default=_integer_or_default(config.get('pose_frame_base'), default=0)),
            'label_frame_reference': (form.cleaned_data.get('label_frame_reference') or
                                      config.get('label_frame_reference', 'pose')),
            'asset_sessions': asset_sessions,
        })

        revision = DatasetRevision.objects.create(
            dataset=dataset,
            number=(previous.number + 1) if previous else 1,
            connector=form.cleaned_data['pose_adapter'],
            asset_ids=[asset.pk for asset in assets],
            config=config,
            status='registered',
            inventory={'assets': len(assets),
                       'roles': {role: sum(asset.role == role for asset in assets)
                                 for role in ('pose', 'roi', 'video', 'labels')},
                       'bytes': sum(asset.size_bytes for asset in assets)},
        )
        study.dataset_revision = revision
        study.save(update_fields=['dataset_revision'])

    messages.success(request, f'Datos registrados en la revisión {revision.number}.')
    return redirect('page', study.pk, 'data')


@require_POST
def save_asset_sessions(request, study_id):
    study = get_object_or_404(Study, pk=study_id)
    if not study.dataset_revision_id:
        messages.error(request, 'Registrá fuentes antes de asociarlas con sesiones.')
        return redirect('page', study.pk, 'data')
    current = study.dataset_revision
    assets_by_id = {asset.pk: asset for asset in DatasetAsset.objects.filter(
        dataset=current.dataset, pk__in=current.asset_ids)}
    assets = [assets_by_id[asset_id] for asset_id in current.asset_ids
              if asset_id in assets_by_id]
    from storm_studio.dataset_inventory import revision_asset_sessions

    initial = revision_asset_sessions(current, assets)
    initial_video_offsets = current.config.get('video_frame_offsets') or {}
    if not isinstance(initial_video_offsets, dict):
        initial_video_offsets = {}
    pose_sessions = {
        str(asset.pk): initial[str(asset.pk)]
        for asset in assets if asset.role == 'pose'
    }
    form = AssetSessionForm(
        request.POST, assets=assets, pose_sessions=pose_sessions, initial=initial,
        video_frame_offsets=initial_video_offsets)
    if not any(asset.role != 'pose' for asset in assets):
        messages.error(request, 'Esta revisión no tiene videos, ROIs o anotaciones para vincular.')
        return redirect('page', study.pk, 'data')
    if not form.is_valid():
        messages.error(request, '; '.join(str(error) for error in form.errors.values()))
        return redirect('page', study.pk, 'data')
    asset_sessions = form.cleaned_data['asset_sessions']
    video_frame_offsets = form.cleaned_data['video_frame_offsets']
    if (asset_sessions == initial and
            video_frame_offsets == {
                str(asset.pk): _integer_or_default(
                    initial_video_offsets.get(str(asset.pk), 0), default=0)
                for asset in assets if asset.role == 'video'
            }):
        messages.info(request, 'Los vínculos con las sesiones no cambiaron; no se creó otra revisión.')
        return redirect('page', study.pk, 'data')

    config = dict(current.config)
    config['asset_sessions'] = asset_sessions
    config['video_frame_offsets'] = video_frame_offsets
    inventory = {key: current.inventory[key] for key in ('assets', 'roles', 'bytes')
                 if key in current.inventory}
    latest_number = current.dataset.revisions.order_by('-number').values_list(
        'number', flat=True).first() or 0
    with transaction.atomic():
        revision = DatasetRevision.objects.create(
            dataset=current.dataset,
            number=latest_number + 1,
            connector=current.connector,
            asset_ids=current.asset_ids,
            config=config,
            status='registered',
            inventory=inventory,
            artifact_ref=None,
        )
        study.dataset_revision = revision
        study.save(update_fields=['dataset_revision'])
    messages.success(
        request,
        f'Vínculos guardados como revisión {revision.number}. Inspeccioná el inventario para actualizarlos.')
    return redirect('page', study.pk, 'data')


@require_POST
def inventory_data(request, study_id):
    study = get_object_or_404(Study, pk=study_id)
    if not study.dataset_revision_id:
        messages.error(request, 'Registrá las fuentes antes de ejecutar el inventario.')
        return redirect('page', study.pk, 'data')
    try:
        job = services.enqueue_inventory(study, study.dataset_revision)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f'Inventario encolado como trabajo {job.pk}. El worker lo ejecutará en segundo plano.')
    return redirect('page', study.pk, 'data')


@require_POST
def review_video_timeline(request, study_id):
    study = get_object_or_404(Study, pk=study_id)
    if not study.dataset_revision_id:
        messages.error(request, 'Registrá las fuentes antes de revisar la continuidad del video.')
        return redirect('page', study.pk, 'data')
    if request.POST.get('confirm_review') != 'on':
        messages.error(request, 'Confirmá que revisaste la continuidad antes de guardar la decisión.')
        return redirect('page', study.pk, 'data')
    from storm_studio.video_timeline_reviews import record_review

    reviewer = (request.user.get_username() if request.user.is_authenticated
                else request.POST.get('reviewer', ''))
    try:
        decision = record_review(
            study, study.dataset_revision, reviewer=reviewer,
            reason=request.POST.get('reason', ''))
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(
            request,
            f'Continuidad revisada por {decision.payload["reviewer"]}; '
            'la decisión quedó guardada en el historial del estudio.')
    return redirect('page', study.pk, 'data')


@require_POST
def save_video_discontinuities(request, study_id):
    study = get_object_or_404(Study, pk=study_id)
    current = study.dataset_revision if study.dataset_revision_id else None
    if not current or current.status != 'ready':
        messages.error(request, 'Inspeccioná los datos antes de editar sus discontinuidades.')
        return redirect('page', study.pk, 'data')
    rows = _video_discontinuity_override_rows(current)
    if not rows:
        messages.error(request, 'No hay sesiones de video vinculadas para editar.')
        return redirect('page', study.pk, 'data')
    reviewer = (request.user.get_username() if request.user.is_authenticated
                else request.POST.get('reviewer', ''))
    reviewer = str(reviewer or '').strip()
    reason = str(request.POST.get('reason', '') or '').strip()
    if not reviewer or len(reviewer) > 160:
        messages.error(request, 'Indicá quién definió los límites (hasta 160 caracteres).')
        return redirect('page', study.pk, 'data')
    if not reason or len(reason) > 2000:
        messages.error(request, 'Escribí el motivo del cambio (hasta 2.000 caracteres).')
        return redirect('page', study.pk, 'data')

    overrides = {}
    for index, row in enumerate(rows):
        raw = str(request.POST.get(f'boundaries_{index}', '') or '').strip()
        if not raw:
            boundaries = []
        else:
            try:
                boundaries = [int(value) for value in re.split(r'[,;\s]+', raw)]
            except ValueError:
                messages.error(
                    request,
                    f'Los límites de {row["session_id"]} deben ser números de frame enteros.')
                return redirect('page', study.pk, 'data')
        if any(value <= 0 for value in boundaries):
            messages.error(request, 'Un límite debe estar antes de un frame mayor que 0.')
            return redirect('page', study.pk, 'data')
        if row['frame_count'] and any(value >= row['frame_count'] for value in boundaries):
            messages.error(
                request,
                f'Los límites de {row["session_id"]} deben ser menores que '
                f'{row["frame_count"]}, el total de frames del video.')
            return redirect('page', study.pk, 'data')
        overrides[row['session_id']] = sorted(set(boundaries))

    config = dict(current.config)
    config['video_discontinuity_overrides_by_session'] = overrides
    inventory = {key: current.inventory[key] for key in ('assets', 'roles', 'bytes')
                 if key in current.inventory}
    previous = current.dataset.revisions.order_by('-number').first()
    with transaction.atomic():
        revision = DatasetRevision.objects.create(
            dataset=current.dataset,
            number=(previous.number + 1) if previous else current.number + 1,
            connector=current.connector,
            asset_ids=current.asset_ids,
            config=config,
            status='registered',
            inventory=inventory,
            artifact_ref=None,
        )
        study.dataset_revision = revision
        study.save(update_fields=['dataset_revision'])
        previous_edit = study.revision_set.filter(
            kind='video_discontinuity_edit').order_by('-pk').first()
        Revision.objects.create(
            study=study,
            kind='video_discontinuity_edit',
            parent=previous_edit,
            payload={
                'previous_dataset_revision_id': current.pk,
                'dataset_revision_id': revision.pk,
                'dataset_id': current.dataset_id,
                'source_fingerprint': current.inventory.get('source_fingerprint'),
                'video_timeline': current.inventory.get('video_timeline', []),
                'video_discontinuity_overrides_by_session': overrides,
                'author': reviewer,
                'reason': reason,
            },
        )
        job = services.enqueue_inventory(study, revision)
    messages.success(
        request,
        f'Límites guardados como revisión {revision.number}; '
        f'se está actualizando el inventario ({job.pk}). La revisión anterior se conserva.')
    return redirect('page', study.pk, 'data')


@require_POST
def save_session_partitions(request, study_id):
    study = get_object_or_404(Study, pk=study_id)
    if not study.dataset_revision_id:
        messages.error(request, 'Registrá e inspeccioná los datos antes de particionar sesiones.')
        return redirect('page', study.pk, 'data')
    current = study.dataset_revision
    sessions = [item['session_id'] for item in current.inventory.get('sessions', [])]
    if current.status != 'ready' or not sessions:
        messages.error(request, 'El inventario debe estar listo y contener sesiones.')
        return redirect('page', study.pk, 'data')
    initial = current.config.get('session_partitions', {})
    initial_ranges = current.config.get('reserved_evaluation_ranges', {})
    if not isinstance(initial_ranges, dict):
        initial_ranges = {}
    form = SessionPartitionForm(request.POST, sessions=sessions, initial=initial,
                                reserved_ranges=initial_ranges)
    if not form.is_valid():
        messages.error(request, '; '.join(str(error) for error in form.errors.values()))
        return redirect('page', study.pk, 'data')

    config = dict(current.config)
    session_partitions = form.cleaned_data['session_partitions']
    reserved_ranges = form.cleaned_data['reserved_evaluation_ranges']
    session_records = {item['session_id']: item
                       for item in current.inventory.get('sessions', [])}
    for session_id, interval in reserved_ranges.items():
        record = session_records.get(session_id, {})
        first_frame, last_frame = record.get('first_frame'), record.get('last_frame')
        if ((first_frame is not None and interval['start'] < first_frame) or
                (last_frame is not None and interval['stop'] > last_frame + 1)):
            messages.error(
                request,
                f'El rango reservado de {session_id} debe quedar dentro de '
                'los frames registrados de esa sesión.')
            return redirect('page', study.pk, 'data')
    if session_partitions == initial and reserved_ranges == initial_ranges:
        messages.info(request, 'Las particiones y reservas no cambiaron; no se creó otra revisión.')
        return redirect('page', study.pk, 'data')
    config['session_partitions'] = session_partitions
    config['reserved_evaluation_ranges'] = reserved_ranges
    inventory = dict(current.inventory)
    inventory['sessions'] = [
        dict(item, partition=session_partitions[item['session_id']],
             reserved_evaluation_range=reserved_ranges.get(item['session_id']))
        for item in inventory.get('sessions', [])]
    preview = []
    for row in inventory.get('preview', []):
        interval = reserved_ranges.get(row.get('session_id'))
        frame = row.get('frame')
        is_reserved = bool(interval and isinstance(frame, int) and
                           interval['start'] <= frame < interval['stop'])
        preview.append(dict(
            row,
            partition=('test' if is_reserved else
                       session_partitions.get(row.get('session_id'), 'train')),
            reserved_evaluation=is_reserved))
    inventory['preview'] = preview
    inventory['partition_counts'] = {
        partition: sum(item.get('frames', 0) for item in inventory['sessions']
                       if item['partition'] == partition)
        for partition in ('train', 'validation', 'test')
    }
    warnings = list(inventory.get('warnings', []))
    warnings.append('Las particiones o reservas cambiaron; inspeccioná esta revisión para actualizar la vista previa.')
    inventory['warnings'] = warnings
    identity = {
        'connector': current.connector,
        'config': config,
        'assets': [{'role': asset.role, 'sha256': asset.sha256,
                    'session_id': asset.session_id}
                   for asset in current.dataset.assets.filter(pk__in=current.asset_ids)],
    }
    from storm.config import fingerprint

    inventory['source_fingerprint'] = fingerprint(identity)
    previous = current.dataset.revisions.order_by('-number').first()
    with transaction.atomic():
        revision = DatasetRevision.objects.create(
            dataset=current.dataset,
            number=(previous.number + 1) if previous else current.number + 1,
            connector=current.connector,
            asset_ids=current.asset_ids,
            config=config,
            status='registered',
            inventory=inventory,
            artifact_ref=None,
        )
        study.dataset_revision = revision
        study.save(update_fields=['dataset_revision'])
    messages.success(request, f'Particiones y reservas guardadas como revisión {revision.number}. Volvé a inspeccionar el contenido para actualizar su vista previa.')
    return redirect('page', study.pk, 'data')


@require_POST
def snapshot(request, study_id):
    study = get_object_or_404(Study, pk=study_id)
    section = request.POST.get('section', 'history')
    if section not in dict(PAGES):
        return HttpResponse(status=400)
    latest = study.revision_set.filter(kind='plan').order_by('-pk').first()
    try:
        visual_state = json.loads(request.POST.get('state', '{}'))
    except (TypeError, json.JSONDecodeError) as error:
        return HttpResponse(f'Invalid visual state: {error}', status=400, content_type='text/plain')
    if not isinstance(visual_state, dict):
        return HttpResponse('Visual state must be an object', status=400, content_type='text/plain')
    Revision.objects.create(study=study, kind='snapshot', payload={
        'section': section, 'name': request.POST.get('name', '')[:160],
        'plan_revision': latest.pk if latest else None,
        'adopted_job': str(study.adopted_id) if study.adopted_id else None,
        'visual_state': visual_state})
    return redirect('page', study.pk, 'history')


@require_POST
def import_data(request, study_id):
    study = get_object_or_404(Study, pk=study_id)
    previous = study.revision_set.filter(kind='plan').order_by('-pk').first()
    current = previous.payload if previous else {}
    uploaded = request.FILES.get('dataset')
    if uploaded is None or uploaded.size > 2_000_000:
        return HttpResponse('Provide a JSON file smaller than 2 MB.', status=400)
    try:
        from storm.suite import validate_data
        data = json.loads(uploaded.read())
        validate_data(data, numeric=current.get('connector', 'numeric_json') == 'numeric_json',
                      require_train=current.get('operation', 'train') != 'infer')
    except (ValueError, TypeError, KeyError) as error:
        return HttpResponse(str(error), status=400, content_type='text/plain')
    spec = dict(previous.payload) if previous else {'model': 'identity', 'config': {}, 'steps': [], 'seed': 42}
    spec['data'] = data
    Revision.objects.create(study=study, kind='plan', parent=previous, payload=spec)
    return redirect('page', study.pk, 'data')


@require_POST
def assistant(request, study_id):
    study = get_object_or_404(Study, pk=study_id)
    from storm.learning import LocalAssistant
    proposal = LocalAssistant().propose({'study': study.pk, 'section': 'flow'})
    Revision.objects.create(study=study, kind='proposal', payload=proposal)
    messages.info(request, proposal['text'])
    return redirect('page', study.pk, 'history')


def scaffold(request):
    kind = request.GET.get('kind')
    if kind:
        allowed = {'connector', 'step', 'model', 'metric', 'visualizer'}
        if kind not in allowed:
            return HttpResponse('Unknown extension type', status=400)
        name = re.sub(r'[^a-zA-Z0-9_]+', '_', request.GET.get('name', 'my_extension')).strip('_').lower()
        if not name or not name[0].isalpha():
            return HttpResponse('Extension name must start with a letter', status=400)
        methods = {
            'connector': ('load',), 'step': ('fit', 'transform'), 'model': ('fit', 'predict'),
            'metric': ('evaluate',), 'visualizer': ('render',),
        }[kind]
        safe_name = name
        class_name = ''.join(part.title() for part in safe_name.split('_'))
        source = f'''"""External STORM {kind} extension: {safe_name}."""

class {class_name}:
    """Generated placeholder. Implement and validate before enabling."""
    def __init__(self, config=None):
        self.config = config or {{}}
'''
        for method in methods:
            source += f'\n    def {method}(self, *args, **kwargs):\n        raise NotImplementedError("Implement {method} before enabling this extension")\n'
        descriptor = {'name': safe_name, 'kind': kind, 'status': 'pending_implementation',
                      'version': '0.1.0', 'license': 'MIT', 'entrypoint': f'implementation:{class_name}',
                      'required_methods': list(methods)}
        test_source = f'''import json
from pathlib import Path

ROOT = Path(__file__).parent

def test_descriptor_is_explicitly_pending_and_declares_contract():
    descriptor = json.loads((ROOT / "descriptor.json").read_text())
    assert descriptor["status"] == "pending_implementation"
    assert descriptor["kind"] == {kind!r}
    assert descriptor["required_methods"] == {list(methods)!r}

def test_placeholder_has_not_been_mistaken_for_enabled_code():
    source = (ROOT / "implementation.py").read_text()
    assert "raise NotImplementedError" not in source, "Implement the generated methods before enabling this plugin"
'''
        readme = f'''# {safe_name}\n\nGenerated STORM {kind} extension.\n\nStatus: pending implementation. This package is not loaded by Studio and is never executed by the web server.\n\n1. Implement every required method in `implementation.py`.\n2. Run `python -m pytest -q`.\n3. Add the package to the external plugin environment only after its implementation and project-specific behavior tests pass.\n\nThe descriptor records its entry point, version, contract and MIT license declaration.\n'''
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as package:
            root = f'storm_{safe_name}/'
            package.writestr(root + '__init__.py', '')
            package.writestr(root + 'implementation.py', source)
            package.writestr(root + 'descriptor.json', json.dumps(descriptor, indent=2))
            package.writestr(root + 'test_conformance.py', test_source)
            package.writestr(root + 'README.md', readme)
            package.writestr(root + 'LICENSE', 'MIT License\n\nCopyright (c) STORM extension author\n')
        response = HttpResponse(buffer.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="storm_{safe_name}.zip"'
        return response
    source = '''# STORM plugin skeleton: implement the model before enabling the package.
from storm.suite import Component
from storm.models import ModelOutput

class MyModel:
    def __init__(self, config):
        self.config = config

    def fit(self, inputs, targets=None):
        raise NotImplementedError("Implement training or declare inference-only capabilities")

    def predict(self, inputs):
        raise NotImplementedError("Return ModelOutput with aligned predictions")

def register(catalog):
    catalog.register(Component("my.model", MyModel, ("train", "infer"),
        {"type": "object", "properties": {}}))
'''
    response = HttpResponse(source, content_type='text/plain')
    response['Content-Disposition'] = 'attachment; filename="storm_plugin.py"'
    return response


def visualization(request, job_id, visualizer):
    from storm.artifacts import FileArtifactStore, ArtifactRef
    from storm.visualization import VisualizationManager, VisualizationRequest, VisualizationSpec
    job = get_object_or_404(Job, pk=job_id, status='completed')
    try:
        output = FileArtifactStore(settings.ARTIFACT_ROOT).load(ArtifactRef.from_dict(job.result['output_ref']))
        resolved_data = job.result['resolved_data']
        indices = job.result['indices']
        visual_metadata = {'execution_id': str(job.pk), 'indices': indices}
        for key in ('frames', 'sessions', 'segments', 'partitions'):
            rows = resolved_data.get(key)
            if isinstance(rows, list) and len(rows) == len(resolved_data['inputs']):
                visual_metadata[key] = [rows[index] for index in indices]
        result = VisualizationManager(services.catalog().visualizations).render(
            VisualizationSpec(visualizer), VisualizationRequest(
                data=resolved_data['inputs'], output=output, metrics=job.result['metrics'],
                metadata=visual_metadata))
        if result.media_type not in ('image/svg+xml', 'image/png', 'text/plain'):
            raise ValueError('This browser endpoint supports SVG, PNG or text renderers')
        response = HttpResponse(result.content, content_type=result.media_type)
        response['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'"
        return response
    except (ValueError, TypeError, KeyError, OSError) as error:
        return HttpResponse(str(error), status=400, content_type='text/plain')
