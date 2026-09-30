import pytest


class MaskedOutputModel:
    def __init__(self, config):
        self.prediction_mask = config.get('prediction_mask', [False, True])

    def fit(self, _inputs, _targets=None):
        return self

    def predict(self, inputs):
        assert len(inputs) == 2
        from storm import ModelOutput

        return ModelOutput(
            [None, 2], metadata={'prediction_mask': self.prediction_mask}
        )


class NativePreparationOnlyModel:
    supports_pipeline_steps = False

    def __init__(self, _config):
        pass

    def predict(self, inputs):
        from storm import ModelOutput

        return ModelOutput(list(inputs))


def test_training_does_not_fit_on_evaluation_data(tmp_path):
    from storm.suite import execute, infer
    spec = {'model': 'mean_regressor', 'config': {}, 'seed': 42,
            'data': {'inputs': [0, 1, 2], 'targets': [2, 4, 100],
                     'train': [0, 1], 'test': [2]}, 'steps': []}
    result = execute(spec, tmp_path, 'run-1')
    assert result['predictions'] == [3.0]
    assert result['metrics']['mse'] == 9409.0
    assert infer(result, [5, 6], tmp_path) == [3.0, 3.0]


def test_rejects_overlapping_partitions(tmp_path):
    from storm.suite import execute
    with pytest.raises(ValueError, match='overlap'):
        execute({'model': 'identity', 'config': {}, 'data': {
            'inputs': [1, 2], 'targets': [1, 2], 'train': [0], 'test': [0]}}, tmp_path, 'bad')


def test_constraints_are_used_and_unsupported_ones_rejected(tmp_path):
    from storm.suite import execute
    spec = {'model': 'constrained_groups', 'config': {}, 'data': {
        'inputs': [0, 10, 20], 'train': [0, 1, 2], 'test': []},
        'constraints': [[0, 2]]}
    result = execute(spec, tmp_path, 'groups')
    assert result['predictions'][0] == result['predictions'][2]
    spec['model'] = 'identity'
    with pytest.raises(ValueError, match='constraints'):
        execute(spec, tmp_path, 'unsupported')


def test_center_is_fitted_on_train_only(tmp_path):
    from storm.suite import execute, infer
    result = execute({'model': 'identity', 'data': {
        'inputs': [2, 4, 100], 'targets': [2, 4, 100], 'train': [0, 1], 'test': [2]},
        'steps': [{'type': 'center'}]}, tmp_path, 'center')
    assert result['fitted_steps'] == [{'type': 'center', 'value': 3}]
    assert result['predictions'] == [97]
    assert infer(result, [13], tmp_path) == [10]


def test_model_that_owns_preparation_rejects_generic_pipeline_steps(tmp_path):
    from storm.suite import Component, default_catalog, execute

    catalog = default_catalog()
    catalog.register(Component(
        'native_preparation_only', NativePreparationOnlyModel, ('infer',),
        {'type': 'object', 'properties': {}},
    ))

    with pytest.raises(ValueError, match='does not accept generic pipeline steps'):
        execute({
            'operation': 'infer', 'model': 'native_preparation_only',
            'steps': [{'type': 'scale', 'factor': 2}],
            'data': {'inputs': [1, 2], 'train': [], 'test': [0, 1]},
        }, tmp_path, 'native-preparation', catalog)


def test_query_uncertainty_preserves_pool_and_seed():
    from storm.learning import select_samples
    assert select_samples([0, 1, 3], count=2, seed=4) == select_samples([0, 1, 3], count=2, seed=4)
    assert select_samples([0, 1, 3], count=1, scores={0: .9, 1: .2, 3: .7}, strategy='uncertainty') == [1]
    with pytest.raises(ValueError):
        select_samples([0], strategy='uncertainty')


def test_alignment_rejects_duplicate_and_missing_ids():
    from storm.learning import ObservationAlignment
    with pytest.raises(ValueError):
        ObservationAlignment(('a', 'a'), (0, 1))
    with pytest.raises(ValueError):
        ObservationAlignment(('a',), (0, 1))


def test_validation_partition_and_external_inference(tmp_path):
    from storm.suite import execute, evaluate
    result = execute({'model': 'mean_regressor', 'data': {
        'inputs': [1, 2, 3], 'targets': [2, 8, 100], 'train': [0], 'validation': [1], 'test': [2]}},
        tmp_path, 'validation')
    assert result['indices'] == [1]
    assert result['partition'] == 'validation'
    evaluated = evaluate(result, [3], [100], tmp_path)
    assert evaluated['metrics']['mse'] == 9604


def test_execution_persists_observation_alignment_and_rejects_duplicate_ids(tmp_path):
    from storm.suite import execute, validate_data

    data = {'inputs': [1, 2, 3], 'targets': [1, 2, 3],
            'observation_ids': ['row-a', 'row-b', 'row-c'],
            'train': [0], 'test': [1, 2]}
    result = execute({'model': 'identity', 'data': data}, tmp_path, 'aligned')
    assert result['alignment'] == {
        'source_ids': ['row-a', 'row-b', 'row-c'],
        'output_indices': [1, 2],
    }
    with pytest.raises(ValueError, match='observation_ids'):
        validate_data({**data, 'observation_ids': ['row-a', 'row-a', 'row-c']})


def test_metric_descriptor_is_persisted(tmp_path):
    from storm.suite import execute
    result = execute({'model': 'identity', 'metrics': ['mae'], 'data': {
        'inputs': [1, 2], 'targets': [1, 3], 'train': [0], 'test': [1]}}, tmp_path, 'metric-contract')
    assert result['metric_definitions'] == [{'name': 'mae', 'version': '1', 'direction': 'minimize'}]


def test_aligned_transform_can_filter_only_with_explicit_source_mapping(tmp_path):
    from storm.pipeline import PipelineStep
    from storm.suite import Component, default_catalog, execute

    class KeepLast(PipelineStep):
        step_type = 'keep_last'

        def process(self, context):
            context.data = context.data[1:]
            context.metadata['observation_indices'] = context.metadata['observation_indices'][1:]
            return context

    catalog = default_catalog()
    catalog.steps.register(KeepLast)
    result = execute({'model': 'identity', 'data': {
        'inputs': [1, 2, 3], 'targets': [1, 2, 3], 'train': [0], 'test': [1, 2]},
        'steps': [{'type': 'keep_last'}]}, tmp_path, 'filtered', catalog)
    assert result['indices'] == [2]
    assert result['predictions'] == [3]
    assert result['alignment']['output_indices'] == [2]


def test_aligned_transform_keeps_empty_evaluation_partitions_empty():
    from storm.pipeline import PipelineStep
    from storm.suite import default_catalog, transform_aligned

    class RequiresRows(PipelineStep):
        step_type = 'requires_rows'

        def process(self, context):
            context.data[0]
            return context

    catalog = default_catalog()
    catalog.steps.register(RequiresRows)
    fitted = [{'type': 'requires_rows', 'value': {}}]

    prepared, fitted_steps, indices = transform_aligned(
        [], [], [{'type': 'requires_rows', 'config': {}}],
        learned=fitted, catalog=catalog)

    assert prepared == []
    assert fitted_steps == fitted
    assert indices == []


def test_catalog_validates_required_types_ranges_and_defaults():
    from storm.suite import Catalog, Component

    catalog = Catalog()
    catalog.register(Component('configured', lambda config: config, ('infer',), {
        'type': 'object',
        'required': ['kind'],
        'properties': {
            'kind': {'type': 'string', 'enum': ['a', 'b']},
            'limit': {'type': 'integer', 'minimum': 1, 'default': 2},
        },
    }))

    assert catalog.normalize('configured', {'kind': 'a'}) == {'kind': 'a', 'limit': 2}
    for config, message in [
        ({}, 'required'),
        ({'kind': 'c'}, 'enum'),
        ({'kind': 'a', 'limit': 1.5}, 'integer'),
        ({'kind': 'a', 'limit': 0}, 'minimum'),
        ({'kind': 'a', 'extra': True}, 'Unknown'),
    ]:
        with pytest.raises(ValueError, match=message):
            catalog.normalize('configured', config)


def test_catalog_rejects_unsupported_schema_keywords():
    from storm.suite import Catalog, Component

    catalog = Catalog()
    catalog.register(Component('bad-schema', lambda config: config, (), {
        'type': 'object', 'properties': {'value': {'type': 'date'}}}))
    with pytest.raises(ValueError, match='unsupported'):
        catalog.normalize('bad-schema', {'value': 'today'})


def test_catalog_keeps_property_descriptions_available_for_configuration_ui():
    from storm.suite import Catalog, Component

    catalog = Catalog()
    catalog.register(Component('described.config', lambda config: config, (), {
        'type': 'object',
        'properties': {
            'pose_paths': {'type': 'array', 'default': [],
                           'description': 'Se toman de las sesiones de pose registradas.'},
        },
    }))

    assert catalog.normalize('described.config', {}) == {'pose_paths': []}


def test_metrics_exclude_predictions_outside_model_output_mask(tmp_path):
    from storm.suite import Component, default_catalog, execute

    catalog = default_catalog()
    catalog.register(Component(
        'masked-output', MaskedOutputModel, ('train',), {
            'type': 'object',
            'properties': {'prediction_mask': {'type': 'array'}},
        }
    ))
    result = execute({
        'model': 'masked-output', 'metrics': ['mae'], 'data': {
            'inputs': [0, 1, 2], 'targets': [0, 2, 6],
            'train': [0], 'test': [1, 2],
        },
    }, tmp_path, 'masked-output', catalog)

    assert result['predictions'] == [None, 2]
    assert result['prediction_mask'] == [False, True]
    assert result['metric_indices'] == [2]
    assert result['metrics']['mae'] == 4

    no_valid_predictions = execute({
        'model': 'masked-output', 'config': {'prediction_mask': [False, False]},
        'metrics': ['mae'], 'data': {
            'inputs': [0, 1, 2], 'targets': [0, 2, 6],
            'train': [0], 'test': [1, 2],
        },
    }, tmp_path, 'masked-no-predictions', catalog)
    assert no_valid_predictions['evaluation_status'] == 'inspection_only_no_valid_predictions'
