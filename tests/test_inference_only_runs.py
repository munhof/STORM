import pytest
from storm import DataRef, Dataset, FileArtifactStore, MetricRegistry, ModelOutput, ModelRegistry, RunEngine, RunSpec
from storm.metrics.classic import register_classic_metrics


class FrozenModel:
    def __init__(self, config):
        self.config=config
    def fit(self, inputs, targets=None):
        raise AssertionError('inference must not call fit')
    def predict(self, inputs):
        return ModelOutput([0 for _ in inputs], {'semantics':'binary class'})


class FittedOffset:
    def __init__(self, config):
        self.config = config
        self.fit_count = 0

    def fit(self, inputs, targets=None):
        self.fit_count = len(inputs)

    def predict(self, inputs):
        return ModelOutput([value + self.fit_count for value in inputs])


class DeclaredBinary:
    def __init__(self, config):
        self.config = config

    def fit(self, inputs, targets=None):
        pass

    def predict(self, inputs):
        return ModelOutput([1 for _ in inputs], {
            'semantics': 'binary behavior prediction',
            'task': 'binary_classification',
            'category_mapping': {'0': 'rest', '1': 'approach'},
            'category_mapping_version': 'benchmark-v1',
            'negative_output_meaning': 'rest',
            'output_meaning': 'approach',
        })


def engine(tmp_path):
    models=ModelRegistry(); models.register('frozen',FrozenModel)
    metrics=MetricRegistry(); register_classic_metrics(metrics)
    return RunEngine(models=models,metrics=metrics,artifacts=FileArtifactStore(tmp_path))


def spec(operation):
    return RunSpec('run','frozen',{},tags={'operation':operation})


def test_imported_model_runs_without_fitting_and_metrics_use_only_valid_labels(tmp_path):
    run=engine(tmp_path).train(study_id='study',data_ref=DataRef('d','hash'),
       dataset=Dataset([[1],[2],[3]],[0,None,1],{'evaluation_mask':[True,False,True]}),
       spec=spec('infer'),metric_names=['accuracy'])
    assert run.load_output().predictions == [0,0,0]
    assert run.metrics == {'accuracy':.5}
    assert run.tags['operation']=='infer'
    assert run.tags['metric_indices']==[0,2]


def test_inference_only_with_no_valid_labels_has_no_ranking_metrics(tmp_path):
    run=engine(tmp_path).train(study_id='study',data_ref=DataRef('d','hash'),
       dataset=Dataset([[1],[2]],[None,None],{'evaluation_mask':[False,False]}),
       spec=spec('infer'),metric_names=['accuracy'])
    assert run.metrics == {}
    assert run.tags['evaluation_status']=='inspection_only_no_valid_labels'


def test_training_operation_still_calls_fit(tmp_path):
    with pytest.raises(AssertionError,match='inference must not call fit'):
        engine(tmp_path).train(study_id='study',data_ref=DataRef('d','hash'),
          dataset=Dataset([[1],[2]],[0,1]),spec=spec('train'),metric_names=[])


def test_unknown_operation_fails_before_model_execution(tmp_path):
    with pytest.raises(ValueError,match='operation'):
        engine(tmp_path).train(study_id='study',data_ref=DataRef('d','hash'),
          dataset=Dataset([[1]],[0]),spec=spec('magic'),metric_names=[])


def test_results_are_not_marked_rankable_when_study_has_no_metrics(tmp_path):
    run=engine(tmp_path).train(study_id='study',data_ref=DataRef('d','hash'),
       dataset=Dataset([[1]],[0]),spec=spec('infer'),metric_names=[])
    assert run.tags['evaluation_status']=='metrics_not_requested'


def test_saved_model_applies_to_all_observations_in_a_new_dataset(tmp_path):
    from storm.suite import Component, default_catalog, execute

    catalog = default_catalog()
    catalog.register(Component('fitted_offset', FittedOffset, ('train', 'infer'), {
        'type': 'object', 'properties': {},
    }))
    source = execute({
        'model': 'fitted_offset', 'config': {}, 'steps': [{'type': 'center'}],
        'metrics': [],
        'data': {'inputs': [2, 4, 8], 'targets': [0, 0, 0], 'train': [0, 1], 'test': [2]},
    }, tmp_path, 'source-run', catalog)

    applied = execute({
        'model': 'fitted_offset', 'config': {}, 'operation': 'infer',
        'steps': [{'type': 'center'}], 'metrics': [],
        'data': {'inputs': [10, 20], 'train': [], 'test': [0, 1]},
    }, tmp_path, 'applied-run', catalog, inference_from=source)

    assert applied['predictions'] == [9, 19]
    assert applied['indices'] == [0, 1]
    assert applied['model_ref'] == source['model_ref']
    assert applied['source_execution'] == source['execution_id']

    with pytest.raises(ValueError, match='preparation is incompatible'):
        execute({
            'model': 'fitted_offset', 'config': {}, 'operation': 'infer',
            'steps': [{'type': 'scale', 'factor': 2}], 'metrics': [],
            'data': {'inputs': [10, 20], 'train': [], 'test': [0, 1]},
        }, tmp_path, 'incompatible-apply', catalog, inference_from=source)


def test_imported_inference_only_model_uses_registered_rows_without_a_test_split(tmp_path):
    from storm.suite import Component, default_catalog, execute

    catalog = default_catalog()
    catalog.register(Component('imported_frozen', FrozenModel, ('infer',), {
        'type': 'object', 'properties': {},
    }))

    result = execute({
        'model': 'imported_frozen', 'config': {}, 'operation': 'infer',
        'steps': [], 'metrics': [],
        'data': {'inputs': [10, 20, 30], 'train': [0, 1, 2], 'test': []},
    }, tmp_path, 'registered-inference-only', catalog)

    assert result['indices'] == [0, 1, 2]
    assert result['predictions'] == [0, 0, 0]
    assert result['partition'] == 'test'


def test_saved_binary_model_does_not_score_a_different_taxonomy(tmp_path):
    from storm.suite import Component, default_catalog, execute

    catalog = default_catalog()
    catalog.register(Component('declared_binary', DeclaredBinary, ('train', 'infer'), {
        'type': 'object', 'properties': {},
    }))
    source = execute({
        'model': 'declared_binary', 'config': {}, 'connector': 'json_records',
        'steps': [], 'metrics': ['accuracy'],
        'data': {
            'inputs': [1, 2, 3], 'targets': [0, 1, 1], 'taxonomy': ['rest', 'approach'],
            'train': [0, 1], 'test': [2],
        },
    }, tmp_path, 'binary-source', catalog)

    applied = execute({
        'model': 'declared_binary', 'config': {}, 'connector': 'json_records',
        'operation': 'infer',
        'steps': [], 'metrics': ['accuracy'],
        'data': {
            'inputs': [8, 9], 'targets': [0, 11],
            'taxonomy': [f'category-{index}' for index in range(12)],
            'train': [], 'test': [0, 1],
        },
    }, tmp_path, 'binary-apply', catalog, inference_from=source)

    assert applied['metrics'] == {}
    assert applied['metric_definitions'] == []
    assert applied['evaluation_status'] == 'metrics_not_requested'

    compatible = execute({
        'model': 'declared_binary', 'config': {}, 'connector': 'json_records',
        'operation': 'infer',
        'steps': [], 'metrics': ['accuracy'],
        'data': {
            'inputs': [8, 9], 'targets': ['rest', 'approach'],
            'taxonomy': ['rest', 'approach'], 'train': [], 'test': [0, 1],
        },
    }, tmp_path, 'binary-compatible-apply', catalog, inference_from=source)
    assert compatible['metrics'] == {'accuracy': 0.5}
