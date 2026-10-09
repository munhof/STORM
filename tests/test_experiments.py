from copy import deepcopy
import pytest
from storm.pipeline import PipelineContext


def registry():
    from storm.experiments import ExperimentRegistry, NodeOperation
    result = ExperimentRegistry()
    schema = {'type': 'object', 'properties': {}}
    result.register(NodeOperation('source', '1', {}, {'context': 'context'}, schema,
        lambda inputs, config: {'context': PipelineContext(data=[1], targets=[2],
            state={'value': [3]}, artifacts={'a': 'ref'})}))
    def mutate(inputs, config):
        ctx = inputs['context']
        ctx.data.append(2)
        ctx.state['value'].append(4)
        return {'context': ctx}
    result.register(NodeOperation('mutate', '1', {'context': 'context'},
                                  {'context': 'context'}, schema, mutate))
    def fail(inputs, config):
        raise ValueError('broken calibration')
    result.register(NodeOperation('fail', '1', {'context': 'context'},
                                  {'context': 'context'}, schema, fail))
    return result


def graph():
    return {'version': '1', 'nodes': [
        {'id': 'source', 'type': 'source'}, {'id': 'left', 'type': 'mutate'},
        {'id': 'right', 'type': 'mutate'}], 'edges': [
        {'source': 'source.context', 'target': 'left.context'},
        {'source': 'source.context', 'target': 'right.context'}]}


def test_dag_isolates_every_mutable_context_field_and_records_provenance():
    from storm.experiments import ExperimentExecutor
    result = ExperimentExecutor(registry()).run(graph())
    assert result.status == 'complete'
    assert result.outputs['source']['context'].data == [1]
    assert result.outputs['left']['context'].data == [1, 2]
    assert result.outputs['right']['context'].state == {'value': [3, 4]}
    assert result.outputs['right']['context'].targets == [2]
    assert result.outputs['right']['context'].artifacts == {'a': 'ref'}
    assert result.records['left']['parents'] == ['source.context']


def test_failed_node_blocks_only_descendants():
    from storm.experiments import ExperimentExecutor
    spec = graph()
    spec['nodes'][1]['type'] = 'fail'
    spec['nodes'].append({'id': 'child', 'type': 'mutate'})
    spec['edges'].append({'source': 'left.context', 'target': 'child.context'})
    result = ExperimentExecutor(registry()).run(spec)
    assert result.status == 'partial'
    assert result.records['left']['status'] == 'failed'
    assert result.records['child']['status'] == 'blocked'
    assert result.records['right']['status'] == 'completed'


def test_scientific_fingerprint_ignores_layout_and_node_list_order():
    from storm.experiments import scientific_fingerprint
    original = graph()
    moved = deepcopy(original)
    moved['visual'] = {'positions': {'source': [10, 20]}, 'zoom': 2}
    moved['nodes'].reverse()
    assert scientific_fingerprint(original) == scientific_fingerprint(moved)
    moved['nodes'][0]['config'] = {'factor': 2}
    assert scientific_fingerprint(original) != scientific_fingerprint(moved)


def test_all_sweep_configurations_validate_before_any_execution():
    from storm.experiments import expand_study, ExperimentValidationError
    spec = {'graph': graph(), 'variants': [{'id': 'a', 'parameters': {}},
            {'id': 'b', 'parameters': {}}], 'seeds': [2, 3], 'sweep': {}}
    runs = expand_study(spec, registry())
    assert [(r['variant_id'], r['seed']) for r in runs] == [('a', 2), ('a', 3), ('b', 2), ('b', 3)]
    assert len({r['run_id'] for r in runs}) == 4
    spec['sweep'] = {'left.factor': [1, 'bad']}
    with pytest.raises(ExperimentValidationError):
        expand_study(spec, registry())


def test_invalid_graph_is_rejected_before_loading_data():
    from storm.experiments import ExperimentExecutor, ExperimentValidationError
    spec = graph()
    spec['edges'].append({'source': 'left.context', 'target': 'source.context'})
    with pytest.raises(ExperimentValidationError):
        ExperimentExecutor(registry()).run(spec)


def test_noncopyable_resources_require_explicit_adapter_strategy():
    from storm.experiments import ExperimentExecutor, NodeOperation
    class Resource:
        def __deepcopy__(self, memo):
            raise TypeError('resource cannot be copied')
    reg = registry()
    reg.register(NodeOperation('resource', '1', {}, {'context': 'context'},
        {'type': 'object', 'properties': {}},
        lambda inputs, config: {'context': PipelineContext(data=Resource())}))
    spec = graph()
    spec['nodes'][0]['type'] = 'resource'
    result = ExperimentExecutor(reg).run(spec)
    assert result.records['left']['status'] == 'failed'
    assert 'copied' in result.records['left']['error']


def test_temporal_alignment_has_no_extrapolation_and_earlier_ties():
    from storm.alignment import align_temporal, ObservationSeries, TimeAxis
    axis = TimeAxis('camera', 's', 'session-start', 'session')
    reference = ObservationSeries(('a', 'b', 'c'), ('s',)*3, ('train',)*3,
                                  (0.5, 1., 3.), [0, 0, 0], axis)
    source = ObservationSeries(('x', 'y'), ('s',)*2, ('train',)*2,
                               (0., 2.), [0., 20.], axis)
    near = align_temporal(reference, source, method='nearest', tolerance=2.)
    assert near.values == [0., 0., None]
    assert near.valid == [True, True, False]
    linear = align_temporal(reference, source, method='linear', tolerance=2.)
    assert linear.values == [5., 10., None]
    assert linear.parents == [('x', 'y'), ('x', 'y'), ()]


def test_temporal_alignment_rejects_ambiguous_duplicates_and_clock_mismatch():
    from storm.alignment import align_temporal, ObservationSeries, TimeAxis
    axis = TimeAxis('camera', 's', 'session-start', 'session')
    ref = ObservationSeries(('a',), ('s',), ('train',), (1.,), [0], axis)
    duplicate = ObservationSeries(('b', 'c'), ('s',)*2, ('train',)*2, (1., 1.), [2, 3], axis)
    with pytest.raises(ValueError, match='Duplicate'):
        align_temporal(ref, duplicate)
    other = ObservationSeries(('b',), ('s',), ('train',), (1.,), [2],
                              TimeAxis('other', 's', 'session-start', 'session'))
    with pytest.raises(ValueError, match='clock|Clock'):
        align_temporal(ref, other)
    with pytest.raises(ValueError, match='tolerance'):
        align_temporal(ref, ref, method='nearest')


def test_alignment_never_interpolates_across_sessions_or_partitions():
    from storm.alignment import align_temporal, align_identity, ObservationSeries, TimeAxis
    axis = TimeAxis('camera', 's', 'session-start', 'session')
    ref = ObservationSeries(('a',), ('s1',), ('validation',), (1.,), [0], axis)
    src = ObservationSeries(('b', 'c'), ('s1', 's2'), ('train', 'validation'),
                            (0., 2.), [0., 20.], axis)
    assert align_temporal(ref, src, method='linear', tolerance=3.).valid == [False]
    permuted = ObservationSeries(('b', 'a'), ('s1',)*2, ('validation',)*2,
                                 (0., 1.), [5, 9], axis)
    assert align_identity(ref, permuted).values == [9]


def tabular_graph():
    return {'version': '1', 'nodes': [
        {'id': 'data', 'type': 'data.inline', 'config': {'inputs': [1., 3., 100., 999.],
          'targets': [2., 4., 7., 900.], 'ids': ['a', 'b', 'c', 'd'],
          'sessions': ['s1', 's1', 's2', 's3'],
          'partitions': ['train', 'train', 'validation', 'test']}},
        {'id': 'center', 'type': 'transform.center'},
        {'id': 'train_inputs', 'type': 'adapter.select', 'config': {'data': 'data', 'targets': 'targets'}},
        {'id': 'valid_inputs', 'type': 'adapter.select', 'config': {'data': 'data', 'targets': 'targets'}},
        {'id': 'model', 'type': 'model.mean_regressor'},
        {'id': 'metric', 'type': 'metric.mse'}], 'edges': [
        {'source': 'data.train', 'target': 'center.train'},
        {'source': 'data.validation', 'target': 'center.validation'},
        {'source': 'center.train', 'target': 'train_inputs.context'},
        {'source': 'center.validation', 'target': 'valid_inputs.context'},
        {'source': 'train_inputs.context', 'target': 'model.train'},
        {'source': 'valid_inputs.context', 'target': 'model.validation'},
        {'source': 'model.predictions', 'target': 'metric.context'}]}


def test_tabular_graph_fits_on_train_only_and_preserves_bindings():
    from storm.experiment_nodes import experiment_registry
    from storm.experiments import ExperimentExecutor
    result = ExperimentExecutor(experiment_registry()).run(tabular_graph())
    assert result.status == 'complete', result.records
    assert result.outputs['center']['train'].data == [-1., 1.]
    assert result.outputs['center']['validation'].data == [98.]
    assert result.outputs['model']['predictions'].data == [3.]
    assert result.outputs['metric']['metric']['value'] == 16.
    assert result.outputs['model']['predictions'].metadata['observation_ids'] == ['c']
    assert result.outputs['train_inputs']['context'].state['bindings'] == {'data': 'data', 'targets': 'targets'}


def test_same_model_variants_execute_independently():
    from storm.experiment_nodes import experiment_registry, run_study
    spec = tabular_graph()
    spec['nodes'][4]['type'] = 'model.constant'
    runs = run_study({'graph': spec, 'variants': [
        {'id': 'low', 'parameters': {'model.value': 2}},
        {'id': 'high', 'parameters': {'model.value': 9}}]}, experiment_registry())
    assert [r['result'].outputs['model']['predictions'].data for r in runs] == [[2], [9]]


def test_context_schema_is_descriptive_and_does_not_serialize_values():
    from storm.pipeline.context import ContentDescriptor
    context = PipelineContext(data=object(), schema={'data': ContentDescriptor(
        dtype='float64', dimensions=('observation', 'feature'), units='mm',
        granularity='frame', identity='metadata.observation_ids', time='metadata.times')})
    summary = context.summarize()
    assert summary['schema']['data']['units'] == 'mm'
    assert summary['data'] == {'type': 'object'}


def test_model_adapter_passes_full_context_to_contextual_models():
    from storm.adapters import ModelAdapter
    from storm.models import ModelOutput
    class Contextual:
        def bind_context(self, context):
            assert context.state['offset'] == 4
            assert context.artifacts['report'] == 'ref'
        def fit(self, inputs, targets):
            assert targets == [2]
        def predict(self, inputs):
            return ModelOutput(inputs)
    context = PipelineContext(data=[1], targets=[2], state={'offset': 4}, artifacts={'report': 'ref'})
    adapter = ModelAdapter()
    model = Contextual()
    adapter.fit(model, context)
    assert adapter.predict(model, context).predictions == [1]


def test_missing_model_binding_and_join_tolerance_fail_preflight():
    from storm.experiment_nodes import experiment_registry
    spec = tabular_graph()
    spec['edges'][4]['source'] = 'center.train'
    assert any(p.code == 'experiment.binding' for p in experiment_registry().validate(spec))
    spec = {'version': '1', 'nodes': [tabular_graph()['nodes'][0],
        {'id': 'join', 'type': 'join.align', 'config': {'method': 'nearest'}}],
        'edges': [{'source': 'data.train', 'target': 'join.reference'},
                  {'source': 'data.train', 'target': 'join.source'}]}
    assert any(p.field == 'config.tolerance' for p in experiment_registry().validate(spec))


def test_legacy_entry_points_and_study_use_shared_executor(tmp_path, monkeypatch):
    from storm.experiments import ExperimentExecutor
    from storm.suite import execute
    from storm.studies import Study
    calls = []
    original = ExperimentExecutor.run_legacy
    def spy(*args, **kwargs):
        calls.append(kwargs['semantics'])
        return original(*args, **kwargs)
    monkeypatch.setattr(ExperimentExecutor, 'run_legacy', spy)
    result = execute({'model': 'identity', 'data': {'inputs': [1, 2], 'targets': [1, 2],
                     'train': [0], 'test': [1]}}, tmp_path, 'legacy')
    assert result['predictions'] == [2]
    assert calls == ['suite.partitioned.legacy']
    study = Study.from_experiment({'graph': tabular_graph()})
    assert study.run()[0]['result'].status == 'complete'


def test_frozen_selection_evaluates_test_without_refitting_and_is_tamper_evident():
    from storm.experiment_nodes import experiment_registry
    from storm.experiments import ExperimentExecutor
    from storm.selection import freeze_selection, evaluate_reserved
    registry = experiment_registry()
    run = ExperimentExecutor(registry).run(tabular_graph())
    selection = freeze_selection(run, 'model')
    result = evaluate_reserved(selection)
    assert result['partition'] == 'test'
    assert result['predictions'] == [3.]
    assert result['targets'] == [900.]
    assert result['observation_ids'] == ['d']
    selection.graph['nodes'][0]['config']['new_parameter'] = 1
    with pytest.raises(ValueError, match='frozen'):
        evaluate_reserved(selection)


def test_temporal_windows_keep_explicit_parent_identities():
    from storm.experiment_nodes import experiment_registry
    from storm.experiments import ExperimentExecutor
    spec = tabular_graph()
    spec['nodes'] = [spec['nodes'][0], {'id': 'windows', 'type': 'transform.windows',
        'config': {'offsets': [0, 1]}}]
    spec['edges'] = [{'source': 'data.train', 'target': 'windows.context'}]
    result = ExperimentExecutor(experiment_registry()).run(spec)
    context = result.outputs['windows']['context']
    assert context.data == [[1., 3.]]
    assert context.targets == [2.]
    assert context.metadata['observation_ids'] == ['a']
    assert context.state['window_parents'] == [['a', 'b']]


def test_grouping_models_keep_clustering_semantics_in_the_experiment_catalog():
    from storm.experiment_nodes import experiment_registry
    from storm.suite import Component, default_catalog
    from storm.models import ModelOutput
    class Groups:
        def __init__(self, config):
            pass
        def fit_predict(self, inputs, constraints=()):
            return ModelOutput([0] * len(inputs))
        def predict(self, inputs):
            return ModelOutput([0] * len(inputs), {'task': 'clustering'})
    catalog = default_catalog()
    catalog.register(Component('groups', Groups, ('group', 'infer'), {'type': 'object', 'properties': {}}))
    assert 'model.groups' in experiment_registry(catalog).describe()


def test_plugin_config_validator_rejects_all_invalid_runs_before_execution():
    from storm.experiments import NodeOperation, ExperimentValidationError, expand_study
    reg = registry()
    def validate(config):
        if config['factor'] % 2:
            raise ValueError('factor must be even')
    reg.register(NodeOperation('even', '1', {'context': 'context'}, {'context': 'context'},
        {'type': 'object', 'properties': {'factor': {'type': 'integer', 'default': 2}}},
        lambda inputs, config: inputs, validate_config=validate))
    spec = graph()
    spec['nodes'][1]['type'] = 'even'
    with pytest.raises(ExperimentValidationError, match='even'):
        expand_study({'graph': spec, 'sweep': {'left.factor': [2, 3]}}, reg)


def test_metric_uses_model_input_features_and_valid_output_masks(tmp_path):
    from storm.experiment_nodes import experiment_registry
    from storm.experiments import ExperimentExecutor
    from storm.suite import default_catalog, Component
    from storm.models import ModelOutput
    class Masked:
        def __init__(self, config): pass
        def fit(self, inputs, targets): pass
        def predict(self, inputs):
            return ModelOutput([9., 100.], {'prediction_mask': [True, False], 'task': 'regression'})
    class FeatureMetric:
        compatible_task = 'regression'
        def evaluate(self, *, dataset, output, model):
            assert dataset.inputs == [98.]
            assert dataset.targets == [7.]
            assert output.predictions == [9.]
            return 2.
    catalog = default_catalog()
    catalog.register(Component('masked', Masked, ('train', 'infer'), {'type': 'object', 'properties': {}}))
    catalog.metrics.register('features', FeatureMetric())
    spec = tabular_graph()
    source = spec['nodes'][0]['config']
    source['partitions'][-1] = 'validation'
    spec['nodes'][4]['type'] = 'model.masked'
    spec['nodes'][5]['type'] = 'metric.features'
    result = ExperimentExecutor(experiment_registry(catalog)).run(spec)
    assert result.status == 'complete', result.records
    assert result.outputs['metric']['metric']['observation_ids'] == ['c']


def test_model_declared_preparation_is_checked_before_training():
    from storm.contracts import ModelInputContract
    from storm.suite import Component, default_catalog
    from storm.experiment_nodes import experiment_registry
    from storm.experiment_examples import tabular_graph
    from storm.experiments import ExperimentExecutor
    called = []
    class NeedsWindows:
        def fit(self, inputs, targets):
            called.append('trained')
        def predict(self, inputs):
            from storm.models import ModelOutput
            return ModelOutput(predictions=[0] * len(inputs))
    catalog = default_catalog()
    catalog.register(Component('needs_windows', lambda config: NeedsWindows(), ('train', 'infer'),
        {'type': 'object', 'properties': {}}, input_contract=ModelInputContract(
            input_type='temporal_pose', preparation='external', required_steps=('pose.temporal_windows',))))
    spec = tabular_graph()
    model = next(n for n in spec['nodes'] if n['type'].startswith('model.'))
    model.update(type='model.needs_windows', config={})
    result = ExperimentExecutor(experiment_registry(catalog)).run(spec)
    assert result.records[model['id']]['status'] == 'failed'
    assert 'pose.temporal_windows' in result.records[model['id']]['error']
    assert called == []


def test_graph_model_descriptor_keeps_internal_architecture_separate():
    from storm.suite import default_catalog
    from storm.experiment_nodes import experiment_registry
    catalog = default_catalog()
    component = catalog.get('constant')
    from dataclasses import replace
    catalog.register(replace(component, name='constant_architecture', descriptor={'graph': {'field': 'architecture_graph', 'default': {'version': '1', 'nodes': [], 'edges': []}}}))
    descriptor = experiment_registry(catalog).describe()['model.constant_architecture']
    assert descriptor['descriptor']['graph']['field'] == 'architecture_graph'
    assert descriptor['inputs'] == {'train': 'context', 'validation': 'context'}


def test_partitioned_data_adapter_registry_preserves_bounded_preview():
    from storm.adapters import DataAdapter
    from storm.suite import default_catalog
    from storm.experiment_nodes import experiment_registry
    from storm.experiments import ExperimentExecutor
    catalog = default_catalog()
    def load(config):
        raise AssertionError('Full source must not be loaded for preview')
    catalog.data_adapters['partitioned'] = DataAdapter('partitioned', '1',
        {'type': 'object', 'properties': {}}, load, {'units': 'cm'},
        outputs=('train', 'validation'), preview=lambda config: {
            p: PipelineContext(data=[1], metadata={'partition': p}) for p in ('train', 'validation')})
    registry = experiment_registry(catalog)
    result = ExperimentExecutor(registry).run({'version': '1', 'nodes': [
        {'id': 'source', 'type': 'data.partitioned'}], 'edges': []}, preview=True)
    assert result.status == 'complete'
    assert result.outputs['source']['validation'].metadata['partition'] == 'validation'
    assert registry.describe()['data.partitioned']['descriptor']['contents']['units'] == 'cm'
