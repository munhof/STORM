import pytest


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('size', [(1920, 1080), (2560, 1440)])
def test_desktop_recipe_blocks_insert_reorder_preview_and_save(live_server, settings, tmp_path, size):
    from playwright.sync_api import sync_playwright, expect
    from storm.artifacts import FileArtifactStore
    from storm_studio.models import Dataset, DatasetRevision, Project, Study
    settings.ARTIFACT_ROOT = tmp_path / 'artifacts'
    loaded = {'inputs': [[0, 0, 3, 4], [0, 0, 6, 8]], 'targets': [None, None],
        'observation_ids': ['a:0', 'b:0'], 'frames': [0, 0], 'sessions': ['a', 'b'],
        'segments': ['a:0', 'b:0'], 'partitions': ['train', 'test'],
        'feature_names': ['nose_x', 'nose_y', 'body_x', 'body_y']}
    artifact = FileArtifactStore(settings.ARTIFACT_ROOT).save(kind='datasets', artifact_id='blocks', value=loaded)
    source = DatasetRevision.objects.create(dataset=Dataset.objects.create(name='Pose'), number=1,
        connector='json_records', status='ready', artifact_ref=artifact.to_dict(),
        inventory={'feature_names': loaded['feature_names'], 'frame_count': 2})
    study = Study.objects.create(project=Project.objects.create(name='Blocks'), name='Recipe', dataset_revision=source)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': size[0], 'height': size[1]})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(f'{live_server.url}/studies/{study.pk}/prepare/?editor=recipes')
        expect(page.locator('#recipe-workspace')).to_be_visible()
        assert page.locator('.recipe-library h3').evaluate('(node) => getComputedStyle(node).color') != 'rgb(255, 255, 255)'
        page.get_by_role('button', name='Agregar: Distancia entre puntos', exact=True).drag_to(
            page.get_by_role('button', name='Insertar en posición 1', exact=True))
        expect(page.locator('.recipe-block')).to_have_count(1)
        page.get_by_role('button', name='Insertar en posición 1', exact=True).click()
        page.get_by_role('button', name='Agregar: Multiplicar coordenadas', exact=True).click()
        expect(page.locator('.recipe-block')).to_have_count(2)
        page.get_by_label('Factor', exact=True).fill('2')
        assert page.locator('#id_steps').input_value().find('scale') < page.locator('#id_steps').input_value().find('features.distance')
        page.get_by_role('button', name='Bajar', exact=True).click()
        expect(page.locator('.recipe-block').first).to_contain_text('Distancia entre puntos')
        page.locator('.recipe-block').last.drag_to(page.get_by_role('button', name='Insertar en posición 1', exact=True))
        expect(page.locator('.recipe-block').first).to_contain_text('Multiplicar coordenadas')
        page.get_by_role('button', name='Previsualizar sobre el dataset').click()
        expect(page.locator('#preparation-preview')).to_contain_text('[10]')
        page.get_by_role('button', name='Guardar receta', exact=True).click()
        expect(page.locator('.recipe-block')).to_have_count(2)
        page.reload()
        expect(page.locator('.recipe-block').first).to_contain_text('Multiplicar coordenadas')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=f'/tmp/storm-recipe-blocks-{size[0]}.png', full_page=True)
        assert errors == []
        browser.close()
