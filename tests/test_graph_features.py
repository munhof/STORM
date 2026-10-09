import pytest
from storm.experiment_nodes import experiment_registry
from storm.pipeline import PipelineContext
from storm.pipeline.context import ContentDescriptor


def test_distance_features_preserve_identity_and_context():
    registry = experiment_registry()
    assert 'features.distance' in registry.operations
    context = PipelineContext(data=[[0, 0, 3, 4], [1, 2, 1, 2]], targets=[1, 0],
        metadata={'observation_ids': ['a', 'b'], 'units': 'cm'},
        state={'mask': [True, True]}, artifacts={'source': 'pose'},
        schema={'data': ContentDescriptor('number', units='cm', time='metadata.times')})
    output = registry.operations['features.distance'].execute({'context': context},
        {'first': [0, 1], 'second': [2, 3], 'name': 'body_distance'})['context']
    assert output.data == [[5.0], [0.0]]
    assert output.targets == [1, 0]
    assert output.metadata['observation_ids'] == ['a', 'b']
    assert output.metadata['feature_names'] == ['body_distance']
    assert output.schema['data'].units == 'cm'
    assert output.schema['data'].time == 'metadata.times'
    assert output.state['mask'] == [True, True]
    assert output.artifacts == {'source': 'pose'}


def test_window_features_aggregate_each_observation_without_mixing_windows():
    registry = experiment_registry()
    assert 'features.window_statistics' in registry.operations
    context = PipelineContext(data=[[[1, 2], [3, 6]], [[100, 20], [100, 20]]],
        metadata={'feature_names': ['x', 'y'], 'units': 'cm'},
        state={'window_parents': [['a', 'b'], ['c', 'd']]})
    output = registry.operations['features.window_statistics'].execute(
        {'context': context}, {'statistic': 'mean'})['context']
    assert output.data == [[2., 4.], [100., 20.]]
    assert output.metadata['feature_names'] == ['x_mean', 'y_mean']
    assert output.state['window_parents'] == [['a', 'b'], ['c', 'd']]


@pytest.mark.parametrize('values', [[[0, 1]], [[0, 1, float('nan'), 3]]])
def test_distance_rejects_missing_or_nonfinite_coordinates(values):
    registry = experiment_registry()
    assert 'features.distance' in registry.operations
    with pytest.raises(ValueError):
        registry.operations['features.distance'].execute({'context': PipelineContext(data=values)},
            {'first': [0, 1], 'second': [2, 3], 'name': 'distance'})


def test_feature_graph_preview_and_execution_agree():
    from storm.experiments import ExperimentExecutor
    from storm_studio.experiments import preview_node
    graph = {'version': '1', 'nodes': [
        {'id': 'pose', 'type': 'data.inline', 'config': {
            'inputs': [[0, 0, 3, 4], [1, 2, 1, 2]], 'ids': ['a', 'b'],
            'sessions': ['train_session', 'validation_session'],
            'partitions': ['train', 'validation'], 'units': 'cm'}},
        {'id': 'distance', 'type': 'features.distance', 'config': {
            'first': [0, 1], 'second': [2, 3], 'name': 'nose_body'}}],
        'edges': [{'source': 'pose.train', 'target': 'distance.context'}]}
    registry = experiment_registry()
    assert not registry.validate(graph)
    result = ExperimentExecutor(registry).run(graph)
    assert result.status == 'complete'
    assert result.outputs['distance']['context'].data == [[5.0]]
    preview = preview_node(graph, 'distance', registry)
    assert preview['outputs']['context']['rows'] == [[5.0]]


def test_feature_extractors_are_executable_recipe_steps():
    from storm.suite import default_catalog
    catalog = default_catalog()
    step = catalog.steps.build('features.distance', {'first': [0, 1], 'second': [2, 3], 'name': 'distance'})
    context = PipelineContext(data=[[0, 0, 3, 4]], targets=[1], metadata={'observation_ids': ['frame:0']})
    assert step.process(context).data == [[5.0]]
    assert context.targets == [1]
    assert context.metadata['observation_ids'] == ['frame:0']
    windows = catalog.steps.build('features.window_statistics', {'statistic': 'mean'})
    assert windows.process(PipelineContext(data=[[[1, 3], [5, 9]]])).data == [[3.0, 6.0]]


def test_recipe_scale_accepts_coordinate_vectors_before_feature_extraction():
    from storm.suite import default_catalog
    catalog = default_catalog()
    context = PipelineContext(data=[[0, 0, 3, 4]])
    catalog.steps.build('scale', {'factor': 2}).process(context)
    assert context.data == [[0, 0, 6, 8]]
    assert catalog.steps.build('features.distance', {}).process(context).data == [[10.0]]


@pytest.mark.parametrize('offsets,expected_count,dimensions', [([0], 2, ('observations', 12)), ([0, 2], 0, ('observations', 2, 12))])
def test_supervised_features_describe_shape_and_do_not_bridge_missing_frames(offsets, expected_count, dimensions):
    from storm.feature_nodes import rainstorm_supervised
    parts = ['a', 'b', 'c', 'd', 'e', 'f']
    context = PipelineContext(data=[list(range(12)), list(range(12))],
        metadata={'feature_names': [f'{p}_{axis}' for p in parts for axis in ('x', 'y')],
            'observation_ids': ['a', 'c'], 'sessions': ['s', 's'], 'segments': ['s', 's'],
            'frames': [0, 2], 'partitions': ['validation', 'validation']})
    result = rainstorm_supervised({'context': context}, {'bodyparts': parts, 'offsets': offsets})['context']
    assert len(result.data) == expected_count
    assert result.schema['data'].dimensions == dimensions
