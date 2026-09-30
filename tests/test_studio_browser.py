import pytest
from concurrent.futures import ThreadPoolExecutor
from playwright.sync_api import sync_playwright, expect


@pytest.mark.django_db(transaction=True)
def test_execution_progress_updates_without_reloading_the_page(live_server, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from django.utils import timezone
    from storm_studio.models import Job, Project, Revision, Study

    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Live progress')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'vame_native', 'config': {}, 'steps': [], 'data': {},
    })
    job = Job.objects.create(
        revision=plan, status='running', operation='train', started=timezone.now(),
        progress={
            'phase': 'loading', 'label': 'Cargando datos registrados',
            'stage_index': 1, 'stage_total': 5, 'updated_at': timezone.now().isoformat(),
        })

    with ThreadPoolExecutor(max_workers=1) as database, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1024, 'height': 800})
        page.goto(f'{live_server.url}/studies/{study.pk}/jobs/')
        expect(page.locator('[data-progress-label]')).to_have_text(
            'Cargando datos registrados')
        expect(page.locator('[data-execution-summary]')).to_have_text('1 en curso')
        original_url = page.url
        database.submit(Job.objects.filter(pk=job.pk).update, progress={
            'phase': 'training', 'label': 'Entrenando el modelo · época 2 de 10',
            'stage_index': 3, 'stage_total': 5, 'phase_step': 2,
            'phase_total': 10, 'fraction': 0.2, 'eta_seconds': 80,
            'updated_at': timezone.now().isoformat(),
        }).result()
        expect(page.locator('[data-progress-label]')).to_have_text(
            'Entrenando el modelo · época 2 de 10')
        expect(page.locator('[data-progress-detail]')).to_contain_text('20%')
        expect(page.locator('[data-progress-detail]')).to_contain_text('restan 8 épocas')
        expect(page.locator('[data-eta-text]')).to_contain_text('1 min')
        assert page.url == original_url
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_create_execute_review_and_download(live_server, settings, tmp_path):
    from storm_studio.models import Job, Revision, Study
    from storm_studio.services import perform
    settings.ARTIFACT_ROOT = tmp_path
    with ThreadPoolExecutor(max_workers=1) as database, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(live_server.url)
        page.get_by_label('Nombre', exact=True).fill('Synthetic research')
        page.get_by_role('button', name='Crear estudio').click()
        study = database.submit(Study.objects.get, name='Synthetic research').result()
        study_url = f'{live_server.url}/studies/{study.pk}'
        page.goto(f'{study_url}/models/')
        expect(page.get_by_role(
            'link', name='Configurar entrenamiento y evaluación')).to_be_visible()
        page.get_by_role('link', name='Configurar entrenamiento y evaluación').click()
        page.locator('#id_model').select_option('mean_regressor')
        page.get_by_role('button', name='Guardar revisión del plan').click()
        page.goto(f'{study_url}/models/')
        expect(page.get_by_role(
            'link', name='Iniciá la corrida y revisá las métricas')).to_be_visible()
        page.get_by_role('link', name='Iniciá la corrida y revisá las métricas').click()
        assert page.get_by_role('button', name='Iniciar entrenamiento de mean_regressor').count() == 1, (
            page.url, page.locator('body').inner_text())
        page.get_by_role('button', name='Iniciar entrenamiento de mean_regressor').click()
        expect(page.get_by_role('button', name='Plan en ejecución')).to_be_disabled()
        job = database.submit(Job.objects.get).result()
        database.submit(perform, str(job.pk)).result()
        page.reload()
        expect(page.get_by_text('Completada · Entrenamiento o agrupamiento', exact=True)).to_be_visible()
        page.goto(f'{study_url}/models/')
        page.get_by_role('button', name='Inferir con modelo guardado').click()
        expect(page.get_by_role('heading', name='Inferencia completada')).to_be_visible()
        page.get_by_role('link', name='Volver a modelos').click()
        page.goto(f'{study_url}/evidence/')
        page.locator('#cursor').fill('1')
        expect(page.locator('#selection')).to_contain_text('ID 5')
        page.set_viewport_size({'width': 390, 'height': 844})
        overflow = page.evaluate("""() => ({width: document.documentElement.scrollWidth, viewport: innerWidth,
            elements: [...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > innerWidth + 1)
                .map(el => [el.tagName, el.className.baseVal || el.className, Math.round(el.getBoundingClientRect().right)]).slice(0, 8)})""")
        assert overflow['width'] <= overflow['viewport'], overflow
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.screenshot(path='/tmp/storm-django-evidence.png', full_page=True)
        page.get_by_role('button', name='Crear lote de revisión').click()
        task = database.submit(Revision.objects.get, kind='review').result()
        page.get_by_label('Correcciones de etiqueta', exact=False).fill('{"%s": 12}' % task.payload['indices'][0])
        page.get_by_role('button', name='Aceptar correcciones y crear plan derivado').click()
        assert database.submit(Job.objects.count).result() == 1
        for route, label in [('data', 'Datos'), ('components', 'Componentes'),
                             ('flow', 'Flujo'), ('models', 'Modelos'),
                             ('jobs', 'Ejecuciones'), ('evidence', 'Evidencia'),
                             ('review', 'Revisión'), ('compare', 'Comparar'),
                             ('reports', 'Reportes'), ('history', 'Historial')]:
            page.goto(f'{study_url}/{route}/')
            expect(page.get_by_role('heading', name=label, exact=True)).to_be_visible()
        assert errors == []
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_frozen_report_can_be_created_and_downloaded_from_reports_page(
        live_server, settings, tmp_path):
    import json
    import zipfile
    from storm_studio.models import Project, Revision, Study
    from storm_studio.services import perform, submit

    settings.WORKSPACE = tmp_path / 'workspace'
    settings.WORKSPACE.mkdir()
    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Frozen report browser')
    plan = Revision.objects.create(study=study, kind='plan', payload={
        'model': 'identity',
        'data': {'inputs': [1, 2], 'targets': [1, 2], 'train': [0], 'test': [1]},
    })
    job = submit(plan)
    perform(str(job.pk))
    state = {'selected_jobs': [str(job.pk)], 'cursor': 1,
             'filters': {'pose_timeline': 'reference'}}
    snapshot = Revision.objects.create(study=study, kind='snapshot', payload={
        'section': 'evidence', 'visual_state': state,
    })

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f'{live_server.url}/studies/{study.pk}/reports/')
        page.locator(f'input[name="jobs"][value="{job.pk}"]').check()
        page.locator('#report-snapshot').select_option(str(snapshot.pk))
        page.get_by_role('button', name='Congelar reporte').click()
        expect(page.get_by_role('heading', name='Reportes congelados')).to_be_visible()
        with page.expect_download() as download_event:
            page.locator('a[href*="/reports/frozen/"][href$="/json/"]').click()
        download = download_event.value
        download_path = tmp_path / download.suggested_filename
        download.save_as(download_path)
        browser.close()

    payload = json.loads(download_path.read_text(encoding='utf-8'))
    frozen_id = payload['report_revision_id']
    assert payload['snapshot_revision_id'] == snapshot.pk
    assert payload['visual_state'] == state
    assert payload['runs'][0]['job_id'] == str(job.pk)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f'{live_server.url}/studies/{study.pk}/reports/')
        with page.expect_download() as bundle_event:
            page.get_by_role('link', name='Paquete ZIP').click()
        bundle = bundle_event.value
        bundle_path = tmp_path / bundle.suggested_filename
        bundle.save_as(bundle_path)
        browser.close()

    with zipfile.ZipFile(bundle_path) as archive:
        manifest = json.loads(archive.read('bundle_manifest.json'))
        assert manifest['report_revision_id'] == frozen_id


@pytest.mark.django_db(transaction=True)
def test_codeless_preparation_preview_save_and_model_reuse(live_server, settings, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from storm.artifacts import FileArtifactStore
    from storm_studio.models import Dataset, DatasetRevision, Job, Project, Revision, Study
    from storm_studio.services import perform

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    loaded = {
        'inputs': [[0, 10], [2, 20], [100, 1000]],
        'targets': [None, None, None],
        'observation_ids': ['session-a:0', 'session-a:1', 'session-b:0'],
        'frames': [0, 1, 0], 'sessions': ['session-a', 'session-a', 'session-b'],
        'segments': ['a:0', 'a:0', 'b:0'],
        'partitions': ['train', 'train', 'test'],
        'feature_names': ['nose_x', 'nose_y'],
    }
    artifact = FileArtifactStore(settings.ARTIFACT_ROOT).save(
        kind='datasets', artifact_id='browser-pose', value=loaded)
    dataset = Dataset.objects.create(name='Browser pose')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='json_records', status='ready',
        inventory={'feature_names': loaded['feature_names'], 'frame_count': 3},
        artifact_ref=artifact.to_dict())
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Pose study',
                                 dataset_revision=source)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.set_default_timeout(5000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'{live_server.url}/studies/{study.pk}/prepare/')
        page.get_by_role('button', name='Agregar: Centrar por media del entrenamiento').click()
        expect(page.locator('#preparation-steps')).to_contain_text(
            'Centrar por media del entrenamiento')
        page.get_by_role('button', name='Previsualizar sobre el dataset').click()
        expect(page.locator('#preparation-status')).to_contain_text('Muestra contigua lista')
        expect(page.locator('#preparation-preview')).to_be_visible()
        expect(page.locator('#preparation-preview')).to_contain_text('[-1,-5]')
        expect(page.locator('#preparation-stage-results')).to_contain_text(
            'Paso 1: Centrar por media del entrenamiento')
        expect(page.locator('#preparation-stage-results')).to_contain_text('[-1,-5]')
        page.get_by_role('button', name='Guardar receta').click()
        expect(page.get_by_role('heading', name='Recetas activas')).to_be_visible()
        page.get_by_role('button', name='Procesar y guardar un dataset').click()
        expect(page).to_have_url(f'{live_server.url}/studies/{study.pk}/jobs/')
        with ThreadPoolExecutor(max_workers=1) as database:
            preparation_job = database.submit(Job.objects.get, operation='prepare').result()
            database.submit(perform, str(preparation_job.pk)).result()
            preparation_job = database.submit(Job.objects.get, pk=preparation_job.pk).result()
        prepared_revision_id = preparation_job.result['prepared_dataset_revision_id']
        page.reload()
        expect(page.get_by_text(
            f'Dataset procesado · revisión {prepared_revision_id} · 3 observaciones.')).to_be_visible()
        page.get_by_role('link', name='Ver datos procesados').click()
        expect(page).to_have_url(
            f'{live_server.url}/studies/{study.pk}/data/?prepared_revision={prepared_revision_id}'
            '#prepared-dataset-result')
        result_preview = page.locator('#prepared-dataset-result')
        expect(result_preview).to_contain_text('nose_x')
        expect(result_preview).to_contain_text('-1,0')
        expect(result_preview).to_contain_text('-5,0')
        expect(page.locator('#prepared-dataset-stages')).to_contain_text(
            'Centrar por media del entrenamiento')
        expect(page.locator('#prepared-dataset-stages')).to_contain_text('session-a:0')
        page.get_by_role('link', name='Configurar un modelo con esta revisión').click()
        expect(page.locator('#id_dataset_revision_id')).to_have_value(str(prepared_revision_id))
        page.get_by_role('button', name='Guardar revisión del plan').click()
        expect(page).to_have_url(f'{live_server.url}/studies/{study.pk}/jobs/')
        with ThreadPoolExecutor(max_workers=1) as database:
            plan = database.submit(Revision.objects.get, study=study, kind='plan').result()
        assert plan.payload['steps'] == []
        assert plan.payload['dataset_revision_id'] == prepared_revision_id
        assert plan.payload['connector'] == 'prepared_artifact'
        assert errors == []
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_vame_native_recovery_and_historical_recipe_loader(live_server, settings):
    pytest.importorskip('rainstorm_thesis.plugin')
    import json
    from storm_studio.models import Dataset, DatasetRevision, Project, Study

    settings.STORM_PLUGINS = ('rainstorm_thesis.plugin',)
    dataset = Dataset.objects.create(name='VAME pose')
    bodyparts = [
        'nose', 'left_ear', 'right_ear', 'head', 'neck', 'body',
        'left_shoulder', 'right_shoulder', 'left_midside', 'right_midside',
        'left_hip', 'right_hip', 'tail_base', 'tail_mid', 'tail_end',
    ]
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'feature_names': [
            coordinate for part in bodyparts for coordinate in (f'{part}_x', f'{part}_y')
        ], 'frame_count': 3})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='VAME study',
                                 dataset_revision=source)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.goto(
            f'{live_server.url}/studies/{study.pk}/prepare/'
            '?required_step=pose.temporal_windows&required_model=vame_native')
        expect(page.get_by_role(
            'heading', name='No se inició la corrida de VAME nativo')).to_be_visible()
        page.get_by_role(
            'link', name='Cargar receta VAME nativo · pose_ego').click()
        expect(page.locator('#id_model')).to_have_value('vame_native')
        assert 'recipe_preset=vame_native_pose_ego' in page.url

        page.goto(f'{live_server.url}/studies/{study.pk}/flow/')
        expect(page.get_by_role(
            'heading', name='Recetas VAME recuperables de Tesis_Facu')).to_be_visible()
        page.get_by_role('link', name='Cargar esta receta').nth(0).click()
        expect(page.locator('#id_model')).to_have_value('vame_native')
        recovered_steps = json.loads(page.locator('#id_steps').input_value())
        assert [step['type'] for step in recovered_steps] == [
            'pose.select_coordinates', 'pose.likelihood_filter', 'pose.recenter',
            'pose.orient_coordinates', 'pose.temporal_windows',
        ]
        assert recovered_steps[2]['config']['center_bodypart'] == 'body'
        assert recovered_steps[3]['config']['target_angle_degrees'] == 90
        assert recovered_steps[4]['config']['offsets'] == list(range(-9, 11))
        native_config = json.loads(page.locator('#id_config').input_value())
        assert native_config['n_states'] == 50
        page.goto(
            f'{live_server.url}/studies/{study.pk}/flow/'
            '?recipe_preset=vame_official_ego_roi')
        expect(page.locator('#id_model')).to_have_value('vame_official')
        official_config = json.loads(page.locator('#id_config').input_value())
        assert official_config['config_kwargs']['time_window'] == 19
        assert json.loads(page.locator('#id_steps').input_value()) == []
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_pose_preparation_is_keyboard_operable_at_mobile_width(live_server, settings):
    pytest.importorskip('rainstorm_thesis.plugin')
    from storm_studio.models import Dataset, DatasetRevision, Project, Study

    settings.STORM_PLUGINS = ('rainstorm_thesis.plugin',)
    dataset = Dataset.objects.create(name='Accessible pose')
    source = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'feature_names': ['body_x', 'body_y', 'nose_x', 'nose_y']})
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Accessible study',
                                 dataset_revision=source)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.goto(f'{live_server.url}/studies/{study.pk}/prepare/')
        orient = page.get_by_role('button', name='Agregar: Alinear orientación')
        orient.focus()
        orient.press('Enter')
        angle = page.get_by_label('Ángulo objetivo (grados)')
        angle.fill('90')
        assert '"target_angle_degrees":90' in page.locator('#id_steps').input_value()
        policy = page.get_by_label('Frames con referencias coincidentes')
        policy.focus()
        policy.press('End')

        assert policy.input_value() == 'identity'
        assert '"degenerate_reference_policy":"identity"' in page.locator(
            '#id_steps').input_value()
        dimensions = page.evaluate('''() => ({
            width: document.documentElement.scrollWidth,
            viewport: window.innerWidth,
            activeLabel: document.activeElement.labels?.[0]?.textContent.trim() || ''
        })''')
        assert dimensions['width'] <= dimensions['viewport'], dimensions
        assert dimensions['activeLabel'].startswith('Frames con referencias coincidentes')
        browser.close()


@pytest.mark.django_db
def test_model_configuration_edits_nested_and_array_values_as_typed_data(
        client, monkeypatch):
    from storm.suite import Component, default_catalog
    from storm_studio import forms, services
    from storm_studio.models import Project, Study

    study = Study.objects.create(project=Project.objects.create(name='P'), name='Typed config')
    catalog = default_catalog()
    catalog.register(Component('example.typed_config', lambda config: None, (), {
        'type': 'object',
        'properties': {
            'pose_paths': {'type': 'array', 'default': [],
                           'description': 'Se completan desde las sesiones de pose registradas.'},
            'config_kwargs': {'type': 'object', 'default': {
                'max_epochs': 50, 'confidence': 0.6,
            }},
        },
    }))
    catalog.register(Component('example.pretrained', lambda config: None, ('infer',), {
        'type': 'object', 'properties': {},
    }))
    monkeypatch.setattr(forms, 'catalog', lambda: catalog)
    monkeypatch.setattr(services, 'catalog', lambda: catalog)
    response = client.get(f'/studies/{study.pk}/flow/')
    assert response.status_code == 200

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(response.content.decode())
        page.locator('#id_model').select_option('example.typed_config')

        paths = page.get_by_label('Pose paths')
        epochs = page.get_by_label('Max epochs')
        infer_option = page.locator('#id_operation option[value="infer"]')
        assert page.get_by_text('Se completan desde las sesiones de pose registradas.').is_visible()
        assert paths.evaluate('(element) => element.tagName') == 'TEXTAREA'
        assert epochs.get_attribute('type') == 'number'
        assert page.locator('#model-config-config_kwargs').get_attribute('class') == 'model-config-object'
        assert infer_option.is_disabled()
        paths.fill('["/data/session-a.h5"]')
        epochs.fill('12')
        config = page.locator('#id_config').input_value()
        assert config == '{"pose_paths":["/data/session-a.h5"],"config_kwargs":{"max_epochs":12,"confidence":0.6}}'
        page.locator('#id_model').select_option('example.pretrained')
        assert not infer_option.is_disabled()
        browser.close()


@pytest.mark.django_db
def test_inventory_pose_preview_tracks_selected_bodypart_without_crossing_segments(client):
    from storm_studio.models import Dataset, DatasetAsset, DatasetRevision, Project, Study

    dataset = Dataset.objects.create(name='Pose preview')
    pose = DatasetAsset.objects.create(
        dataset=dataset, role='pose', original_name='mouse-aDLC_pose.h5',
        relative_path='data_sources/mouse-aDLC_pose.h5', sha256='d' * 64,
        size_bytes=10, session_id='mouse-a')
    video = DatasetAsset.objects.create(
        dataset=dataset, role='video', original_name='mouse-a.mp4',
        relative_path='data_sources/mouse-a.mp4', sha256='c' * 64,
        size_bytes=10, session_id='mouse-a')
    revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', asset_ids=[pose.pk, video.pk],
        config={'fps': 30, 'pose_frame_base': 1,
                'video_frame_offsets': {str(video.pk): 5}}, status='ready',
        inventory={
            'frame_count': 3, 'feature_count': 4, 'session_count': 1,
            'segment_count': 2, 'labeled_frames': 0, 'missing_values': 0,
            'feature_names': ['nose_x', 'nose_y', 'tail_x', 'tail_y'],
            'sessions': [{'session_id': 'mouse-a', 'frames': 3, 'segments': 2}],
            'warnings': [], 'roi': [{
                'file': 'mouse-a_rois.json', 'session_id': 'mouse-a',
                'frame_shape': [800, 500],
                'rectangles': [{
                    'name': 'arena', 'center': [100, 80],
                    'width': 40, 'height': 20, 'angle': 15,
                }],
                'circles': [{'name': 'target', 'center': [300, 200], 'radius': 12}],
                'points': [{'name': 'feeder', 'center': [500, 250]}],
            }],
            'preview': [
                {'index': 0, 'observation_id': 'mouse-a:0', 'session_id': 'mouse-a',
                 'frame': 0, 'segment': 'clip-1', 'partition': 'train',
                 'features': [10.0, 30.0, 20.0, 40.0], 'target': None},
                {'index': 1, 'observation_id': 'mouse-a:1', 'session_id': 'mouse-a',
                 'frame': 1, 'segment': 'clip-1', 'partition': 'train',
                 'features': [20.0, 20.0, 30.0, 30.0], 'target': None},
                {'index': 2, 'observation_id': 'mouse-a:9', 'session_id': 'mouse-a',
                 'frame': 9, 'segment': 'clip-2', 'partition': 'train',
                 'features': [30.0, 10.0, 40.0, 20.0], 'target': None},
            ],
        })
    study = Study.objects.create(project=Project.objects.create(name='P'), name='Pose study',
                                 dataset_revision=revision)
    response = client.get(f'/studies/{study.pk}/data/')
    assert response.status_code == 200

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        html = response.content.decode().replace(
            '<script id="pose-motion-data" type="application/json">',
            '''<script>
              HTMLMediaElement.prototype.load = function() {};
            </script>
            <script id="pose-motion-data" type="application/json">''',
            1)
        page.set_content(html)
        page.evaluate("""() => {
          const video = document.getElementById('pose-preview-video');
          let currentTime = 0;
          Object.defineProperty(video, 'readyState', {configurable:true, value:1});
          Object.defineProperty(video, 'paused', {configurable:true, value:false});
          Object.defineProperty(video, 'seeking', {configurable:true, value:false});
          Object.defineProperty(video, 'currentTime', {
            configurable:true, get(){return currentTime;}, set(value){currentTime = Number(value);}
          });
        }""")

        assert page.locator('#pose-motion').count() == 1
        part_checks = page.locator('#pose-preview-bodyparts input[data-bodypart]')
        assert part_checks.count() == 2
        expect(page.locator('#pose-preview-all-bodyparts')).to_be_checked()
        assert page.locator('#pose-motion polyline[data-bodypart="nose"]').count() == 2
        assert page.locator('#pose-motion polyline[data-bodypart="tail"]').count() == 2
        page.locator('#pose-preview-bodyparts-summary').click()
        page.locator('#pose-preview-bodyparts input[data-bodypart="tail"]').uncheck()
        assert page.locator('#pose-motion polyline[data-bodypart="tail"]').count() == 0
        assert page.locator('#pose-motion polyline[data-bodypart="nose"]').count() == 2
        page.locator('#pose-preview-bodyparts input[data-bodypart="tail"]').check()
        assert page.locator('#pose-motion .roi-rectangle[data-roi-name="arena"]').count() == 1
        assert page.locator('#pose-motion .roi-circle[data-roi-name="target"]').count() == 1
        assert page.locator('#pose-motion .roi-point[data-roi-name="feeder"]').count() == 1
        assert page.locator('#pose-motion').get_attribute('viewBox') == '0 0 800 500'
        assert page.locator('#pose-preview-video').is_visible()
        assert f'/studies/{study.pk}/assets/{video.pk}/video/' in page.locator(
            '#pose-preview-video').get_attribute('src')
        page.locator('#pose-preview-video').evaluate(
            "video => video.dispatchEvent(new Event('error'))")
        assert page.locator('#pose-video-playback-status').text_content() == (
            'El navegador no pudo reproducir este video. La vista de pose sigue disponible; '
            'podés preparar una copia MP4 en segundo plano.')
        assert page.locator('#pose-video-proxy-form').is_visible()
        assert page.locator('#pose-video-proxy-form').get_attribute('action') == (
            f'/studies/{study.pk}/assets/{video.pk}/video/prepare/')
        assert page.locator('#pose-video-calibration').is_visible()
        page.locator('#pose-preview-frame').fill('1')
        expect(page.locator('#pose-motion-readout')).to_contain_text('frame 1')
        assert page.locator('#pose-preview-video').evaluate('(video) => video.currentTime') == pytest.approx(5 / 30)
        page.locator('#pose-calibration-video-frame').fill('13')
        page.locator('#pose-calibration-apply').click()
        assert page.locator(f'#id_asset_video_frame_offset_{video.pk}').input_value() == '13'
        expect(page.locator('#pose-calibration-status')).to_contain_text(
            'Guardar vínculos como revisión de datos')
        page.evaluate("""() => {
          const video = document.getElementById('pose-preview-video');
          video.currentTime = 4 / 30;
          video.dispatchEvent(new Event('timeupdate'));
        }""")
        expect(page.locator('#pose-motion-readout')).to_contain_text('frame 0')
        page.evaluate("""() => {
          const video = document.getElementById('pose-preview-video');
          video.currentTime = 13 / 30;
          video.dispatchEvent(new Event('timeupdate'));
        }""")
        expect(page.locator('#pose-motion-readout')).to_contain_text('frame 9')
        page.evaluate("""() => {
          const video = document.getElementById('pose-preview-video');
          Object.defineProperty(video, 'paused', {configurable:true, value:true});
          video.currentTime = 13 / 30;
          video.dispatchEvent(new Event('seeked'));
        }""")
        expect(page.locator('#pose-motion-readout')).to_contain_text('frame 9')
        assert page.locator('#pose-calibration-video-frame').input_value() == '13'
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_pose_preview_slider_loads_frames_outside_the_inventory_sample(
        live_server, settings, tmp_path):
    from storm_studio.models import Dataset, DatasetRevision, Job, Project, Revision, Study
    from storm_studio.pose_preview import write_pose_preview_store

    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    row_count = 1220
    data = {
        'inputs': [[float(index), float(index * 2)] for index in range(row_count)],
        'feature_names': ['nose_x', 'nose_y'],
        'frames': list(range(1200)) + list(range(20)),
        'video_frames': list(range(1200)) + list(range(20)),
        'sessions': ['mouse-a'] * 1200 + ['mouse-b'] * 20,
        'segments': (['clip-1' if index < 600 else 'clip-2' for index in range(1200)]
                     + ['mouse-b-segment'] * 20),
        'observation_ids': ([f'mouse-a:{index}' for index in range(1200)]
                            + [f'mouse-b:{index}' for index in range(20)]),
        'targets': [1 if index == 1100 else None for index in range(row_count)],
        'evaluation_mask': [index == 1100 for index in range(row_count)],
        'taxonomy': ['walk', 'grooming'],
    }
    preview_store = write_pose_preview_store(
        data, root=settings.ARTIFACT_ROOT, artifact_id='pose-preview-browser', chunk_size=128)
    dataset = Dataset.objects.create(name='Pose timeline')
    revision = DatasetRevision.objects.create(
        dataset=dataset, number=1, connector='dlc_h5', status='ready',
        inventory={'frame_count': row_count, 'feature_names': data['feature_names'],
                   'source_fingerprint': 'browser-fingerprint',
                   'preview': [{
                       'observation_id': 'mouse-a:0', 'session_id': 'mouse-a', 'frame': 0,
                       'video_frame': 0, 'segment': 'clip-1', 'features': [0.0, 0.0],
                   }],
                   'taxonomy': data['taxonomy'],
                   'sessions': [{'session_id': 'mouse-a', 'frames': 1200},
                                {'session_id': 'mouse-b', 'frames': 20}],
                   'pose_preview_store': preview_store})
    study = Study.objects.create(
        project=Project.objects.create(name='P'), name='Frame navigation',
        dataset_revision=revision)
    prediction_jobs = []
    for model, prediction, semantics in (
            ('vame_native', 4, 'Local motif IDs'),
            ('supervised_simple', 0.82, 'Positive-class probability')):
        plan = Revision.objects.create(study=study, kind='plan', payload={
            'dataset_revision_id': revision.pk, 'model': model,
        })
        prediction_jobs.append(Job.objects.create(
            revision=plan, status='completed', result={
                'model': model,
                'spec': {'dataset_revision_id': revision.pk},
                'data_fingerprint': 'browser-fingerprint',
                'capabilities': ['group'] if model == 'vame_native' else [],
                'resolved_data': {
                    **data, 'partitions': ['test'] * row_count,
                    'reserved_evaluation': [True] * row_count,
                },
                'indices': [1100], 'predictions': [prediction],
                'prediction_mask': [True],
                'output_metadata': {'semantics': semantics},
            }))
    annotation = Revision.objects.create(
        study=study, kind='annotations', parent=prediction_jobs[0].revision,
        payload={
            'job': str(prediction_jobs[0].pk),
            'data_fingerprint': 'browser-fingerprint',
            'taxonomy': ['walk', 'grooming'],
            'intervals': [{'start': 1100, 'stop': 1101, 'label': 'walk'}],
            'mapping': {'4': 'grooming'}, 'author': 'Researcher',
            'reason': 'Reviewed the synchronized video frame.',
        })

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(
            f'{live_server.url}/studies/{study.pk}/data/?predictions='
            f'{prediction_jobs[0].pk}&predictions={prediction_jobs[1].pk}')
        assert page.locator('#pose-preview-session option').all_text_contents() == [
            'mouse-a', 'mouse-b']
        slider = page.locator('#pose-preview-frame')
        expect(slider).to_have_attribute('max', '1199')
        page.locator('#pose-preview-session').select_option('mouse-b')
        expect(slider).to_have_attribute('max', '19')
        expect(page.locator('#pose-motion-readout')).to_contain_text('frame 0')
        expect(page.locator('#pose-label-readout')).to_contain_text(
            'Sin etiqueta de referencia válida')
        page.locator('#pose-preview-session').select_option('mouse-a')
        expect(slider).to_have_attribute('max', '1199')
        review_filter = page.locator('#pose-preview-review-filter')
        review_filter.select_option('reference')
        slider.fill('1099')
        page.locator('#pose-review-next').click()
        expect(slider).to_have_value('1100')
        expect(page.locator('#pose-motion-readout')).to_contain_text('frame 1100')
        expect(page.locator('#pose-motion-readout')).to_contain_text('(1100, 2200)')
        expect(page.locator('#pose-label-readout')).to_contain_text(
            'Etiqueta de referencia: grooming')
        expect(page.locator('#pose-prediction-readout')).to_contain_text(
            'vame_native')
        expect(page.locator('#pose-prediction-readout')).to_contain_text('4')
        expect(page.locator('#pose-prediction-readout')).to_contain_text(
            'supervised_simple')
        expect(page.locator('#pose-prediction-readout')).to_contain_text('0.82')
        expect(page.locator('#pose-run-annotation-readout')).to_contain_text(
            f'revisión humana {str(annotation.pk)[:8]}')
        expect(page.locator('#pose-run-annotation-readout')).to_contain_text(
            'intervalo: walk')
        expect(page.locator('#pose-run-annotation-readout')).to_contain_text(
            'interpretación: grooming')
        expect(page.locator('#pose-run-annotation-readout')).to_contain_text(
            'Reviewed the synchronized video frame.')
        assert page.locator('#pose-motion polyline[data-segment="clip-2"]').count() == 1
        assert page.locator('#pose-label-session').input_value() == 'mouse-a'
        assert page.locator('#pose-label-frame').input_value() == '1100'
        assert page.locator('#pose-label-observation-id').input_value() == 'mouse-a:1100'
        page.locator('#pose-label-correction').select_option('walk')
        page.locator('#pose-label-author').fill('Investigator')
        page.locator('#pose-label-reason').fill('Video confirms walking.')
        page.get_by_role('button', name='Guardar corrección como revisión').click()
        expect(page.locator('main')).to_contain_text('Corrección guardada como revisión')
        page.locator('#pose-preview-frame').fill('1100')
        expect(page.locator('#pose-label-readout')).to_contain_text(
            'Etiqueta de referencia: grooming')
        expect(page.locator('#pose-correction-readout')).to_contain_text(
            'Corrección vigente: walk')
        review_filter.select_option('correction')
        slider.fill('1099')
        expect(page.locator('#pose-motion-readout')).to_contain_text('frame 1099')
        page.locator('#pose-review-next').click()
        expect(slider).to_have_value('1100')
        page.locator('#pose-preview-session').select_option('mouse-b')
        slider.fill('7')
        expect(page.locator('#pose-motion-readout')).to_contain_text('frame 7')
        page.get_by_text('Guardar estado visual', exact=True).click()
        page.locator('#snapshot-form input[name="name"]').fill('Review filters')
        page.locator('#snapshot-form button').click()
        page.locator('section.card').filter(
            has_text='Review filters'
        ).get_by_role('button', name='Recuperar como nueva revisión').click()
        expect(page.locator('#pose-preview-review-filter')).to_have_value('correction')
        expect(page.locator('#pose-preview-session')).to_have_value('mouse-b')
        expect(slider).to_have_value('7')
        for job in prediction_jobs:
            expect(page.locator(
                f'#pose-prediction-selection input[value="{job.pk}"]')).to_be_checked()
        browser.close()
    saved_snapshot = Revision.objects.filter(
        study=study, kind='snapshot').latest('pk')
    assert saved_snapshot.payload['visual_state']['filters']['pose_timeline'] == (
        'correction')
    assert saved_snapshot.payload['visual_state']['pose_timeline'] == {
        'session_id': 'mouse-b', 'bodyparts': ['nose'], 'frame': 7,
    }
    assert set(saved_snapshot.payload['visual_state']['selected_predictions']) == {
        str(job.pk) for job in prediction_jobs}
    correction = Revision.objects.get(study=study, kind='label_corrections')
    assert correction.payload['corrections']['mouse-a:1100']['label'] == 'walk'
    assert correction.payload['corrections']['mouse-a:1100']['reason'] == (
        'Video confirms walking.')
