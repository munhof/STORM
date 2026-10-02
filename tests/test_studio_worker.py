import os
import json
import subprocess
import sys
import time
from pathlib import Path


def test_worker_heartbeat_reports_liveness_without_claiming_new_progress():
    from storm_studio.management.commands.worker import progress_heartbeat

    message = progress_heartbeat({
        'label': 'Entrenando el modelo', 'stage_index': 3, 'stage_total': 5,
        'phase_step': 2, 'phase_total': 10, 'unit_label': 'épocas',
    }, quiet_seconds=45)

    assert 'No confirma avance del procesamiento' in message
    assert 'El proceso sigue activo' in message
    assert 'Etapa 3 de 5' in message
    assert 'Entrenando el modelo' in message
    assert 'sin una actualización de avance hace 45 s' in message


def test_worker_process_and_recovery(tmp_path):
    env = dict(os.environ, DJANGO_SETTINGS_MODULE='storm_studio.settings', STORM_WORKSPACE=str(tmp_path))
    def run(*args):
        return subprocess.run([sys.executable, *args], env=env, text=True,
                              capture_output=True, check=True, timeout=30)
    run('-m', 'django', 'migrate', '--noinput')
    run('-m', 'django', 'demo')
    run('-c', 'import django; django.setup(); from storm_studio.models import Revision; '
        'from storm_studio.services import submit; submit(Revision.objects.get(kind="plan"))')
    run('-m', 'django', 'worker', '--once')
    recovered = run('-c', 'import django; django.setup(); from storm_studio.models import Job; '
        'from storm_studio.services import predict; j=Job.objects.get(); '
        'assert j.status=="completed", j.error; print(predict(j,[12,13]))')
    assert '[7.0, 7.0]' in recovered.stdout
    assert not (tmp_path / 'worker.lock').exists()


def test_worker_persists_stage_and_error_output_in_job_logs(tmp_path):
    env = dict(os.environ, DJANGO_SETTINGS_MODULE='storm_studio.settings', STORM_WORKSPACE=str(tmp_path))

    def run(*args):
        return subprocess.run([sys.executable, *args], env=env, text=True,
                              capture_output=True, check=True, timeout=30)

    run('-m', 'django', 'migrate', '--noinput')
    run('-m', 'django', 'demo')
    created = run('-c',
        'import django; django.setup(); from storm_studio.models import Study, Revision; '
        'from storm_studio.services import submit; '
        'study=Study.objects.first(); '
        'revision=Revision.objects.create(study=study, kind="plan", payload={'
        '"model":"missing_model_for_log_test", "config":{}, "steps":[], '
        '"data":{"inputs":[1,2], "train":[0], "test":[1]}}); '
        'print(submit(revision).pk)')
    job_id = created.stdout.strip()

    run('-m', 'django', 'worker', '--once')
    logs = run('-c',
        'import django; django.setup(); import json; from storm_studio.models import Job; '
        f'job=Job.objects.get(pk="{job_id}"); '
        'print(json.dumps({"logs":job.logs,"progress":job.progress}))')

    assert 'Worker iniciado' in logs.stdout
    assert 'Cargando y validando los datos' in logs.stdout
    assert 'Background job' in logs.stdout
    assert 'Traceback' in logs.stdout


def test_worker_removes_lock_on_sigterm(tmp_path):
    env = dict(os.environ, DJANGO_SETTINGS_MODULE='storm_studio.settings', STORM_WORKSPACE=str(tmp_path))
    subprocess.run([sys.executable, '-m', 'django', 'migrate', '--noinput'],
                   env=env, check=True, capture_output=True, timeout=30)
    lock = tmp_path / 'worker.lock'
    worker = subprocess.Popen([sys.executable, '-m', 'django', 'worker'], env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 10
    while not lock.exists() and worker.poll() is None and time.monotonic() < deadline:
        time.sleep(.05)
    assert lock.exists(), 'worker did not acquire its workspace lock'

    worker.terminate()
    _, error = worker.communicate(timeout=10)

    assert worker.returncode == 0, error
    assert not lock.exists()


def test_worker_recognizes_a_live_identity_lock(tmp_path):
    env = dict(os.environ, DJANGO_SETTINGS_MODULE='storm_studio.settings', STORM_WORKSPACE=str(tmp_path))
    subprocess.run([sys.executable, '-m', 'django', 'migrate', '--noinput'],
                   env=env, check=True, capture_output=True, timeout=30)
    stat_fields = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
    owner = {'pid': os.getpid(), 'start_time': stat_fields[19],
             'pid_namespace': os.stat('/proc/self/ns/pid').st_ino}
    lock = tmp_path / 'worker.lock'
    lock.write_text(json.dumps(owner), encoding='utf-8')

    duplicate = subprocess.run([sys.executable, '-m', 'django', 'worker', '--recover-stale', '--once'],
                               env=env, text=True, capture_output=True, timeout=30)

    assert duplicate.returncode != 0
    assert 'still active' in duplicate.stderr
    assert lock.exists()
