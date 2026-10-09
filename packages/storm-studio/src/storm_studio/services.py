from copy import deepcopy
from datetime import datetime
import importlib
import json
import logging
import math
import random

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from storm.suite import default_catalog, execute, infer
from storm_studio.models import DatasetRevision, Job, Revision

logger = logging.getLogger(__name__)
MAX_INLINE_RESOLVED_DATA_OBSERVATIONS = 100_000
MAX_JOB_LOG_ENTRIES = 100
MAX_JOB_LOG_MESSAGE_CHARS = 2_000


def append_job_logs(job_id, messages):
    """Persist recent worker messages without letting a job's log grow unbounded."""
    if isinstance(messages, str):
        messages = [messages]
    additions = []
    timestamp = timezone.now().isoformat()
    for message in messages:
        message = str(message).rstrip()
        if not message.strip():
            continue
        if len(message) > MAX_JOB_LOG_MESSAGE_CHARS:
            message = message[:MAX_JOB_LOG_MESSAGE_CHARS - 1] + '…'
        additions.append({'timestamp': timestamp, 'message': message})
    if not additions:
        return
    logs = Job.objects.filter(pk=job_id).values_list('logs', flat=True).first()
    if not isinstance(logs, list):
        logs = []
    Job.objects.filter(pk=job_id).update(
        logs=(logs + additions)[-MAX_JOB_LOG_ENTRIES:])


def catalog():
    result = default_catalog()
    for module in settings.STORM_PLUGINS:
        importlib.import_module(module).register(result)
    return result


def active_plan(study):
    """Keep execution variants out of the editable plan, including legacy branches."""
    for revision in study.revision_set.filter(kind='plan').select_related('parent').order_by('-pk'):
        if revision.payload.get('execution_variant'):
            continue
        parent = revision.parent
        if ('execution_variant' not in revision.payload and parent and revision.payload.get('model') != parent.payload.get('model')
                and revision.payload.get('model') in parent.payload.get('branch_models', [])
                and revision.job_set.exists()):
            continue
        return revision
    return None


def execution_specs(revision):
    from storm.plans import branch_specs
    result = branch_specs(revision.payload)
    for _, spec in result:
        recipe_id = spec.get('preparation_revision_id')
        if recipe_id:
            recipe = Revision.objects.filter(pk=recipe_id, study=revision.study,
                                             kind='preparation').first()
            if recipe is None:
                raise ValueError('Preparation must belong to this study')
            source_id = recipe.payload.get('dataset_revision_id')
            if spec.get('dataset_revision_id') and int(spec['dataset_revision_id']) != source_id:
                raise ValueError('Preparation belongs to another dataset revision')
            spec.update(dataset_revision_id=source_id, steps=deepcopy(recipe.payload.get('steps', [])), data={})
        source_id = spec.get('dataset_revision_id')
        if source_id:
            source = DatasetRevision.objects.filter(pk=source_id, status='ready').first()
            if source is None:
                raise ValueError('Select a ready registered dataset revision')
            if source.config.get('inference_only') and spec.get('operation', 'train') != 'infer':
                raise ValueError('This inference-only benchmark blocks training and updates.')
            spec.update(connector=source.connector, data={})
    return result


def validate_executions(executions, worker_catalog):
    from dataclasses import replace
    from storm.contracts import validate_plan, PlanValidationError
    problems = [replace(problem, branch=branch)
                for branch, spec in executions
                for problem in validate_plan(spec, worker_catalog, plan_data_summary(spec, worker_catalog))]
    if any(problem.severity == 'error' for problem in problems):
        raise PlanValidationError(problems)
    return problems


def plan_data_summary(payload, worker_catalog):
    from storm.plans import branch_specs
    try:
        branches = branch_specs(payload)
    except ValueError:
        return None
    summaries = {name: _single_data_summary(spec, worker_catalog) or {}
                 for name, spec in branches}
    summary = summaries.pop('root', {})
    if summaries:
        summary['branches'] = summaries
    return summary or None


def _single_data_summary(payload, worker_catalog):
    """Inspect metadata only; never load the dataset artifact during preflight."""
    dataset_id = payload.get('dataset_revision_id')
    if not dataset_id:
        return None
    dataset = DatasetRevision.objects.filter(pk=dataset_id).first()
    if dataset is None:
        return None
    summary = dict(dataset.inventory)
    preview = summary.get('preview') or []
    if preview:
        from storm.contracts import _shape
        summary['shape'] = _shape(preview[0].get('features'))
    if dataset.connector == 'prepared_artifact':
        summary['full_sessions'] = False
        recipe = Revision.objects.filter(
            pk=dataset.config.get('preparation_revision_id'), kind='preparation').first()
        source = DatasetRevision.objects.filter(
            pk=dataset.config.get('source_dataset_revision_id')).first()
        if (recipe is not None and source is not None and source.artifact_ref
                and recipe.payload.get('dataset_revision_id') == source.pk
                and source.dataset_id == dataset.dataset_id):
            from storm.config import fingerprint
            from storm_studio.data_preparation import _step_versions
            resolver = worker_catalog.preparation_resolver
            steps = recipe.payload.get('steps', [])
            if resolver:
                steps = resolver(steps, source.inventory.get('feature_names', []))
            versions = _step_versions(steps, worker_catalog)
            identity = fingerprint({'source': source.artifact_ref, 'steps': steps,
                                    'step_versions': versions})
            if (identity == dataset.config.get('preparation_fingerprint')
                    and identity == dataset.inventory.get('source_fingerprint')
                    and versions == dataset.config.get('step_versions')):
                summary['preparation'] = {'validated': True, 'fingerprint': identity,
                                          'resolved_steps': steps}
    return summary


def submit(revision, previous=None, operation=None, source=None):
    if revision.kind != 'plan':
        raise ValueError('Only plan revisions can execute')
    if operation is None:
        operation = ('infer' if revision.payload.get('operation') == 'infer' else 'train')
    if operation not in ('train', 'infer', 'apply', 'update', 'resume'):
        raise ValueError('Unknown operation')
    if revision.payload.get('label_correction_revision_id') and operation != 'train':
        raise ValueError('Label corrections can only be included in a new training plan.')
    worker_catalog = catalog()
    problems = validate_executions(execution_specs(revision), worker_catalog)
    dataset_revision_id = revision.payload.get('dataset_revision_id')
    if revision.payload.get('label_correction_revision_id') and dataset_revision_id is None:
        raise ValueError('Label corrections require a registered dataset source.')
    if dataset_revision_id is not None:
        dataset_revision = DatasetRevision.objects.filter(pk=dataset_revision_id).first()
        if dataset_revision is not None:
            if (dataset_revision.config.get('inference_only') is True
                    and operation in {'train', 'update', 'resume'}):
                raise ValueError(
                    'This inference-only benchmark blocks training and updates.')
            if (dataset_revision.config.get('inference_only') is True
                    and operation == 'infer'):
                try:
                    descriptor = catalog().get(revision.payload.get('model', ''))
                except KeyError as error:
                    raise ValueError('Select a registered model before running inference.') from error
                if {'train', 'group'} & set(descriptor.capabilities):
                    raise ValueError(
                        'This inference-only benchmark requires a completed model; '
                        'apply a saved run from Modelos.')
            from storm_studio.video_timeline_reviews import require_review

            require_review(revision.study, dataset_revision)
    if operation in ('apply', 'update', 'resume'):
        if source is None or source.revision.study_id != revision.study_id:
            raise ValueError('Source must belong to the same study')
        if operation in ('apply', 'update') and source.status != 'completed':
            raise ValueError('Only a completed model can be reused')
        if operation == 'apply':
            if 'infer' not in source.result.get('capabilities', []):
                raise ValueError('The saved model does not support inference')
            if not source.result.get('model_ref'):
                raise ValueError('The source execution has no recoverable model artifact')
            if (revision.payload.get('operation') != 'infer'
                    or revision.payload.get('model') != source.result.get('model')):
                raise ValueError('The inference plan must reuse the selected model')
        if operation == 'resume' and source.status in ('pending', 'running'):
            raise ValueError('Stop the source execution before resuming its checkpoint')
    return Job.objects.create(revision=revision, previous=previous, source=source, operation=operation,
                              result={'validation_problems': [p.to_dict() for p in problems]})


@transaction.atomic
def enqueue_inventory(study, dataset_revision):
    if study.dataset_revision_id != dataset_revision.pk:
        raise ValueError('The dataset revision must be active in this study')
    if Job.objects.filter(
            operation='inventory', status__in=('pending', 'running'),
            revision__payload__dataset_revision=dataset_revision.pk).exists():
        raise ValueError('An inventory job is already active for this dataset revision')
    revision = Revision.objects.create(
        study=study, kind='dataset_inventory',
        payload={'dataset_revision': dataset_revision.pk,
                 'dataset': dataset_revision.dataset_id,
                 'number': dataset_revision.number,
                 'connector': dataset_revision.connector,
                 'asset_ids': dataset_revision.asset_ids,
                 'config': dataset_revision.config})
    job = Job.objects.create(revision=revision, operation='inventory')
    inventory = dict(dataset_revision.inventory)
    inventory.pop('error', None)
    DatasetRevision.objects.filter(pk=dataset_revision.pk).update(
        status='queued', inventory=inventory)
    return job


@transaction.atomic
def enqueue_video_preview(study, dataset_revision, asset):
    if study.dataset_revision_id != dataset_revision.pk:
        raise ValueError('La revisión de datos debe ser la activa en este estudio.')
    if (dataset_revision.status != 'ready' or asset.dataset_id != dataset_revision.dataset_id
            or asset.role != 'video' or asset.pk not in dataset_revision.asset_ids):
        raise ValueError('Seleccioná un video de la revisión de datos lista.')

    from storm_studio.video_previews import latest_video_preview_job, preview_path

    previous = latest_video_preview_job(study.pk, dataset_revision.pk, asset.pk)
    if previous and previous.status in ('pending', 'running'):
        return previous
    if previous and preview_path(previous, settings.WORKSPACE):
        return previous
    revision = Revision.objects.create(
        study=study, kind='video_preview',
        parent=previous.revision if previous else None,
        payload={'dataset_revision': dataset_revision.pk,
                 'asset_id': asset.pk, 'source_sha256': asset.sha256})
    return Job.objects.create(
        revision=revision, previous=previous, operation='video_preview')


@transaction.atomic
def enqueue_preparation(study, recipe):
    if recipe.kind != 'preparation' or recipe.study_id != study.pk:
        raise ValueError('The preparation recipe must belong to this study.')
    source = DatasetRevision.objects.get(pk=recipe.payload['dataset_revision_id'])
    if source.status != 'ready' or not source.artifact_ref:
        raise ValueError('Inspect the source dataset before processing it.')
    if source.config.get('inference_only') is True:
        raise ValueError('This inference-only benchmark cannot be reprocessed.')
    from storm_studio.video_timeline_reviews import require_review

    require_review(study, source)
    if study.dataset_revision_id and source.dataset_id != study.dataset_revision.dataset_id:
        raise ValueError('The recipe source does not belong to this study dataset.')
    if Job.objects.filter(
            revision=recipe, operation='prepare', status__in=('pending', 'running')).exists():
        raise ValueError('This preparation recipe already has an active job.')
    return Job.objects.create(revision=recipe, operation='prepare')


def _record_progress(job_id, update):
    """Persist current progress, a bounded event trace, and an ETA when units are known."""
    now = timezone.now()
    job = Job.objects.only('progress').get(pk=job_id)
    previous = job.progress if isinstance(job.progress, dict) else {}
    progress = {**previous, **update, 'schema_version': 1,
                'execution_id': str(job_id), 'updated_at': now.isoformat()}
    if ('batch_step' not in update and (previous.get('phase') != progress.get('phase')
            or previous.get('label') != progress.get('label')
            or update.get('status') in {'started', 'completed', 'failed'})):
        for key in ('batch_step', 'batch_total', 'throughput', 'batch_eta_seconds',
                    'processed_observations', 'total_observations', 'loss'):
            progress.pop(key, None)
    for key in ('component', 'component_version', 'operation', 'span_id',
                'status', 'duration_seconds', 'error_type', 'error_message'):
        if key not in update:
            progress.pop(key, None)
    phase_changed = previous.get('phase') != progress.get('phase')
    stage_changed = previous.get('stage_index') != progress.get('stage_index')
    label_changed = previous.get('label') != progress.get('label')
    units_changed = any(previous.get(key) != progress.get(key) for key in (
        'phase_step', 'phase_total', 'unit_label', 'batch_step', 'epoch', 'status'))
    trace = previous.get('trace') if isinstance(previous.get('trace'), list) else []
    stage_trace = (previous.get('stage_trace')
                   if isinstance(previous.get('stage_trace'), list) else [])
    if phase_changed or stage_changed or label_changed or units_changed:
        label = str(progress.get('label') or progress.get('phase') or 'Avance actualizado')
        if len(label) > MAX_JOB_LOG_MESSAGE_CHARS:
            label = label[:MAX_JOB_LOG_MESSAGE_CHARS - 1] + '…'
        stage, stages = progress.get('stage_index'), progress.get('stage_total')
        step, total = progress.get('phase_step'), progress.get('phase_total')
        unit = progress.get('unit_label') or 'unidades'
        message = f'Etapa {stage} de {stages}: {label}' if stage and stages else label
        if step is not None and total is not None:
            message += f' · avance {step}/{total} {unit}'
        if len(message) > MAX_JOB_LOG_MESSAGE_CHARS:
            message = message[:MAX_JOB_LOG_MESSAGE_CHARS - 1] + '…'
        event = {
            'timestamp': now.isoformat(), 'phase': progress.get('phase'),
            'stage_index': progress.get('stage_index'),
            'stage_total': progress.get('stage_total'),
            'phase_step': step, 'phase_total': total, 'unit_label': progress.get('unit_label'),
            'message': message,
        }
        event.update({key: progress[key] for key in ('schema_version', 'component', 'component_version', 'operation', 'status', 'span_id', 'duration_seconds', 'error_type', 'error_message', 'failure_kind', 'rnn_backend', 'batch_step', 'batch_total', 'epoch', 'device', 'throughput') if key in progress})
        if progress.get('batch_total'):
            event['message'] += f" · lote {progress.get('batch_step', 0)}/{progress['batch_total']}"
        trace.append(event)
        progress['trace'] = trace[-100:]
        if phase_changed or stage_changed:
            stage_trace.append(event)
            progress['stage_trace'] = stage_trace[-20:]
    if phase_changed:
        progress['phase_started_at'] = now.isoformat()
        progress['phase_started_step'] = progress.get('phase_step') or 0
        progress['eta_seconds'] = None
    else:
        progress['phase_started_at'] = previous.get('phase_started_at', now.isoformat())
        progress['phase_started_step'] = previous.get(
            'phase_started_step', progress.get('phase_step') or 0)

    step = progress.get('phase_step')
    total = progress.get('phase_total')
    if type(step) is int and type(total) is int and total > 0:
        progress['fraction'] = min(1.0, max(0.0, step / total))
        try:
            phase_started = datetime.fromisoformat(progress['phase_started_at'])
            elapsed = max(0.0, (now - phase_started).total_seconds())
            completed_since_start = step - progress['phase_started_step']
            if completed_since_start > 0:
                seconds_per_unit = elapsed / completed_since_start
                progress['eta_seconds'] = max(
                    0, math.ceil(seconds_per_unit * max(0, total - step)))
        except (TypeError, ValueError):
            pass
    else:
        progress.pop('fraction', None)
        progress.pop('eta_seconds', None)
    Job.objects.filter(pk=job_id, status='running').update(progress=progress)


def load_execution_result(job):
    """Load the full result when a large result keeps its data in artifacts."""
    result = job.result if isinstance(job.result, dict) else {}
    artifact_ref = result.get('result_artifact_ref')
    if not artifact_ref:
        return result
    from storm.artifacts import ArtifactRef, FileArtifactStore

    return FileArtifactStore(settings.ARTIFACT_ROOT).load(
        ArtifactRef.from_dict(artifact_ref))


def _result_for_database(job_id, result):
    data = result.get('resolved_data') if isinstance(result, dict) else None
    inputs = data.get('inputs') if isinstance(data, dict) else None
    if not isinstance(inputs, list) or len(inputs) <= MAX_INLINE_RESOLVED_DATA_OBSERVATIONS:
        return result
    from storm.artifacts import FileArtifactStore

    try:
        artifact_ref = FileArtifactStore(settings.ARTIFACT_ROOT).resolve(
            kind='results', artifact_id=str(job_id))
    except (FileNotFoundError, KeyError):
        return result
    targets = data.get('targets')
    metric_indices = result.get('metric_indices', result.get('indices', []))
    targets_available = bool(metric_indices) and isinstance(targets, list) and all(
        type(index) is int and 0 <= index < len(targets) and targets[index] is not None
        for index in metric_indices)
    compact = {key: value for key, value in result.items() if key != 'resolved_data'}
    compact['resolved_data_summary'] = {
        'observation_count': len(inputs),
        'taxonomy': data.get('taxonomy'),
        'targets_available_for_metrics': targets_available,
    }
    compact['result_artifact_ref'] = artifact_ref.to_dict()
    return compact


def perform(job_id):
    started = timezone.now()
    if not Job.objects.filter(pk=job_id, status='pending').update(
            status='running', started=started):
        return
    job = Job.objects.select_related('revision').get(pk=job_id)
    _record_progress(job_id, {
        'phase': 'starting', 'label': 'Iniciando en el worker',
        'stage_index': 1,
        'stage_total': 4 if job.operation in ('infer', 'apply') else 5,
    })
    try:
        if job.operation == 'experiment_preview':
            from storm_studio.experiments import perform_preview
            result = perform_preview(job)
        elif job.operation == 'experiment_evidence':
            from storm_studio.context_evidence import recover_evidence
            source = Job.objects.get(pk=job.revision.payload['job'], revision__study=job.revision.study)
            result = {'evidence_ref': recover_evidence(source)}
        elif job.operation == 'experiment_test':
            from storm_studio.experiments import perform_reserved
            result = perform_reserved(job)
        elif job.operation == 'experiment':
            from storm_studio.experiments import perform_experiment
            result = perform_experiment(job)
        elif job.operation == 'inventory':
            from storm_studio.dataset_inventory import inspect_dataset

            _record_progress(job_id, {
                'phase': 'inventory', 'label': 'Inspeccionando archivos y sesiones',
                'stage_index': 2, 'stage_total': 4,
            })
            result = inspect_dataset(
                job.revision.payload['dataset_revision'], workspace=settings.WORKSPACE,
                artifact_root=settings.ARTIFACT_ROOT, catalog=catalog(),
                progress_callback=lambda update: _record_progress(job_id, update))
        elif job.operation == 'prepare':
            from storm_studio.data_preparation import materialize_preparation

            _record_progress(job_id, {
                'phase': 'preparing', 'label': 'Aplicando la receta de procesamiento',
                'stage_index': 2, 'stage_total': 4,
            })
            result = materialize_preparation(
                job.revision_id, str(job.pk), artifact_root=settings.ARTIFACT_ROOT,
                catalog=catalog(), progress_callback=lambda update: _record_progress(
                    job_id, {'phase': 'preparing', 'stage_index': 2, 'stage_total': 4, **update}))
        elif job.operation == 'video_preview':
            from storm_studio.video_previews import materialize_video_preview

            _record_progress(job_id, {
                'phase': 'video', 'label': 'Preparando la vista compatible del video',
                'stage_index': 2, 'stage_total': 4,
            })
            result = materialize_video_preview(
                job.revision.payload['dataset_revision'],
                job.revision.payload['asset_id'], settings.WORKSPACE)
        else:
            _record_progress(job_id, {
                'phase': 'loading', 'label': 'Cargando datos registrados',
                'stage_index': 1,
                'stage_total': 4 if job.operation in ('infer', 'apply') else 5,
            })
            options = {}
            if job.operation == 'apply':
                if job.source_id is None or job.source.status != 'completed':
                    raise ValueError('A completed source model is required for inference')
                options['inference_from'] = job.source.result
            elif job.operation == 'update':
                options['update_from'] = job.source.result
            elif job.operation == 'resume':
                from storm.artifacts import FileArtifactStore
                options['resume_from'] = FileArtifactStore(settings.ARTIFACT_ROOT).resolve(
                    kind='checkpoints', artifact_id=str(job.source_id))
                options['resume_config'] = job.source.revision.payload.get('config', {})
            worker_catalog = catalog()
            spec = execution_specs(job.revision)[0][1]
            spec['data_summary'] = plan_data_summary(spec, worker_catalog)
            progress_callback = lambda update: _record_progress(job_id, update)
            dataset_revision_id = spec.get('dataset_revision_id')
            if (spec.get('label_correction_revision_id') is not None
                    and dataset_revision_id is None):
                raise ValueError('Label corrections require a registered dataset source.')
            if dataset_revision_id is not None:
                dataset_revision = DatasetRevision.objects.get(pk=dataset_revision_id)
                if dataset_revision.status != 'ready':
                    raise ValueError('Inspect the registered dataset before running a model.')
                from storm_studio.video_timeline_reviews import require_review

                require_review(job.revision.study, dataset_revision)
                from storm_studio.dataset_inventory import connector_input

                spec['connector'] = dataset_revision.connector
                spec['data'] = connector_input(
                    dataset_revision.pk, workspace=settings.WORKSPACE,
                    artifact_root=settings.ARTIFACT_ROOT)
                spec.pop('preapplied_steps', None)
                if dataset_revision.connector == 'prepared_artifact':
                    preparation = Revision.objects.filter(
                        pk=dataset_revision.config.get('preparation_revision_id'),
                        study=job.revision.study, kind='preparation').first()
                    if preparation is not None:
                        spec['preapplied_steps'] = list(dict.fromkeys(
                            step['type'] for step in preparation.payload.get('steps', [])
                            if isinstance(step, dict) and isinstance(step.get('type'), str)
                        ))
                if spec.get('steps'):
                    from storm_studio.data_preparation import resolve_preparation_steps

                    spec['steps'] = resolve_preparation_steps(
                        spec.get('steps', []),
                        dataset_revision.inventory.get('feature_names')
                        or spec['data'].get('feature_names', []))
                correction_revision_id = spec.get('label_correction_revision_id')
                if correction_revision_id is not None:
                    if job.operation != 'train':
                        raise ValueError(
                            'Label corrections can only be included in a new training plan.')
                    correction_revision = Revision.objects.get(
                        pk=correction_revision_id, study=job.revision.study,
                        kind='label_corrections')
                    applied = apply_label_corrections(
                        spec['data'], correction_revision,
                        dataset_revision=dataset_revision, study=job.revision.study)
                    spec['data'] = applied['data']
                    result = execute(
                        spec, settings.ARTIFACT_ROOT, str(job.pk), worker_catalog,
                        progress_callback=progress_callback, **options)
                    result['label_corrections'] = {
                        'revision_id': correction_revision.pk,
                        'source_dataset_revision_id': correction_revision.payload[
                            'dataset_revision_id'],
                        'source_fingerprint': correction_revision.payload[
                            'source_fingerprint'],
                        'applied_observations': applied['applied_observations'],
                        'excluded_observations': applied['excluded_observations'],
                    }
                else:
                    result = execute(
                        spec, settings.ARTIFACT_ROOT, str(job.pk), worker_catalog,
                        progress_callback=progress_callback, **options)
            else:
                result = execute(
                    spec, settings.ARTIFACT_ROOT, str(job.pk), worker_catalog,
                    progress_callback=progress_callback, **options)
        finished = timezone.now()
        current_progress = Job.objects.only('progress').get(pk=job_id).progress
        stage_total = (current_progress.get('stage_total')
                       if isinstance(current_progress, dict) else None)
        if type(stage_total) is not int or stage_total < 1:
            stage_total = 4 if job.operation in ('infer', 'apply') else 5
        _record_progress(job_id, {
            'phase': 'completed', 'label': 'Ejecución completada',
            'stage_index': stage_total, 'stage_total': stage_total,
            'phase_step': 1, 'phase_total': 1, 'unit_label': 'resultado',
            'fraction': 1.0, 'eta_seconds': 0,
        })
        final_progress = Job.objects.only('progress').get(pk=job_id).progress
        persisted_result = _result_for_database(job_id, result)
        Job.objects.filter(pk=job_id, status='running').update(
            status=(result['status'] if job.operation == 'experiment'
                    and result.get('status') in ('partial', 'failed') else 'completed'), result=persisted_result,
            progress=final_progress, finished=finished)
    except Exception as error:
        logger.exception('Background job %s failed', job_id)
        try:
            last_progress = Job.objects.only('progress').get(pk=job_id).progress or {}
            _record_progress(job_id, {
                'failure_context': {key: last_progress[key] for key in ('phase', 'epoch', 'batch_step', 'batch_total', 'checkpoint_epoch', 'device', 'component', 'operation', 'failure_kind', 'rnn_backend') if key in last_progress},
                'phase': 'failed',
                'label': f'Proceso fallido: {type(error).__name__}: {error}',
                'phase_step': None, 'phase_total': None, 'unit_label': None,
            })
        except Job.DoesNotExist:
            pass
        if job.operation == 'inventory':
            dataset_revision_id = job.revision.payload.get('dataset_revision')
            dataset_revision = DatasetRevision.objects.filter(pk=dataset_revision_id).first()
            if dataset_revision:
                inventory = dict(dataset_revision.inventory)
                inventory['error'] = f'{type(error).__name__}: {error}'
                DatasetRevision.objects.filter(pk=dataset_revision.pk).update(
                    status='failed', inventory=inventory)
        Job.objects.filter(pk=job_id, status='running').update(
            status='failed', error=f'{type(error).__name__}: {error}', finished=timezone.now())


def predict(job, inputs):
    if job.status != 'completed':
        raise ValueError('Model is not available')
    result = infer(job.result, inputs, settings.ARTIFACT_ROOT, catalog())
    Revision.objects.create(study=job.revision.study, kind='inference', parent=job.revision,
                            payload={'job': str(job.pk), 'inputs': inputs, 'predictions': result})
    return result


def propose(job, count=3, seed=0, strategy='random'):
    if job.status != 'completed':
        raise ValueError('Select a completed model')
    spec = job.revision.payload
    result = load_execution_result(job)
    candidates = list(result['resolved_data']['train'])
    from storm.learning import select_samples
    scores = None
    if strategy == 'uncertainty':
        from storm.artifacts import FileArtifactStore, ArtifactRef
        from storm.suite import transform
        model = FileArtifactStore(settings.ARTIFACT_ROOT).load(ArtifactRef.from_dict(result['model_ref']))
        prepared, _ = transform([result['resolved_data']['inputs'][i] for i in candidates],
                                spec.get('steps', []), result['fitted_steps'], catalog())
        output = model.predict(prepared)
        confidence = output.metadata.get('confidence')
        if output.metadata.get('confidence_semantics') != 'probability' or confidence is None or len(confidence) != len(candidates):
            raise ValueError('The adapter must provide probability confidence for every candidate')
        scores = dict(zip(candidates, confidence))
    indices = select_samples(candidates, count=count, seed=seed, strategy=strategy, scores=scores)
    return Revision.objects.create(study=job.revision.study, kind='review', parent=job.revision,
        payload={'job': str(job.pk), 'indices': indices, 'seed': seed, 'strategy': strategy,
                 'evidence': [{'index': i, 'input': result['resolved_data']['inputs'][i],
                               'target': (result['resolved_data'].get('targets') or [None] * len(result['resolved_data']['inputs']))[i]} for i in indices],
                 'status': 'pending'})


@transaction.atomic
def review(task, labels, constraints):
    if task.kind != 'review' or task.parent is None:
        raise ValueError('Not a review task')
    if Revision.objects.filter(kind='decision', parent=task).exists():
        raise ValueError('This review batch has already been accepted')
    spec = deepcopy(task.parent.payload)
    from math import isfinite
    if not isinstance(labels, dict) or not isinstance(constraints, list):
        raise ValueError('Expected label object and constraint list')
    for index, label in labels.items():
        i = int(index)
        if i not in task.payload['indices']:
            raise ValueError('Correction outside the selected review batch')
        if spec.get('connector', 'numeric_json') == 'numeric_json' and (type(label) not in (int, float) or not isfinite(label)):
            raise ValueError('Numeric label required')
        from storm.config import json_compatible
        json_compatible(label)
        if spec['data'].get('targets') is None:
            raise ValueError('This dataset has no numeric target vector')
        spec['data']['targets'][i] = label
    for pair in constraints:
        if not isinstance(pair, list) or len(pair) != 2 or any(type(i) is not int or i not in spec['data']['train'] for i in pair):
            raise ValueError('Constraints must reference training observations')
    spec['constraints'] = spec.get('constraints', []) + constraints
    Revision.objects.create(study=task.study, kind='decision', parent=task,
                            payload={'labels': labels, 'constraints': constraints})
    return Revision.objects.create(study=task.study, kind='plan', parent=task.parent, payload=spec)


@transaction.atomic
def correct_dataset_label(study, dataset_revision, *, session_id, observation_id, frame,
                          label, author, reason):
    """Save a human label correction as an immutable layer over registered data."""
    if (not study.dataset_revision_id or study.dataset_revision_id != dataset_revision.pk
            or dataset_revision.status != 'ready'):
        raise ValueError('Label corrections require the active, inspected dataset revision.')
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError('Select a registered session.')
    if not isinstance(observation_id, str) or not observation_id.strip():
        raise ValueError('Select a registered observation.')
    if type(frame) is not int or frame < 0:
        raise ValueError('Pose frame must be a nonnegative integer.')
    if not isinstance(author, str) or not author.strip() or len(author.strip()) > 160:
        raise ValueError('Provide an annotation author of at most 160 characters.')
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 2000:
        raise ValueError('Provide an annotation reason of at most 2000 characters.')

    taxonomy = dataset_revision.inventory.get('taxonomy')
    if (not isinstance(taxonomy, list) or not taxonomy
            or any(not isinstance(item, str) or not item.strip() for item in taxonomy)
            or len(set(taxonomy)) != len(taxonomy)):
        raise ValueError('The dataset must declare a distinct, nonempty label taxonomy.')
    if not isinstance(label, str) or label not in taxonomy:
        raise ValueError('Choose a label from the dataset taxonomy.')
    source_fingerprint = dataset_revision.inventory.get('source_fingerprint')
    store_ref = dataset_revision.inventory.get('pose_preview_store')
    if not isinstance(source_fingerprint, str) or not source_fingerprint or not store_ref:
        raise ValueError('The dataset inventory must include its fingerprint and pose index.')

    from storm_studio.pose_preview import read_pose_preview_page

    page = read_pose_preview_page(
        root=settings.ARTIFACT_ROOT, store_ref=store_ref,
        session_id=session_id, frame=frame)
    selected = next((row for row in page['rows']
                     if row.get('observation_id') == observation_id
                     and row.get('frame') == frame), None)
    if selected is None:
        raise ValueError('The selected observation does not match this session and frame.')

    latest = (Revision.objects.filter(
        study=study, kind='label_corrections',
        payload__dataset_revision_id=dataset_revision.pk).order_by('-pk').first())
    if latest and latest.payload.get('source_fingerprint') != source_fingerprint:
        raise ValueError('The source data changed; start a new label review for this revision.')
    corrections = deepcopy(latest.payload.get('corrections', {})) if latest else {}
    if not isinstance(corrections, dict):
        raise ValueError('The previous label correction revision is invalid.')
    previous = corrections.get(observation_id, {})
    corrections[observation_id] = {
        'session_id': session_id,
        'observation_id': observation_id,
        'pose_frame': frame,
        'video_frame': selected.get('video_frame'),
        'original_target': previous.get('original_target', selected.get('target')),
        'original_label_valid': previous.get(
            'original_label_valid', selected.get('evaluation_mask') is True),
        'label': label,
        'author': author.strip(),
        'reason': reason.strip(),
    }
    return Revision.objects.create(
        study=study, kind='label_corrections', parent=latest,
        payload={'schema_version': 1,
                 'dataset_revision_id': dataset_revision.pk,
                 'source_fingerprint': source_fingerprint,
                 'taxonomy': list(taxonomy),
                 'corrections': corrections})


def _root_dataset_revision(dataset_revision):
    """Resolve a prepared dataset back to the inspected revision it derives from."""
    current = dataset_revision
    seen = set()
    while current.config.get('source_dataset_revision_id'):
        if current.pk in seen:
            raise ValueError('The prepared dataset lineage contains a cycle.')
        seen.add(current.pk)
        source_id = current.config['source_dataset_revision_id']
        current = DatasetRevision.objects.get(pk=source_id, status='ready')
        if current.dataset_id != dataset_revision.dataset_id:
            raise ValueError('The prepared dataset lineage crosses source datasets.')
    return current


def validate_label_correction_scope(study, dataset_revision, correction_revision):
    """Check that a correction layer belongs to this study and data lineage."""
    if correction_revision.kind != 'label_corrections' or correction_revision.study_id != study.pk:
        raise ValueError('Choose a label correction review from this study.')
    payload = correction_revision.payload
    source = _root_dataset_revision(dataset_revision)
    if payload.get('dataset_revision_id') != source.pk:
        raise ValueError('The correction review belongs to a different dataset revision.')
    fingerprint = source.inventory.get('source_fingerprint')
    if not fingerprint or payload.get('source_fingerprint') != fingerprint:
        raise ValueError('The correction review belongs to a different source fingerprint.')
    taxonomy = source.inventory.get('taxonomy')
    if not isinstance(taxonomy, list) or not taxonomy or payload.get('taxonomy') != taxonomy:
        raise ValueError('The correction review taxonomy does not match this dataset.')
    corrections = payload.get('corrections')
    if not isinstance(corrections, dict):
        raise ValueError('The correction review is invalid.')
    for observation_id, correction in corrections.items():
        if (not isinstance(observation_id, str) or not isinstance(correction, dict)
                or correction.get('observation_id', observation_id) != observation_id
                or correction.get('label') not in taxonomy):
            raise ValueError('The correction review contains an invalid label or observation.')
    return source


def apply_label_corrections(data, correction_revision, *, dataset_revision, study):
    """Apply an explicitly selected review layer to unreserved training rows only."""
    source = validate_label_correction_scope(study, dataset_revision, correction_revision)
    corrected = deepcopy(data)
    inputs = corrected.get('inputs')
    if not isinstance(inputs, list):
        raise ValueError('Corrected training data must contain an inputs list.')
    count = len(inputs)
    observation_ids = corrected.get('observation_ids')
    sessions = corrected.get('sessions')
    frames = corrected.get('frames')
    partitions = corrected.get('partitions')
    reserved = corrected.get('reserved_evaluation')
    targets = corrected.get('targets')
    evaluation_mask = corrected.get('evaluation_mask')
    aligned = (observation_ids, sessions, frames, partitions, reserved, targets)
    if any(not isinstance(values, list) or len(values) != count for values in aligned):
        raise ValueError('Correction labels require aligned observation, session, frame, partition, reservation, and target data.')
    if evaluation_mask is None:
        evaluation_mask = [target is not None for target in targets]
    if (not isinstance(evaluation_mask, list) or len(evaluation_mask) != count
            or any(type(value) is not bool for value in evaluation_mask)
            or any(type(value) is not bool for value in reserved)):
        raise ValueError('Correction label masks must align with observations.')
    if len(set(observation_ids)) != count:
        raise ValueError('Correction labels require unique observation identifiers.')

    row_by_id = {observation_id: index
                 for index, observation_id in enumerate(observation_ids)}
    taxonomy = source.inventory['taxonomy']
    taxonomy_index = {label: index for index, label in enumerate(taxonomy)}
    applied = []
    excluded = {}
    for observation_id, correction in correction_revision.payload['corrections'].items():
        index = row_by_id.get(observation_id)
        if index is None:
            excluded[observation_id] = 'observation_not_found'
            continue
        if (str(sessions[index]) != correction.get('session_id')
                or frames[index] != correction.get('pose_frame')):
            raise ValueError(
                f'Correction observation {observation_id!r} no longer matches its source frame.')
        if reserved[index]:
            excluded[observation_id] = 'reserved_evaluation'
            continue
        if partitions[index] != 'train':
            excluded[observation_id] = 'not_training_partition'
            continue
        targets[index] = taxonomy_index[correction['label']]
        evaluation_mask[index] = True
        applied.append(observation_id)

    corrected['targets'] = targets
    corrected['evaluation_mask'] = evaluation_mask
    return {'data': corrected, 'applied_observations': applied,
            'excluded_observations': excluded}


def comparable(jobs):
    return not compare_reasons(jobs)


def session_local_state_summary(result):
    metadata = result.get('output_metadata') or {}
    if metadata.get('discretizer_scope') not in {'session_local', 'per_session'}:
        return False
    data = result.get('resolved_data') or {}
    sessions = data.get('sessions') or []
    indices = result.get('indices') or []
    return len({str(sessions[index]) for index in indices
                if type(index) is int and 0 <= index < len(sessions)}) > 1


def compare_group_stability(jobs):
    """Compare two grouping runs with permutation-invariant adjusted Rand index."""
    if len(jobs) != 2:
        return None
    first, second = [job.result if isinstance(job.result, dict) else {} for job in jobs]
    if any(session_local_state_summary(result) for result in (first, second)):
        return None
    if any('group' not in result.get('capabilities', []) for result in (first, second)):
        return None
    fingerprint = first.get('data_fingerprint')
    if not fingerprint or fingerprint != second.get('data_fingerprint'):
        return None

    def labels_by_index(result):
        indices = result.get('indices') or []
        predictions = result.get('predictions') or []
        valid = result.get('prediction_mask') or [True] * len(indices)
        if not (len(indices) == len(predictions) == len(valid)):
            return None
        if any(type(item) is not bool for item in valid):
            return None
        return {index: prediction for index, prediction, keep
                in zip(indices, predictions, valid) if keep}

    first_labels, second_labels = labels_by_index(first), labels_by_index(second)
    if first_labels is None or second_labels is None:
        return None
    common = sorted(first_labels.keys() & second_labels.keys())
    if len(common) < 2:
        return None

    from collections import Counter

    left = [first_labels[index] for index in common]
    right = [second_labels[index] for index in common]
    left_counts, right_counts = Counter(left), Counter(right)
    cells = Counter(zip(left, right))
    choose_two = lambda count: count * (count - 1) / 2
    cell_pairs = sum(choose_two(count) for count in cells.values())
    left_pairs = sum(choose_two(count) for count in left_counts.values())
    right_pairs = sum(choose_two(count) for count in right_counts.values())
    total_pairs = choose_two(len(common))
    expected = left_pairs * right_pairs / total_pairs
    maximum = 0.5 * (left_pairs + right_pairs)
    denominator = maximum - expected
    if denominator == 0:
        left_to_right, right_to_left = {}, {}
        same_partition = True
        for left_label, right_label in zip(left, right):
            if ((left_label in left_to_right and left_to_right[left_label] != right_label)
                    or (right_label in right_to_left
                        and right_to_left[right_label] != left_label)):
                same_partition = False
                break
            left_to_right[left_label] = right_label
            right_to_left[right_label] = left_label
        value = 1.0 if same_partition else 0.0
    else:
        value = (cell_pairs - expected) / denominator
    return {
        'metric': 'ARI',
        'value': round(value, 6),
        'observations': len(common),
        'interpretation': (
            'No mide concordancia humana; cuantifica la estabilidad de la partición '
            'entre estas dos corridas y no depende de los IDs de estado.'),
    }


def compare_reasons(jobs):
    """Return explicit reasons why completed results cannot be ranked together."""
    if len(jobs) < 2:
        return ['Select at least two completed executions']
    reasons = []
    results = [job.result if isinstance(job.result, dict) else {} for job in jobs]
    if any(session_local_state_summary(result) for result in results):
        reasons.append('session-local state IDs cannot be pooled across sessions')
    data_summaries = []
    for result in results:
        summary = result.get('resolved_data_summary')
        if not isinstance(summary, dict):
            data = result.get('resolved_data') or {}
            targets = data.get('targets')
            indices = result.get('metric_indices', result.get('indices', ()))
            summary = {
                'taxonomy': data.get('taxonomy'),
                'targets_available_for_metrics': (
                    bool(indices) and isinstance(targets, list) and all(
                        type(index) is int and 0 <= index < len(targets)
                        and targets[index] is not None for index in indices)),
            }
        data_summaries.append(summary)
    if len({j.result.get('data_fingerprint') for j in jobs}) != 1:
        reasons.append('data fingerprints differ')
    if len({j.result.get('partition') for j in jobs}) != 1:
        reasons.append('evaluation partitions differ')
    observation_sets = {tuple(j.result.get('indices', ())) for j in jobs}
    if len(observation_sets) != 1:
        reasons.append('evaluated observations differ')
    metric_sets = {tuple(sorted(j.result.get('metrics', {}))) for j in jobs}
    if len(metric_sets) != 1 or any(not j.result.get('metrics') for j in jobs):
        reasons.append('metric sets differ or are empty')
    metric_definitions = {tuple((item.get('name'), item.get('version'), item.get('direction'))
                                for item in j.result.get('metric_definitions', [])) for j in jobs}
    if len(metric_definitions) != 1:
        reasons.append('metric definitions differ or are missing')
    if any(j.result.get('model_version') is None for j in jobs):
        reasons.append('a result has no model version')
    semantics = {json.dumps(j.result.get('output_metadata', {}).get('semantics'), sort_keys=True)
                 for j in jobs}
    if len(semantics) != 1:
        reasons.append('output semantics differ')
    output_categories = {
        json.dumps({key: j.result.get('output_metadata', {}).get(key)
                    for key in ('task', 'output_meaning', 'negative_output_meaning',
                                'category_mapping', 'category_mapping_version')},
                   sort_keys=True)
        for j in jobs
    }
    if len(output_categories) != 1:
        reasons.append('output task or category meanings differ')
    for job in jobs:
        operation = getattr(job, 'operation', None) or job.result.get('spec', {}).get('operation')
        if operation != 'infer':
            continue
        population = job.result.get('output_metadata', {}).get('training_population')
        if (not isinstance(population, str)
                or population.strip().casefold() in {'', 'unknown', 'unspecified'}):
            reasons.append(
                'training population is unknown; overlap with the evaluation benchmark cannot be ruled out')
            break
    taxonomies = {json.dumps(item.get('taxonomy'), sort_keys=True)
                  for item in data_summaries}
    if len(taxonomies) != 1:
        reasons.append('target taxonomies differ')
    for result, summary in zip(results, data_summaries):
        metadata = result.get('output_metadata', {})
        if metadata.get('task') != 'binary_classification':
            continue
        taxonomy = summary.get('taxonomy')
        mapping = metadata.get('category_mapping')
        if (not isinstance(taxonomy, list) or len(taxonomy) != 2
                or any(not isinstance(label, str) or not label for label in taxonomy)
                or len(set(taxonomy)) != 2
                or mapping != {'0': taxonomy[0], '1': taxonomy[1]}
                or metadata.get('output_meaning') != taxonomy[1]
                or metadata.get('negative_output_meaning') != taxonomy[0]
                or not isinstance(metadata.get('category_mapping_version'), str)
                or not metadata['category_mapping_version'].strip()):
            reasons.append(
                'binary outputs need a mapping to the two-category evaluation taxonomy')
            break
    metric_observations = {tuple(j.result.get('metric_indices', j.result.get('indices', ())))
                           for j in jobs}
    if len(metric_observations) != 1:
        reasons.append('evaluated metric observations differ')
    metric_targets = []
    targets_missing = False
    for job, result, summary in zip(jobs, results, data_summaries):
        if 'targets_available_for_metrics' in summary:
            if not summary['targets_available_for_metrics']:
                targets_missing = True
                continue
            metric_targets.append(result.get('data_fingerprint'))
            continue
        targets = result.get('resolved_data', {}).get('targets')
        indices = result.get('metric_indices', result.get('indices', ()))
        if not isinstance(targets, list) or any(
                type(index) is not int or not 0 <= index < len(targets)
                or targets[index] is None for index in indices):
            targets_missing = True
            continue
        metric_targets.append(json.dumps([targets[index] for index in indices], sort_keys=True))
    if targets_missing:
        reasons.append('evaluation targets are missing')
    elif len(set(metric_targets)) != 1:
        reasons.append('evaluation targets differ')
    return reasons


def annotate(job, *, taxonomy, intervals, mapping, author, reason):
    """Create a separate human interpretation; never rewrite model outputs."""
    if job.status != 'completed':
        raise ValueError('Annotations require a completed result')
    if not isinstance(author, str) or not author.strip() or len(author.strip()) > 160:
        raise ValueError('Provide an annotation author of at most 160 characters')
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 2000:
        raise ValueError('Provide an annotation reason of at most 2000 characters')
    if (not isinstance(taxonomy, list) or not taxonomy or
            any(not isinstance(label, str) or not label.strip() for label in taxonomy) or
            len(set(taxonomy)) != len(taxonomy)):
        raise ValueError('Taxonomy must contain distinct nonempty names')
    if not isinstance(intervals, list) or not isinstance(mapping, dict):
        raise ValueError('Expected interval list and interpretation object')
    summary = job.result.get('resolved_data_summary', {})
    count = summary.get('observation_count')
    if not isinstance(count, int):
        count = len(job.result['resolved_data']['inputs'])
    for item in intervals:
        if (not isinstance(item, dict) or type(item.get('start')) is not int or type(item.get('stop')) is not int
                or not 0 <= item['start'] < item['stop'] <= count or item.get('label') not in taxonomy):
            raise ValueError('Invalid half-open observation interval or label')
    if mapping and 'group' not in job.result['capabilities']:
        raise ValueError('Group interpretation requires a grouping model')
    groups = {str(value) for value in job.result['predictions']}
    if any(group not in groups or label not in taxonomy for group, label in mapping.items()):
        raise ValueError('Unknown group or taxonomy label')
    previous = job.revision.study.revision_set.filter(kind='annotations', payload__job=str(job.pk)).order_by('-pk').first()
    return Revision.objects.create(study=job.revision.study, kind='annotations', parent=previous or job.revision,
        payload={'job': str(job.pk), 'data_fingerprint': job.result['data_fingerprint'],
                 'taxonomy': taxonomy, 'intervals': intervals, 'mapping': mapping,
                 'author': author.strip(), 'reason': reason.strip(),
                 'unit': 'observation_index', 'interval_convention': '[start, stop)', 'origin': 'human'})
