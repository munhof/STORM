import sys
from pathlib import Path

from storm.suite import default_catalog, execute, Component
from storm.visualization import VisualizationManager, VisualizationRequest, VisualizationSpec
from storm.models import ModelOutput


def test_external_package_extends_all_registries(tmp_path):
    sys.path.insert(0, str(Path(__file__).parents[1] / 'examples'))
    from storm_suite_plugin import register
    catalog = default_catalog()
    register(catalog)
    result = execute({'connector': 'example.inline', 'model': 'example.offset',
        'config': {'offset': 2}, 'steps': [{'type': 'example.absolute'}],
        'metrics': ['example.count'], 'data': {'inputs': [-1, -2], 'targets': [3, 4],
        'train': [0], 'test': [1]}}, tmp_path, 'external', catalog)
    assert result['predictions'] == [4]
    assert result['metrics'] == {'example.count': 1}
    rendered = VisualizationManager(catalog.visualizations).render(
        VisualizationSpec('example.count'), VisualizationRequest(output=ModelOutput([4])))
    assert rendered.content == '1'


def test_inference_only_adapter_is_not_forced_to_train():
    sys.path.insert(0, str(Path(__file__).parents[1] / 'examples'))
    from storm_suite_plugin.process_model import ProcessModel
    catalog = default_catalog()
    catalog.register(Component('example.process', ProcessModel, ('infer',), {'properties': {}}))
    assert catalog.build('example.process', {}).predict([2, 4]).predictions == [4, 8]


def test_inference_only_adapter_can_execute_and_persist(tmp_path):
    sys.path.insert(0, str(Path(__file__).parents[1] / 'examples'))
    from storm_suite_plugin.process_model import ProcessModel
    catalog = default_catalog()
    catalog.register(Component('example.process', ProcessModel, ('infer',), {'properties': {}}))
    result = execute({'operation': 'infer', 'model': 'example.process',
                      'data': {'inputs': [2, 4], 'train': [], 'test': [0, 1]}},
                     tmp_path, 'inference-only', catalog)
    assert result['predictions'] == [4, 8]


def test_generic_records_and_categorical_targets(tmp_path):
    from storm.suite import infer
    result = execute({'connector': 'json_records', 'model': 'identity', 'metrics': ['accuracy'],
        'data': {'inputs': ['A', 'B', 'C'], 'targets': ['A', 'B', 'C'], 'train': [0], 'test': [1, 2]}},
        tmp_path, 'categorical')
    assert result['metrics'] == {'accuracy': 1.0}
    assert infer(result, ['D'], tmp_path) == ['D']
