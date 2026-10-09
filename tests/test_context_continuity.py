from storm.models import ModelOutput
from storm.pipeline import PipelineContext, PipelineStep
from storm.suite import default_catalog, transform_aligned


class WriteContext(PipelineStep):
    step_type = 'write_context'

    def process(self, context):
        context.targets = [10, 20]
        context.state['calibration'] = {'offset': 4}
        context.artifacts['report'] = 'artifact://report'
        context.metadata['units'] = 'mm'
        return context


class ReadContext(PipelineStep):
    step_type = 'read_context'

    def process(self, context):
        assert context.targets == [10, 20]
        assert context.state['calibration'] == {'offset': 4}
        assert context.artifacts['report'] == 'artifact://report'
        assert context.metadata['units'] == 'mm'
        return context


def test_suite_preserves_the_complete_context_between_steps():
    catalog = default_catalog()
    catalog.steps.register(WriteContext)
    catalog.steps.register(ReadContext)
    output, _, indices = transform_aligned(
        [1, 2], [0, 1], [{'type': 'write_context'}, {'type': 'read_context'}], catalog=catalog)
    assert output == [1, 2]
    assert indices == [0, 1]


def test_context_is_available_to_callers_and_checkpoint_recovery():
    catalog = default_catalog()
    catalog.steps.register(WriteContext)
    catalog.steps.register(ReadContext)
    context = PipelineContext(dataset_id='measurements')
    checkpoints = []
    steps = [{'type': 'write_context'}, {'type': 'read_context'}]
    transform_aligned([1, 2], [0, 1], steps, catalog=catalog,
                      pipeline_context=context, checkpoint_callback=checkpoints.append)
    assert context.targets == [10, 20]
    assert context.dataset_id == 'measurements'
    recovered = PipelineContext()
    transform_aligned([1, 2], [0, 1], steps, catalog=catalog,
                      pipeline_context=recovered, resume_state=checkpoints[0])
    assert recovered.state == context.state
    assert recovered.artifacts == context.artifacts
    assert recovered.targets == context.targets


class ContextModel:
    def __init__(self, config):
        pass
    def bind_context(self, context):
        assert context.state['changed'] is True
    def fit(self, inputs, targets):
        assert targets == [12]
    def predict(self, inputs):
        return ModelOutput([13] * len(inputs))

def test_suite_model_receives_context_and_transformed_targets(tmp_path):
    from storm.suite import Component, execute
    from storm.models import ModelOutput
    class TargetStep(PipelineStep):
        step_type = 'targets_step'
        def process(self, context):
            assert context.targets is not None
            context.targets = [v + 10 for v in context.targets]
            context.state['changed'] = True
            return context
    catalog = default_catalog()
    catalog.steps.register(TargetStep)
    catalog.register(Component('context_model', ContextModel, ('train', 'infer'), {'properties': {}}))
    result = execute({'model': 'context_model', 'steps': [{'type': 'targets_step'}],
        'data': {'inputs': [1, 2], 'targets': [2, 3], 'train': [0], 'test': [1]}},
        tmp_path, 'context', catalog)
    assert result['predictions'] == [13]
    assert result['metrics']['mse'] == 0


def test_checkpoint_preserves_descriptors_and_step_trace():
    from storm.pipeline.context import ContentDescriptor
    descriptor = ContentDescriptor(dtype='float64', units='mm')
    original = PipelineContext(schema={'data': descriptor})
    checkpoints = []
    steps = [{'type': 'scale', 'factor': 2}, {'type': 'scale', 'factor': 3}]
    transform_aligned([1], [0], steps, pipeline_context=original, checkpoint_callback=checkpoints.append)
    recovered = PipelineContext()
    transform_aligned([1], [0], steps, pipeline_context=recovered, resume_state=checkpoints[0])
    assert recovered.schema == original.schema
    assert len(recovered.executions) == len(original.executions) == 2
