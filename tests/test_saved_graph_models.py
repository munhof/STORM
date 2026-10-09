"""Recovery must preserve fitted state and keep held-out evaluation separate."""
import pytest
from storm.adapters import ModelAdapter
from storm.models import ModelOutput
from storm.pipeline import PipelineContext


class FittedOnly:
    def fit(self, *args):
        raise AssertionError('Recovered inference must not fit')

    def predict(self, inputs):
        return ModelOutput([7] * len(inputs))


def test_saved_inference_does_not_train_or_relabel_task():
    from storm.experiment_nodes import SavedModelHandle, _infer_saved
    context = PipelineContext(data=[1, 2], metadata={'partition': 'validation',
        'task': 'binary_classification', 'observation_ids': ['a', 'b']})
    handle = SavedModelHandle(FittedOnly(), ModelAdapter(), {'model': 'fitted'})
    result = _infer_saved({'model': handle, 'validation': context}, {})['predictions']
    assert result.data == [7, 7]
    assert result.metadata['task'] == 'binary_classification'
    assert result.state['model_inputs'] == [1, 2]


def test_saved_inference_cannot_bypass_reserved_test_action():
    from storm.experiment_nodes import SavedModelHandle, _infer_saved
    handle = SavedModelHandle(FittedOnly(), ModelAdapter(), {})
    context = PipelineContext(data=[1], metadata={'partition': 'test'})
    with pytest.raises(ValueError, match='validation'):
        _infer_saved({'model': handle, 'validation': context}, {})


def test_preview_does_not_deserialize_saved_model():
    from storm.experiment_nodes import experiment_registry
    from storm_studio.experiments import preview_node
    graph = {'version': '1', 'nodes': [{'id': 'saved', 'type': 'model.saved',
        'config': {'source': {'model': 'constant'}}}], 'edges': []}
    with pytest.raises(ValueError, match='Preview'):
        preview_node(graph, 'saved', experiment_registry())


def test_recovered_preparation_uses_original_training_statistics():
    from storm.experiment_nodes import SavedModelHandle, _prepare_saved
    graph = {'nodes': [{'id': 'source', 'type': 'data.inline'},
        {'id': 'center', 'type': 'transform.center'},
        {'id': 'bind', 'type': 'adapter.select', 'config': {'data': 'data', 'targets': 'targets'}},
        {'id': 'model', 'type': 'model.constant'}], 'edges': [
        {'source': 'source.train', 'target': 'center.train'},
        {'source': 'source.validation', 'target': 'center.validation'},
        {'source': 'center.validation', 'target': 'bind.context'},
        {'source': 'bind.context', 'target': 'model.validation'}]}
    fitted = {'center': {'train': PipelineContext(state={'center': 2})}}
    handle = SavedModelHandle(FittedOnly(), ModelAdapter(),
        {'source_graph': graph, 'model_node': 'model'}, fitted)
    context = PipelineContext(data=[200], targets=[5], metadata={
        'partition': 'validation', 'observation_ids': ['new']})
    result = _prepare_saved({'model': handle, 'context': context}, {})['context']
    assert result.data == [198]
    assert result.state['center'] == 2
    assert result.targets == [5]
    assert fitted['center']['train'].state == {'center': 2}


def test_recovered_preparation_refuses_unknown_fitted_transform():
    from storm.experiment_nodes import SavedModelHandle, _prepare_saved
    graph = {'nodes': [{'id': 'unknown', 'type': 'transform.custom'},
        {'id': 'model', 'type': 'model.constant'}], 'edges': [
        {'source': 'unknown.context', 'target': 'model.validation'}]}
    handle = SavedModelHandle(FittedOnly(), ModelAdapter(),
        {'source_graph': graph, 'model_node': 'model'}, {})
    with pytest.raises(ValueError, match='replay'):
        _prepare_saved({'model': handle, 'context': PipelineContext(data=[1])}, {})


def test_recovery_rejects_changed_model_version_before_loading(tmp_path):
    from storm.experiment_nodes import _load_saved_model
    from storm.suite import default_catalog
    catalog = default_catalog()
    catalog.experiment_artifact_root = tmp_path
    with pytest.raises(ValueError, match='version'):
        _load_saved_model({}, {'source': {'model': 'constant', 'model_version': 'obsolete',
            'bundle': True, 'config': {'value': 1}, 'capabilities': ['infer']}}, catalog)


def test_trainable_model_is_not_a_pretrained_bundle(tmp_path):
    from storm.experiment_nodes import _load_saved_model
    from storm.suite import default_catalog
    catalog = default_catalog()
    catalog.experiment_artifact_root = tmp_path
    with pytest.raises(ValueError, match='bundle'):
        _load_saved_model({}, {'source': {'model': 'constant', 'model_version': '1',
            'bundle': True, 'config': {'value': 1}, 'capabilities': ['infer']}}, catalog)


def test_fitted_graph_recovery_roundtrip(tmp_path):
    from storm.artifacts import FileArtifactStore
    from storm.experiments import ExperimentExecutor
    from storm.experiment_nodes import experiment_registry
    from storm.suite import default_catalog
    from tests.test_experiments import tabular_graph
    catalog = default_catalog()
    catalog.experiment_artifact_root = tmp_path
    executor = ExperimentExecutor(experiment_registry(catalog))
    original = executor.run(tabular_graph())
    assert original.status == 'complete'
    ref = FileArtifactStore(tmp_path).save(kind='outputs', artifact_id='source', value=original.outputs)
    source = {'model': 'mean_regressor', 'model_version': '1', 'model_node': 'model',
        'source_graph': original.resolved, 'outputs_ref': ref.to_dict()}
    graph = {'version': '1', 'nodes': [tabular_graph()['nodes'][0],
        {'id': 'saved', 'type': 'model.saved', 'config': {'source': source}},
        {'id': 'prepare', 'type': 'adapter.saved_preparation'},
        {'id': 'infer', 'type': 'model.infer_saved'}], 'edges': [
        {'source': 'saved.model', 'target': 'prepare.model'},
        {'source': 'data.validation', 'target': 'prepare.context'},
        {'source': 'saved.model', 'target': 'infer.model'},
        {'source': 'prepare.context', 'target': 'infer.validation'}]}
    recovered = executor.run(graph)
    assert recovered.status == 'complete', recovered.records
    assert recovered.outputs['prepare']['context'].data == [98]
    assert recovered.outputs['infer']['predictions'].data == original.outputs['model']['predictions'].data


def test_unknown_training_provenance_is_exploratory():
    from storm.experiment_nodes import SavedModelHandle, _infer_saved
    handle = SavedModelHandle(FittedOnly(), ModelAdapter(), {'config': {'training_population': 'unknown'}})
    context = PipelineContext(data=[1], metadata={'partition': 'validation'})
    result = _infer_saved({'model': handle, 'validation': context}, {})['predictions']
    assert result.metadata['exploratory'] is True


def test_recovered_context_keeps_alignment_and_isolates_changes():
    from storm.experiment_nodes import SavedModelHandle, _saved_context
    original = PipelineContext(data=[[1]], targets=[2], metadata={'partition': 'validation', 'observation_ids': ['s:1']})
    handle = SavedModelHandle(FittedOnly(), ModelAdapter(), {}, {'bound': {'context': original}})
    recovered = _saved_context({'model': handle}, {'node': 'bound', 'port': 'context'})['context']
    recovered.data[0][0] = 10
    assert original.data == [[1]]
    assert recovered.targets == [2]
    assert recovered.metadata['observation_ids'] == ['s:1']


def test_recovered_context_refuses_reserved_partition():
    from storm.experiment_nodes import SavedModelHandle, _saved_context
    handle = SavedModelHandle(FittedOnly(), ModelAdapter(), {}, {'bound': {'context': PipelineContext(metadata={'partition': 'test'})}})
    with pytest.raises(ValueError, match='reserved'):
        _saved_context({'model': handle}, {'node': 'bound', 'port': 'context'})


def test_graph_records_separate_training_and_inference_timing():
    from storm.experiments import ExperimentExecutor
    from storm.experiment_nodes import experiment_registry
    from tests.test_experiments import tabular_graph
    result=ExperimentExecutor(experiment_registry()).run(tabular_graph())
    timing=result.outputs['model']['predictions'].metadata['timing']
    assert timing['training_seconds'] >= 0
    assert timing['inference_seconds'] >= 0
