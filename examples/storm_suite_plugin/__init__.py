"""Example external package: registration never requires editing STORM."""
from storm.suite import Component
from storm.models import ModelOutput
from storm.pipeline import PipelineStep
from storm.visualization import Visualization, VisualizationResult


class OffsetModel:
    def __init__(self, config):
        self.offset = config.get('offset', 1)

    def fit(self, inputs, targets=None):
        return self

    def predict(self, inputs):
        return ModelOutput([x + self.offset for x in inputs])


class AbsoluteStep(PipelineStep):
    step_type = 'example.absolute'

    def process(self, context):
        context.data = [abs(x) for x in context.data]
        return context


class CountMetric:
    def evaluate(self, *, dataset, output, model):
        return len(output.predictions)


class CountView(Visualization):
    visualization_type = 'example.count'

    def render(self, request):
        return VisualizationResult(str(len(request.output.predictions)), 'text/plain')


def register(catalog):
    catalog.register(Component('example.offset', OffsetModel, ('train', 'infer'),
                               {'type': 'object', 'properties': {'offset': {'type': 'number'}}}))
    catalog.connectors['example.inline'] = lambda source: dict(source)
    catalog.steps.register(AbsoluteStep)
    catalog.metrics.register('example.count', CountMetric())
    catalog.visualizations.register(CountView)
