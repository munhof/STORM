"""Separate data loading and model preparation contracts, with legacy hook bridges."""
from dataclasses import dataclass
from typing import Callable

from storm.pipeline import PipelineContext
from storm.models import ModelOutput


@dataclass(frozen=True)
class DataAdapter:
    name: str
    version: str
    schema: dict
    load: Callable
    contents: dict
    outputs: tuple[str, ...] = ('context',)
    preview: Callable | None = None

    def __call__(self, config):
        context = self.load(config)
        if self.outputs == ('context',):
            if not isinstance(context, PipelineContext):
                raise TypeError('Data adapters must return PipelineContext')
        elif (not isinstance(context, dict) or set(context) != set(self.outputs)
                or not all(isinstance(value, PipelineContext) for value in context.values())):
            raise TypeError('Partitioned data adapters must return their declared contexts')
        return context


class ModelAdapter:
    name = 'fit_predict'
    version = '1'

    def prepare_training(self, model, context):
        self._bind(model, context)
        return context.data, context.targets

    def fit(self, model, context):
        inputs, targets = self.prepare_training(model, context)
        model.fit(inputs, targets)
        return model

    def prepare_inference(self, model, context):
        self._bind(model, context)
        return context.data

    def predict(self, model, context):
        inputs = self.prepare_inference(model, context)
        if callable(getattr(model, 'predict_with_context', None)):
            output = model.predict_with_context(inputs, self._legacy_data(context),
                context.metadata.get('observation_indices', list(range(len(inputs)))))
        else:
            output = model.predict(inputs)
        if not isinstance(output, ModelOutput) or len(output.predictions) != len(inputs):
            raise ValueError('Model output must align with its bound observations')
        return output

    @staticmethod
    def _legacy_data(context):
        return context.state.get('legacy_data', {'inputs': context.data,
            'targets': context.targets, **context.metadata})

    def _bind(self, model, context):
        if callable(getattr(model, 'bind_context', None)):
            model.bind_context(context)
        if callable(getattr(model, 'bind_data', None)):
            model.bind_data(self._legacy_data(context),
                context.metadata.get('observation_indices', list(range(len(context.data)))))


class GroupModelAdapter(ModelAdapter):
    name = 'fit_predict_groups'

    def fit(self, model, context):
        inputs, _ = self.prepare_training(model, context)
        model.fit_predict(inputs, ())
        return model
