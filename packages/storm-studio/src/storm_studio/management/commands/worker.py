import codecs
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from storm_studio.models import Job


WORKER_HEARTBEAT_SECONDS = 15


def progress_heartbeat(progress, *, quiet_seconds):
    """Describe a live child without implying that its model reported progress."""
    progress = progress if isinstance(progress, dict) else {}
    label = progress.get('label') or 'ejecutando una etapa sin porcentaje informado'
    stage, total = progress.get('stage_index'), progress.get('stage_total')
    prefix = f'Etapa {stage} de {total}: ' if stage and total else ''
    message = f'El proceso sigue activo · {prefix}{label}'
    step, units = progress.get('phase_step'), progress.get('phase_total')
    if step is not None and units is not None:
        message += (f" · último avance {step}/{units} "
                    f"{progress.get('unit_label') or 'unidades'}")
    return f'{message}; sin una actualización de avance hace {quiet_seconds} s.'


def process_identity(pid):
    stat_fields = Path(f'/proc/{pid}/stat').read_text(encoding='utf-8').rsplit(')', 1)[1].split()
    return {
        'pid': pid,
        'start_time': stat_fields[19],
        'pid_namespace': os.stat(f'/proc/{pid}/ns/pid').st_ino,
    }


def lock_owner_is_alive(contents):
    owner = json.loads(contents)
    if isinstance(owner, dict):
        pid = owner.get('pid')
        if type(pid) is not int or not isinstance(owner.get('start_time'), str):
            raise ValueError('Invalid worker identity')
        return process_identity(pid) == owner
    if type(owner) is not int or owner < 1:
        raise ValueError('Invalid worker PID')
    os.kill(owner, 0)
    if os.environ.get('STORM_CONTAINERIZED') == '1':
        arguments = Path(f'/proc/{owner}/cmdline').read_bytes().decode(errors='replace').split('\0')
        return 'worker' in arguments and any('storm-studio' in value for value in arguments)
    return True


class Command(BaseCommand):
    help = 'Execute the local queue in isolated child processes. Run one worker per workspace.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--recover-stale', action='store_true',
                            help='Remove the lock only when its recorded PID is no longer alive.')

    def handle(self, *args, **options):
        from django.conf import settings
        lock = settings.WORKSPACE / 'worker.lock'
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not options['recover_stale']:
                raise CommandError('Worker lock exists. Use --recover-stale after verifying the recorded PID.')
            try:
                owner_active = lock_owner_is_alive(lock.read_text(encoding='utf-8').strip())
            except (FileNotFoundError, ValueError, ProcessLookupError):
                lock.unlink(missing_ok=True)
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except PermissionError:
                raise CommandError('The worker recorded in worker.lock is still active.')
            else:
                if not owner_active:
                    lock.unlink(missing_ok=True)
                    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                else:
                    raise CommandError('The worker recorded in worker.lock is still active.')
        try:
            os.write(descriptor, json.dumps(process_identity(os.getpid())).encode())
        finally:
            os.close(descriptor)
        child = None
        stopping = False

        def request_stop(_signum, _frame):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        try:
            Job.objects.filter(status='running').update(status='interrupted', finished=timezone.now())
            while not stopping:
                job = Job.objects.filter(status='pending').order_by('created').first()
                if job:
                    code = 'import django; django.setup(); from storm_studio.services import perform; perform(__import__("sys").argv[1])'
                    child = subprocess.Popen(
                        [sys.executable, '-u', '-c', code, str(job.pk)],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
                    from storm_studio.services import append_job_logs

                    append_job_logs(job.pk, ['Worker iniciado; esperando el primer avance.'])
                    output = child.stdout
                    selector = selectors.DefaultSelector()
                    selector.register(output, selectors.EVENT_READ)
                    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
                    current_line = ''
                    pending_logs = []
                    last_flush = time.monotonic()
                    last_progress = None
                    last_progress_at = last_flush
                    last_heartbeat_at = last_flush
                    child_stopped = False
                    try:
                        while child.poll() is None or selector.get_map():
                            if child.poll() is None and not child_stopped:
                                cancelled = Job.objects.filter(
                                    pk=job.pk, status='cancelled').exists()
                                if stopping or cancelled:
                                    child.terminate()
                                    try:
                                        child.wait(timeout=5)
                                    except subprocess.TimeoutExpired:
                                        child.kill()
                                        child.wait()
                                    child_stopped = True

                            if selector.get_map():
                                for key, _ in selector.select(timeout=.2):
                                    chunk = os.read(key.fileobj.fileno(), 65536)
                                    text = decoder.decode(chunk) if chunk else decoder.decode(
                                        b'', final=True)
                                    for character in text:
                                        if character in '\r\n':
                                            if current_line.strip():
                                                pending_logs.append(current_line)
                                            current_line = ''
                                        else:
                                            current_line += character
                                            if len(current_line) >= 2_000:
                                                pending_logs.append(current_line)
                                                current_line = ''
                                    if not chunk:
                                        selector.unregister(key.fileobj)
                            elif child.poll() is None:
                                time.sleep(.2)

                            current_job_progress = Job.objects.only('progress').get(
                                pk=job.pk).progress
                            if isinstance(current_job_progress, dict):
                                label = current_job_progress.get('label')
                                signature = (
                                    current_job_progress.get('stage_index'), label,
                                    current_job_progress.get('phase_step'),
                                    current_job_progress.get('phase_total'),
                                    current_job_progress.get('unit_label'))
                                if label and signature != last_progress:
                                    stage = current_job_progress.get('stage_index')
                                    total = current_job_progress.get('stage_total')
                                    prefix = (f'Etapa {stage} de {total}: '
                                              if stage and total else '')
                                    step = current_job_progress.get('phase_step')
                                    units = current_job_progress.get('phase_total')
                                    detail = ''
                                    if step is not None and units is not None:
                                        unit = current_job_progress.get('unit_label') or 'unidades'
                                        detail = f' · avance {step} de {units} {unit}'
                                    pending_logs.append(f'{prefix}{label}{detail}')
                                    last_progress = signature
                                    last_progress_at = time.monotonic()

                            now = time.monotonic()
                            if (child.poll() is None
                                    and now - last_heartbeat_at >= WORKER_HEARTBEAT_SECONDS):
                                pending_logs.append(progress_heartbeat(
                                    current_job_progress,
                                    quiet_seconds=int(now - last_progress_at)))
                                last_heartbeat_at = now
                            if pending_logs and (len(pending_logs) >= 10
                                                 or now - last_flush >= 1
                                                 or (child.poll() is not None
                                                     and not selector.get_map())):
                                append_job_logs(job.pk, pending_logs)
                                pending_logs = []
                                last_flush = now
                        if current_line.strip():
                            pending_logs.append(current_line)
                        if pending_logs:
                            append_job_logs(job.pk, pending_logs)
                    finally:
                        selector.close()
                        output.close()
                    if child.returncode and not stopping:
                        Job.objects.filter(pk=job.pk, status__in=['pending', 'running']).update(
                            status='failed', error=f'Worker process exited {child.returncode}', finished=timezone.now())
                    child = None
                if options['once']:
                    return
                time.sleep(.5)
        finally:
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            Job.objects.filter(status='running').update(status='interrupted', finished=timezone.now())
            lock.unlink(missing_ok=True)
