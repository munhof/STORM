import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from storm_studio.models import Job


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
                    child = subprocess.Popen([sys.executable, '-c', code, str(job.pk)])
                    while child.poll() is None:
                        if stopping:
                            child.terminate()
                            try:
                                child.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                child.kill()
                                child.wait()
                            break
                        time.sleep(.2)
                        if Job.objects.filter(pk=job.pk, status='cancelled').exists():
                            child.terminate()
                            try:
                                child.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                child.kill()
                                child.wait()
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
