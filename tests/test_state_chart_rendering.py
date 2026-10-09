import re
import pytest
from django.template.loader import render_to_string
from django.utils.translation import override
from storm_studio.views import _report_visuals


def many_states():
    predictions = [state for state in range(45) for _ in range(2 ** (state % 9))]
    count = len(predictions)
    return _report_visuals({'predictions': predictions, 'indices': list(range(count)),
        'capabilities': ['group'], 'resolved_data': {'inputs': [0] * count,
        'sessions': ['s'] * count, 'frames': list(range(count))}})


def test_spanish_svg_coordinates_use_decimal_points():
    with override('es'):
        html = render_to_string('storm_studio/report_visuals.html', {'report_visuals': many_states()})
    attributes = re.findall(r'\b(?:x|y|width|height)="([^"]+)"', html)
    assert attributes
    assert not [value for value in attributes if ',' in value]


def test_each_state_remains_visible_and_histogram_bars_do_not_overlap():
    visuals = many_states()
    assert [row['label'] for row in visuals['predictions']] == [f'Estado {i}' for i in range(45)]
    for group in visuals['state_duration_histogram']['bins']:
        for first, second in zip(group['bars'], group['bars'][1:]):
            assert first['x'] + first['width'] <= second['x']


@pytest.mark.parametrize('size', [(1920, 1080), (2560, 1440)])
def test_desktop_many_states_remain_readable(size):
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    import storm_studio
    static = Path(storm_studio.__file__).parent / 'static/storm_studio'
    with override('es'):
        report = render_to_string('storm_studio/report_visuals.html', {'report_visuals': many_states(),
            'prediction_chart_id': 'result-distribution-chart', 'prediction_title_id': 'distribution-title'})
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': size[0], 'height': size[1]})
        page.set_content('<style>' + (static / 'studio.css').read_text() + (static / 'context-visuals.css').read_text() + '</style><main><div id="context-results">' + report + '</div></main>')
        distribution = page.locator('#result-distribution-chart')
        assert distribution.locator('text').first.evaluate('(el) => el.getBoundingClientRect().height') >= 12
        wrapper = page.locator('#state-transition-matrix').locator('..')
        assert wrapper.evaluate('(el) => el.clientHeight <= 480 && el.scrollWidth > el.clientWidth')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('#state-duration-histogram rect').evaluate_all('(els) => els.every(el => !el.getAttribute("x").includes(",") && Number.isFinite(Number(el.getAttribute("x"))))')
        page.screenshot(path=f'/tmp/storm-states-readable-{size[0]}.png', full_page=True)
        browser.close()
