"""Application services shared by Python clients and Studio.

The original Study API remains supported. These services introduce explicitly
partitioned execution and capability-based model construction.
"""
from dataclasses import asdict, dataclass
from copy import deepcopy
from math import isfinite
import random
from typing import Callable

from storm.contracts import ModelInputContract, _materialized_steps, require_valid_plan
from storm.artifacts import ArtifactRef, FileArtifactStore
from storm.config import fingerprint, json_compatible
from storm.models import ModelOutput
from storm.testing.models import ConstantModel, IdentityModel, MeanRegressor
from storm.testing.online import OnlineMean
from storm.pipeline import StepRegistry, PipelineContext, PipelineRunner, PipelineStep
from storm.testing.steps import ScaleStep
from storm.metrics import MetricRegistry
from storm.metrics.classic import register_classic_metrics
from storm.runs import Dataset
from storm.visualization import VisualizationRegistry
from storm.learning import ObservationAlignment


@dataclass(frozen=True)
class Component:
    name: str
    builder: Callable
    capabilities: tuple[str, ...]
    schema: dict
    version: str = '1'
    input_contract: ModelInputContract | None = None
    descriptor: dict | None = None
    config_validator: Callable | None = None


class Catalog:
    def __init__(self):
        self._components = {}
        self.steps = StepRegistry()
        self.metrics = MetricRegistry()
        self.visualizations = VisualizationRegistry()
        self.connectors = {
            'numeric_json': lambda data: data,
            'json_records': lambda data: data,
            'prepared_artifact': lambda data: data,
        }
        self.preparation_resolver = None
        self.seeders = []
        self.recipe_presets = {}
        self.dataset_presets = {}

    def register(self, component):
        if component.name in self._components:
            raise ValueError('Duplicate component')
        self._components[component.name] = component

    def register_recipe_preset(self, preset):
        """Register a serializable model-and-preparation recipe for Studio."""
        if not isinstance(preset, dict) or not isinstance(preset.get('id'), str):
            raise ValueError('Recipe presets require a string id')
        if preset['id'] in self.recipe_presets:
            raise ValueError('Duplicate recipe preset')
        self.recipe_presets[preset['id']] = dict(preset)

    def register_dataset_preset(self, preset):
        """Register a bundled data source that Studio can copy into a study."""
        if (not isinstance(preset, dict) or not isinstance(preset.get('id'), str)
                or not isinstance(preset.get('label'), str)):
            raise ValueError('Dataset presets require string id and label fields')
        if preset['id'] in self.dataset_presets:
            raise ValueError('Duplicate dataset preset')
        self.dataset_presets[preset['id']] = dict(preset)

    def get(self, name):
        return self._components[name]

    def describe(self):
        return [{'name': c.name, 'version': c.version, 'capabilities': c.capabilities,
                 'schema': deepcopy(c.schema), 'descriptor': deepcopy(c.descriptor),
                 'descriptor_fingerprint': fingerprint({'schema': c.schema, 'descriptor': c.descriptor,
                     'version': c.version, 'input_contract': asdict(c.input_contract) if c.input_contract else None}),
                 'input_contract': (asdict(c.input_contract)
                     if c.input_contract is not None else None)} for c in self._components.values()]

    def validate(self, name, config):
        self.normalize(name, config)
        return self.get(name)

    def normalize(self, name, config):
        """Validate and materialize a component configuration.

        The supported schema is deliberately small and serializable. Keeping this
        operation in the engine lets Django forms and Python callers share the
        same contract without importing web code.
        """
        component = self.get(name)
        if not isinstance(config, dict):
            raise ValueError('Model configuration must be an object')
        schema = component.schema or {'type': 'object', 'properties': {}}
        if schema.get('type', 'object') != 'object':
            raise ValueError('Component schema must describe an object')
        supported = {'type', 'properties', 'required', 'additionalProperties'}
        if set(schema) - supported:
            raise ValueError('unsupported schema keyword')
        properties = schema.get('properties', {})
        if not isinstance(properties, dict):
            raise ValueError('Component schema properties must be an object')
        required = schema.get('required', [])
        if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
            raise ValueError('Component schema required must be a list')
        missing = [key for key in required if key not in config and 'default' not in properties.get(key, {})]
        if missing:
            raise ValueError(f"Missing required configuration fields: {', '.join(missing)}")
        if schema.get('additionalProperties', False) is not False:
            raise ValueError('unsupported schema keyword: additionalProperties must be false')
        if set(config) - set(properties):
            raise ValueError('Unknown configuration fields')
        normalized = deepcopy(config)
        for key, descriptor in properties.items():
            if 'default' in descriptor and key not in normalized:
                normalized[key] = deepcopy(descriptor['default'])
            if key not in normalized:
                continue
            normalized[key] = self._validate_value(key, normalized[key], descriptor)
        if component.config_validator is not None:
            component.config_validator(normalized)
        return normalized

    @staticmethod
    def _validate_value(key, value, descriptor):
        supported = {'type', 'default', 'enum', 'minimum', 'maximum', 'description',
                     'properties', 'required', 'additionalProperties', 'items', 'minItems', 'maxItems'}
        if set(descriptor) - supported:
            raise ValueError(f'unsupported schema keyword for {key}')
        if 'description' in descriptor and not isinstance(descriptor['description'], str):
            raise ValueError(f'{key} description must be a string')
        kind = descriptor.get('type')
        valid = {
            'number': lambda v: not isinstance(v, bool) and isinstance(v, (int, float)) and isfinite(v),
            'integer': lambda v: not isinstance(v, bool) and isinstance(v, int),
            'string': lambda v: isinstance(v, str),
            'boolean': lambda v: isinstance(v, bool),
            'array': lambda v: isinstance(v, list),
            'object': lambda v: isinstance(v, dict),
        }
        if kind not in valid:
            raise ValueError(f'unsupported schema type for {key}: {kind}')
        if not valid[kind](value):
            raise ValueError(f'{key} must be a {kind}')
        if 'enum' in descriptor and value not in descriptor['enum']:
            raise ValueError(f'{key} must be one of the declared enum values')
        if 'minimum' in descriptor and value < descriptor['minimum']:
            raise ValueError(f'{key} is below minimum')
        if 'maximum' in descriptor and value > descriptor['maximum']:
            raise ValueError(f'{key} is above maximum')
        if kind == 'array':
            if len(value) < descriptor.get('minItems', 0):
                raise ValueError(f'{key} has too few items')
            if 'maxItems' in descriptor and len(value) > descriptor['maxItems']:
                raise ValueError(f'{key} has too many items')
            if 'items' in descriptor:
                value = [Catalog._validate_value(f'{key}[{index}]', item, descriptor['items'])
                         for index, item in enumerate(value)]
        if kind == 'object' and 'properties' in descriptor:
            properties = descriptor['properties']
            if descriptor.get('additionalProperties', False) is not False:
                raise ValueError(f'unsupported schema keyword for {key}: additionalProperties')
            if set(value) - set(properties):
                raise ValueError(f'{key} has unknown configuration fields')
            value = deepcopy(value)
            for child, child_schema in properties.items():
                if child not in value and 'default' in child_schema:
                    value[child] = deepcopy(child_schema['default'])
                if child not in value:
                    if child in descriptor.get('required', []):
                        raise ValueError(f'{key}.{child} is required')
                    continue
                value[child] = Catalog._validate_value(f'{key}.{child}', value[child], child_schema)
        return value

    def build(self, name, config):
        component = self.get(name)
        model = component.builder(self.normalize(name, config))
        for capability in component.capabilities:
            if capability == 'checkpoint' and 'group' in component.capabilities:
                method = 'fit_predict_with_checkpoints'
            else:
                method = {'train': 'fit', 'infer': 'predict', 'group': 'fit_predict',
                          'update': 'partial_fit', 'checkpoint': 'fit_with_checkpoints',
                          'constraints': 'fit_predict'}.get(capability)
            if method and not callable(getattr(model, method, None)):
                raise TypeError(f'Missing method {method}')
        if 'checkpoint' in component.capabilities:
            for method in ('save_checkpoint', 'load_checkpoint'):
                if not callable(getattr(model, method, None)):
                    raise TypeError(f'Missing checkpoint method {method}')
        return model


class ConstrainedGroups:
    """Reference transductive grouping: connected components of must-link pairs."""
    def __init__(self, config):
        pass

    def fit_predict(self, inputs, constraints=()):
        parent = list(range(len(inputs)))
        def root(i):
            while parent[i] != i:
                i = parent[i]
            return i
        for a, b in constraints:
            parent[root(b)] = root(a)
        return ModelOutput([root(i) for i in range(len(inputs))],
                           {'semantics': 'group identifiers, not labels'})


def default_catalog():
    catalog = Catalog()
    catalog.steps.discover('storm.testing.steps')
    catalog.visualizations.discover('storm.visualization.classic')
    register_classic_metrics(catalog.metrics)
    for name, builder, props in [
        ('constant', ConstantModel, {'value': {'type': 'number', 'default': 0}}),
        ('identity', IdentityModel, {}), ('mean_regressor', MeanRegressor, {})]:
        catalog.register(Component(name, builder, ('train', 'infer'),
                                   {'type': 'object', 'properties': props}))
    catalog.register(Component('constrained_groups', ConstrainedGroups,
                               ('group', 'constraints'), {'type': 'object', 'properties': {}}))
    catalog.register(Component('online_mean', OnlineMean, ('train', 'infer', 'update', 'checkpoint'),
                               {'type': 'object', 'properties': {}}))
    return catalog


def validate_data(data, *, require_train=True, numeric=True, allow_missing_targets=False):
    json_compatible(data)
    inputs = data['inputs']
    if not isinstance(inputs, list) or not inputs:
        raise ValueError('inputs must be a nonempty list')
    if numeric and any(isinstance(x, bool) or not isinstance(x, (float, int)) or not isfinite(x) for x in inputs):
        raise ValueError('This numeric connector requires finite numbers')
    observation_ids = data.get('observation_ids', [str(index) for index in range(len(inputs))])
    if (not isinstance(observation_ids, list) or len(observation_ids) != len(inputs)
            or any(not isinstance(value, str) or not value for value in observation_ids)):
        raise ValueError('observation_ids must align with inputs and contain nonempty strings')
    if len(set(observation_ids)) != len(observation_ids):
        raise ValueError('observation_ids must be unique')
    targets = data.get('targets')
    evaluation_mask = data.get('evaluation_mask', [True] * len(inputs))
    reserved = data.get('reserved_evaluation', [False] * len(inputs))
    for name, mask in (('evaluation', evaluation_mask), ('reserved', reserved)):
        if (not isinstance(mask, list) or len(mask) != len(inputs)
                or any(type(value) is not bool for value in mask)):
            raise ValueError(f'{name} mask must contain one boolean per observation')
    if targets is not None:
        if not isinstance(targets, list) or len(targets) != len(inputs):
            raise ValueError('targets must match inputs')
        if any(value is None and evaluation_mask[index]
               for index, value in enumerate(targets)):
            raise ValueError('Missing targets must be explicitly masked')
        if numeric and any(value is not None and (isinstance(value, bool)
                or not isinstance(value, (int, float)) or not isfinite(value))
                for value in targets):
            raise ValueError('targets must be finite numbers')
    train, test = data.get('train', []), data.get('test', [])
    validation = data.get('validation', [])
    for partition in (train, validation, test):
        if not isinstance(partition, list) or any(type(i) is not int or i < 0 or i >= len(inputs) for i in partition):
            raise ValueError('Invalid partition indices')
        if len(set(partition)) != len(partition):
            raise ValueError('Duplicate partition indices')
    if require_train and not train:
        raise ValueError('Training partition is empty')
    if set(train) & set(test) or set(train) & set(validation) or set(test) & set(validation):
        raise ValueError('Partitions overlap')
    if any(reserved[index] for index in train):
        raise ValueError('Training partition contains reserved evaluation observations')
    if (require_train and targets is not None and not allow_missing_targets
            and any(targets[index] is None for index in train)):
        raise ValueError('Training targets cannot contain masked observations')
    if 'groups' in data:
        groups = data['groups']
        if len(groups) != len(inputs):
            raise ValueError('Groups must align with inputs')
        partitions = [{groups[i] for i in p} for p in (train, validation, test)]
        if (require_train and
                (partitions[0] & partitions[1] or partitions[0] & partitions[2]
                 or partitions[1] & partitions[2])):
            raise ValueError('Groups overlap between partitions')
    return inputs, targets, train, test


class CenterStep(PipelineStep):
    step_type = 'center'

    def __init__(self, value):
        self.value = value

    def process(self, context):
        context.data = [_subtract_center(row, self.value) for row in context.data]
        return context


def _subtract_center(value, center):
    if value is None or (isinstance(value, float) and not isfinite(value)):
        return value
    if isinstance(value, (list, tuple)):
        if not isinstance(center, (list, tuple)) or len(value) != len(center):
            raise ValueError('Training and evaluation feature shapes do not match')
        return [_subtract_center(item, offset) for item, offset in zip(value, center)]
    return value - center


def _fit_center(values):
    if not values:
        raise ValueError('Cannot center an empty training partition')
    first = values[0]
    if isinstance(first, (list, tuple)):
        if any(not isinstance(value, (list, tuple)) or len(value) != len(first)
               for value in values):
            raise ValueError('Training feature rows must have the same shape')
        return [_fit_center([value[index] for value in values])
                for index in range(len(first))]
    finite_values = [value for value in values
                     if isinstance(value, (int, float)) and not isinstance(value, bool)
                     and isfinite(value)]
    if not finite_values:
        return 0.0
    return sum(finite_values) / len(finite_values)


def transform(values, steps, learned=None, catalog=None, trace=None):
    """Fit centering only on training values; replay fixed transforms on test."""
    output, fitted, _ = transform_aligned(values, range(len(values)), steps, learned, catalog, trace)
    return output, fitted


def transform_aligned(values, observation_indices, steps, learned=None, catalog=None, trace=None,
                      context_metadata=None, stage_trace=None,
                      fit_observation_indices=None, progress_callback=None,
                      checkpoint_callback=None, resume_state=None):
    """Prepare values while preserving an explicit mapping to source observations.

    A step that filters or reorders values must update
    ``context.metadata['observation_indices']``. This prevents a target or a
    prediction from being silently associated with a different source row.
    """
    output, fitted = list(values), []
    source_indices = list(observation_indices)
    if len(source_indices) != len(output) or len(set(source_indices)) != len(source_indices):
        raise ValueError('Observation indices must be unique and align with inputs')
    static_keys = {'feature_names', 'taxonomy', 'likelihood_bodyparts'}
    original_metadata = dict(context_metadata or {})
    for key, rows in original_metadata.items():
        if key in static_keys:
            continue
        if not isinstance(rows, (list, tuple)):
            raise ValueError(f"Aligned pipeline metadata '{key}' must be a row sequence")
        if any(index < 0 or index >= len(rows) for index in source_indices):
            raise ValueError(f"Aligned pipeline metadata '{key}' does not cover observations")
    current_metadata = dict(original_metadata)
    for key in original_metadata.keys() - static_keys:
        current_metadata[key] = [original_metadata[key][index] for index in source_indices]
    initial_indices = list(source_indices)
    completed_steps = 0
    completed_trace = []
    if resume_state is not None:
        completed_steps = resume_state.get('completed_steps')
        if (resume_state.get('steps') != steps
                or resume_state.get('initial_indices') != initial_indices
                or resume_state.get('fit_indices') != fit_observation_indices
                or resume_state.get('learned') != learned
                or type(completed_steps) is not int
                or not 0 <= completed_steps <= len(steps)):
            raise ValueError('Preparation checkpoint does not match this pipeline.')
        output = list(resume_state['inputs'])
        source_indices = list(resume_state['selected'])
        fitted = list(resume_state['fitted'])
        current_metadata = dict(resume_state['metadata'])
        completed_trace = list(resume_state['stage_trace'])
        if (len(output) != len(source_indices) or len(set(source_indices)) != len(source_indices)
                or not set(source_indices).issubset(initial_indices)
                or len(fitted) != completed_steps or len(completed_trace) != completed_steps):
            raise ValueError('Invalid preparation checkpoint alignment.')
        if stage_trace is not None:
            stage_trace.extend(completed_trace)
    if not output and learned is not None and resume_state is None:
        if len(learned) != len(steps):
            raise ValueError('Fitted step states must align with the pipeline steps')
        if stage_trace is not None:
            stage_trace.extend(
                {'type': step['type'], 'row_count': 0, 'rows': []}
                for step in steps)
        return [], list(learned), []
    for position, step in enumerate(steps):
        if position < completed_steps:
            continue
        if progress_callback is not None:
            progress_callback({
                'label': f"Aplicando paso {position + 1} de {len(steps)}: {step['type']}",
                'phase_step': position, 'phase_total': len(steps),
                'unit_label': 'pasos de preparación',
            })
        if step['type'] == 'center':
            if learned is not None:
                value = learned[position]['value']
            elif fit_observation_indices is not None:
                fit_indices = set(fit_observation_indices)
                fit_values = [item for item, index in zip(output, source_indices)
                              if index in fit_indices]
                if not fit_values:
                    raise ValueError('Centering requires an unreserved training observation')
                value = _fit_center(fit_values)
            else:
                value = _fit_center(output)
            plugin = CenterStep(value)
        elif step['type'] == 'scale':
            value = float(step['factor'])
            if not isfinite(value):
                raise ValueError('Scale must be finite')
            plugin = ScaleStep(factor=value)
        else:
            if catalog is None:
                raise ValueError('Unsupported step')
            plugin = catalog.steps.build(step['type'], step.get('config', {}))
            value = step.get('config', {})
        step_metadata = {'observation_indices': list(source_indices), **current_metadata}
        context = PipelineRunner([plugin]).run(PipelineContext(
            data=output, metadata=step_metadata, progress_callback=progress_callback))
        if progress_callback is not None:
            progress_callback({'label': f"Validando alineación del paso {position + 1} de {len(steps)}: {step['type']}",
                               'phase_step': position, 'phase_total': len(steps),
                               'unit_label': 'pasos de preparación'})
        output = list(context.data)
        mapped = context.metadata.get('observation_indices')
        if not isinstance(mapped, list) or len(mapped) != len(output):
            raise ValueError('A step that changes observations must declare observation_indices')
        source_index_set = set(source_indices)
        if (any(type(index) is not int or index not in source_index_set for index in mapped)
                or len(set(mapped)) != len(mapped)):
            raise ValueError('Invalid observation_indices declared by pipeline step')
        if len(output) != len(source_indices) and mapped == source_indices:
            raise ValueError('A step that changes observations must declare observation_indices')
        source_indices = mapped
        next_metadata = {
            key: value for key, value in context.metadata.items()
            if key in static_keys or key == 'window_source_indices'
        }
        for key, original_rows in original_metadata.items():
            if key in static_keys:
                next_metadata.setdefault(key, original_rows)
                continue
            candidate = context.metadata.get(key)
            if isinstance(candidate, (list, tuple)) and len(candidate) == len(output):
                next_metadata[key] = list(candidate)
            else:
                next_metadata[key] = [original_rows[index] for index in source_indices]
        current_metadata = next_metadata
        if trace is not None:
            trace.extend(dict(item.to_dict(), index=position) for item in context.executions)
        if stage_trace is not None or checkpoint_callback is not None:
            row = {
                'type': step['type'],
                'row_count': len(output),
                'rows': [
                    {'observation_index': index, 'value': deepcopy(item)}
                    for index, item in zip(source_indices[:5], output[:5])
                ],
            }
            completed_trace.append(row)
            if stage_trace is not None:
                stage_trace.append(row)
        fitted.append({'type': step['type'], 'value': value})
        if checkpoint_callback is not None:
            checkpoint_callback({
                'steps': deepcopy(steps), 'initial_indices': initial_indices,
                'fit_indices': fit_observation_indices, 'learned': learned,
                'completed_steps': position + 1, 'inputs': output,
                'selected': source_indices, 'fitted': list(fitted),
                'metadata': current_metadata, 'stage_trace': list(completed_trace),
            })
        if progress_callback is not None:
            progress_callback({
                'label': f"Paso {position + 1} de {len(steps)} completado: {step['type']}",
                'phase_step': position + 1, 'phase_total': len(steps),
                'unit_label': 'pasos de preparación',
            })
    return output, fitted, source_indices


def missing_required_pipeline_steps(model_name, steps, catalog=None):
    """Return adapter-declared preparation steps that are absent from a plan."""
    catalog = catalog or default_catalog()
    component = catalog.get(model_name)
    required = (component.input_contract.required_steps if component.input_contract
                else getattr(component.builder, 'required_pipeline_steps', ()))
    configured = {
        step.get('type') for step in steps
        if isinstance(step, dict) and isinstance(step.get('type'), str)
    }
    return [step for step in required if step not in configured]


def execute(spec, store_root, execution_id, catalog=None, *, update_from=None,
            resume_from=None, resume_config=None, inference_from=None,
            progress_callback=None):
    def report_progress(phase, label, stage_index, *, phase_step=None, phase_total=None,
                        unit_label=None, **details):
        if progress_callback is not None:
            progress_callback({
                'phase': phase, 'label': label,
                'stage_index': stage_index,
                'stage_total': 4 if spec.get('operation') == 'infer' else 5,
                'phase_step': phase_step, 'phase_total': phase_total,
                'unit_label': unit_label,
                **details,
            })

    def pipeline_progress(phase, stage_index):
        def report_step(update):
            report_progress(
                phase, update.get('label', 'Preparación de datos'), stage_index,
                **{key: value for key, value in update.items() if key not in {'phase', 'label'}})
        return report_step

    report_progress('loading', 'Cargando y validando los datos', 1)
    spec = json_compatible(spec)
    catalog = catalog or default_catalog()
    validation_problems = require_valid_plan(spec, catalog, spec.get('data_summary'))
    descriptor = catalog.get(spec['model'])
    from storm.descriptors import component_snapshot
    snapshot = component_snapshot(descriptor)
    preapplied_steps = spec.get('preapplied_steps', [])
    if (not isinstance(preapplied_steps, list)
            or any(not isinstance(step, str) or not step for step in preapplied_steps)
            or len(preapplied_steps) != len(set(preapplied_steps))):
        raise ValueError('preapplied_steps must be a unique list of nonempty step types')
    steps_for_validation = list(spec.get('steps', [])) + [
        {'type': step} for step in preapplied_steps] + _materialized_steps(spec.get('data_summary'))
    missing_steps = missing_required_pipeline_steps(
        spec['model'], steps_for_validation, catalog)
    if missing_steps:
        raise ValueError(
            f"Model {spec['model']} requires preparation step(s): "
            f"{', '.join(missing_steps)}"
        )
    operation = spec.get('operation', 'train')
    if operation not in ('train', 'infer'):
        raise ValueError('Unknown execution operation')
    if operation == 'infer' and (update_from is not None or resume_from is not None):
        raise ValueError('Inference cannot update or resume training')
    if inference_from is not None and (operation != 'infer' or update_from is not None
                                       or resume_from is not None):
        raise ValueError('Saved-model inference is a separate operation')
    if sum(value is not None for value in (update_from, resume_from, inference_from)) > 1:
        raise ValueError('Updating, resuming, and applying a saved model are separate operations')
    store = FileArtifactStore(store_root)
    from storm.observability import ExecutionObserver
    data = ExecutionObserver(pipeline_progress('loading', 1)).call(
        catalog.connectors[spec.get('connector', 'numeric_json')], '__call__', spec['data'])
    raw_inputs = data.get('inputs')
    input_count = len(raw_inputs) if isinstance(raw_inputs, list) else 0
    report_progress(
        'loading', 'Datos cargados; validando observaciones', 1,
        phase_step=0 if input_count else None,
        phase_total=input_count if input_count else None,
        unit_label='observaciones' if input_count else None)
    data_fingerprint = data.get('data_fingerprint')
    if data_fingerprint is not None:
        if not isinstance(data_fingerprint, str) or not data_fingerprint.startswith('sha256:'):
            raise ValueError('Connector data_fingerprint must be a SHA-256 identity')
    else:
        data_fingerprint = fingerprint(data)
    if inference_from is not None:
        source_spec = inference_from.get('spec', {})
        if (inference_from.get('model') != spec['model']
                or inference_from.get('model_version') != descriptor.version
                or 'infer' not in inference_from.get('capabilities', [])
                or source_spec.get('connector', 'numeric_json')
                != spec.get('connector', 'numeric_json')
                or source_spec.get('config', {}) != spec.get('config', {})
                or source_spec.get('steps', []) != spec.get('steps', [])
                or source_spec.get('preapplied_steps', [])
                != spec.get('preapplied_steps', [])):
            raise ValueError('Saved model, version, configuration, or preparation is incompatible')
        if not inference_from.get('model_ref') or not isinstance(
                inference_from.get('fitted_steps'), list) or len(
                inference_from['fitted_steps']) != len(spec.get('steps', [])):
            raise ValueError('Saved execution has no recoverable model and fitted pipeline')
        data = dict(data)
        count = len(data.get('inputs', []))
        data['train'] = []
        data['validation'] = []
        data['test'] = list(range(count))
        if isinstance(data.get('partitions'), list):
            data['partitions'] = ['test'] * count
    elif (operation == 'infer' and 'infer' in descriptor.capabilities
          and not {'train', 'group'} & set(descriptor.capabilities)
          and not data.get('validation') and not data.get('test')):
        # Imported inference-only models need no training split. If a registered
        # dataset has no explicit evaluation partition, predict every observation.
        data = dict(data)
        count = len(data.get('inputs', []))
        data['train'] = []
        data['validation'] = []
        data['test'] = list(range(count))
        if isinstance(data.get('partitions'), list):
            data['partitions'] = ['test'] * count
    inputs, targets, train, test = validate_data(
        data,
        require_train=operation != 'infer',
        numeric=spec.get('connector', 'numeric_json') == 'numeric_json',
        allow_missing_targets='group' in descriptor.capabilities,
    )
    report_progress(
        'loading', f'Datos validados: {len(inputs)} observaciones', 1,
        phase_step=len(inputs), phase_total=len(inputs), unit_label='observaciones')
    alignment = ObservationAlignment(
        tuple(data.get('observation_ids', [str(index) for index in range(len(inputs))])),
        tuple(range(len(inputs))),
    )
    partition_name = 'validation' if data.get('validation') else 'test'
    evaluation_indices = data.get(partition_name, [])
    constraints = spec.get('constraints', [])
    if constraints and 'constraints' not in descriptor.capabilities:
        raise ValueError('Model does not accept constraints')
    if 'group' not in descriptor.capabilities and not evaluation_indices:
        raise ValueError('Evaluation partition is required')
    report_progress('preparing', 'Preparando los datos para el modelo', 2)
    random.seed(spec.get('seed', 0))
    for seeder in catalog.seeders:
        seeder(spec.get('seed', 0))
    model = (store.load(ArtifactRef.from_dict(inference_from['model_ref']))
             if inference_from is not None
             else catalog.build(spec['model'], spec.get('config', {})))
    previous_steps = None
    checkpoint = None
    if inference_from is not None:
        previous_steps = inference_from['fitted_steps']
    if update_from is not None:
        if 'update' not in descriptor.capabilities:
            raise ValueError('Model does not support incremental updates')
        if (update_from['model'] != spec['model'] or update_from['model_version'] != descriptor.version
                or update_from['spec'].get('config', {}) != spec.get('config', {})
                or update_from['spec'].get('steps', []) != spec.get('steps', [])
                or update_from['spec'].get('preapplied_steps', [])
                != spec.get('preapplied_steps', [])):
            raise ValueError('Incremental model and preparation must remain compatible')
        model = store.load(ArtifactRef.from_dict(update_from['model_ref']))
        previous_steps = update_from['fitted_steps']
    if resume_from is not None:
        if 'checkpoint' not in descriptor.capabilities:
            raise ValueError('Model does not support checkpoints')
        checkpoint = store.load(resume_from)
        if (checkpoint.get('component_identity') is not None
                and checkpoint['component_identity'] != snapshot['identity_fingerprint']):
            raise ValueError('Checkpoint component identity changed; create a new training plan')
        compatible_fingerprint = checkpoint['fingerprint'] == fingerprint(spec)
        if not compatible_fingerprint and isinstance(resume_config, dict):
            current_config = spec.get('config') or {}
            current_without_device = {
                key: value for key, value in current_config.items() if key != 'device'}
            previous_without_device = {
                key: value for key, value in resume_config.items() if key != 'device'}
            if (current_config.get('device') != resume_config.get('device')
                    and current_without_device == previous_without_device):
                compatible_spec = deepcopy(spec)
                compatible_spec['config'] = {
                    **current_config, 'device': resume_config.get('device')}
                compatible_fingerprint = (
                    checkpoint['fingerprint'] == fingerprint(compatible_spec))
        if (not compatible_fingerprint
                or checkpoint['model_version'] != descriptor.version
                or checkpoint['data_fingerprint'] != data_fingerprint):
            raise ValueError('Checkpoint is not compatible with this plan and model version')
        previous_steps = checkpoint['fitted_steps']
        model.load_checkpoint(checkpoint['state'])
    if spec.get('steps') and getattr(model, 'supports_pipeline_steps', True) is False:
        raise ValueError(
            'This model performs its own preparation and does not accept generic pipeline steps'
        )
    from storm.observability import ExecutionObserver
    def model_progress(update):
        phase = update.get('phase', 'predicting' if operation == 'infer' else 'training')
        stage = 4 if phase == 'evaluating' or (phase == 'predicting' and operation != 'infer') else 3
        if progress_callback is not None:
            progress_callback({'phase': phase, 'stage_index': stage,
                               'stage_total': 4 if operation == 'infer' else 5, **update})
    model_observer = ExecutionObserver(model_progress if progress_callback else None)
    bind_progress = model_observer.bind(model)
    training_trace, evaluation_trace = [], []
    aligned_metadata = {
        key: data[key]
        for key in ("frames", "sessions", "segments", "partitions",
                    "reserved_evaluation", "likelihoods", "feature_names",
                    "taxonomy", "likelihood_bodyparts")
        if key in data
    }
    if operation == 'infer':
        if 'infer' not in descriptor.capabilities:
            raise ValueError('Model cannot infer')
        if inference_from is None and any(
                step['type'] == 'center' for step in spec.get('steps', [])):
            raise ValueError('Use a recovered trained pipeline for centering during inference')
        evaluation, fitted, selected = transform_aligned(
            [inputs[i] for i in evaluation_indices], evaluation_indices, spec.get('steps', []),
            previous_steps, catalog, evaluation_trace, context_metadata=aligned_metadata,
            progress_callback=pipeline_progress('preparing', 2))
        report_progress(
            'predicting', 'Calculando predicciones', 3,
            phase_step=0, phase_total=len(evaluation), unit_label='observaciones')
        predict_with_context = getattr(model, 'predict_with_context', None)
        if callable(predict_with_context):
            output = predict_with_context(evaluation, data, selected)
        else:
            output = model_observer.call(model, 'predict', evaluation)
    else:
        prepared, fitted, prepared_train_indices = transform_aligned(
            [inputs[i] for i in train], train, spec.get('steps', []), previous_steps,
            catalog=catalog, trace=training_trace, context_metadata=aligned_metadata,
            progress_callback=pipeline_progress('preparing', 2))
        total_epochs = spec.get('config', {}).get('epochs')
        if type(total_epochs) is not int or total_epochs < 1:
            total_epochs = None
        initial_epoch = checkpoint.get('state', {}).get('epoch', 0) if checkpoint else 0
        report_progress(
            'training', 'Entrenando o agrupando los datos', 3,
            phase_step=initial_epoch if total_epochs else None,
            phase_total=total_epochs, unit_label='épocas' if total_epochs else None,
        )

        def save_checkpoint(state):
            store.save(kind='checkpoints', artifact_id=execution_id, value={
                'state': state, 'fingerprint': fingerprint(spec), 'model_version': descriptor.version,
                'data_fingerprint': data_fingerprint,
                'component_identity': snapshot['identity_fingerprint'],
                'fitted_steps': fitted, 'source_execution': execution_id},
                metadata={'execution_id': execution_id})
            epoch = state.get('epoch') if isinstance(state, dict) else None
            state_config = state.get('training_config', {}) if isinstance(state, dict) else {}
            epochs = state_config.get('epochs', total_epochs)
            if type(epoch) is int and type(epochs) is int and epochs > 0:
                report_progress(
                    'training', 'Entrenando el modelo', 3,
                    phase_step=epoch, phase_total=epochs, unit_label='épocas',
                    checkpoint_epoch=epoch, checkpoint_saved=True,
                )

        if 'group' in descriptor.capabilities:
            index_map = {original: i for i, original in enumerate(prepared_train_indices)}
            if any(len(pair) != 2 or any(type(i) is not int or i not in index_map for i in pair) for pair in constraints):
                raise ValueError('constraints must reference training samples')
            bind_data = getattr(model, 'bind_data', None)
            if callable(bind_data):
                bind_data(data, prepared_train_indices)
            prepared_constraints = [(index_map[a], index_map[b]) for a, b in constraints]
            if 'checkpoint' in descriptor.capabilities:
                output = model.fit_predict_with_checkpoints(
                    prepared, prepared_constraints, save_checkpoint)
            else:
                output = model_observer.call(model, 'fit_predict', prepared, prepared_constraints)
            selected = prepared_train_indices
            report_progress('evaluating', 'Generando y validando los grupos', 4)
        else:
            if 'train' not in descriptor.capabilities:
                raise ValueError('Model cannot train')
            train_targets = None if targets is None else [targets[i] for i in prepared_train_indices]
            if update_from is not None:
                model.partial_fit(prepared, train_targets)
            elif 'checkpoint' in descriptor.capabilities:
                model.fit_with_checkpoints(prepared, train_targets, save_checkpoint)
            else:
                model_observer.call(model, 'fit', prepared, train_targets)
            evaluation, _, selected = transform_aligned(
                [inputs[i] for i in evaluation_indices], evaluation_indices, spec.get('steps', []),
                fitted, catalog, evaluation_trace, context_metadata=aligned_metadata,
                progress_callback=pipeline_progress('evaluating', 4))
            report_progress(
                'evaluating', 'Evaluando las predicciones', 4,
                phase_step=0, phase_total=len(evaluation), unit_label='observaciones')
            output = model_observer.call(model, 'predict', evaluation)
    if not isinstance(output, ModelOutput) or len(output.predictions) != len(selected):
        raise ValueError('Output must align with selected samples')
    output_stage = 3 if operation == 'infer' else 4
    output_label = ('Predicciones calculadas' if operation == 'infer' else
                    'Grupos generados y validados' if 'group' in descriptor.capabilities else
                    'Predicciones evaluadas')
    report_progress(
        'predicting' if operation == 'infer' else 'evaluating',
        f'{output_label}: {len(output.predictions)}', output_stage,
        phase_step=len(output.predictions),
        phase_total=(len(evaluation) if operation == 'infer' or 'group' not in descriptor.capabilities
                     else len(selected)),
        unit_label='observaciones')
    prediction_mask = output.metadata.get('prediction_mask', [True] * len(selected))
    if (not isinstance(prediction_mask, (list, tuple))
            or len(prediction_mask) != len(selected)
            or any(type(value) is not bool for value in prediction_mask)):
        raise ValueError('prediction_mask must contain one boolean per selected observation')
    prediction_mask = list(prediction_mask)
    predictions = json_compatible(list(output.predictions))
    evaluation_mask = data.get('evaluation_mask', [True] * len(inputs))
    metric_indices = [index for position, index in enumerate(selected)
                      if evaluation_mask[index] and prediction_mask[position]]
    is_group_model = 'group' in descriptor.capabilities
    requested_metrics = spec.get('metrics') or []
    output_task = ('clustering' if is_group_model else
                   output.metadata.get('task'))
    if output_task is None and not requested_metrics:
        output_task = 'regression'
    metric_targets = targets
    metric_reason = None
    if output.metadata.get('task') == 'binary_classification' and targets is not None:
        taxonomy = data.get('taxonomy')
        mapping = output.metadata.get('category_mapping')
        mapping_version = output.metadata.get('category_mapping_version')
        valid_taxonomy = (
            isinstance(taxonomy, list) and len(taxonomy) == 2
            and all(isinstance(category, str) and category for category in taxonomy)
            and len(set(taxonomy)) == 2
            and mapping == {'0': taxonomy[0], '1': taxonomy[1]}
            and isinstance(mapping_version, str) and bool(mapping_version.strip())
        )
        normalized_targets = list(targets)
        valid_binary_values = valid_taxonomy
        if valid_binary_values:
            for position, index in enumerate(selected):
                if index not in metric_indices:
                    continue
                target = targets[index]
                prediction = output.predictions[position]
                if (isinstance(target, (int, float)) and not isinstance(target, bool)
                        and target in (0, 1)):
                    normalized_targets[index] = int(target)
                elif isinstance(target, str) and target in taxonomy:
                    normalized_targets[index] = taxonomy.index(target)
                else:
                    valid_binary_values = False
                    break
                if (not isinstance(prediction, (int, float)) or isinstance(prediction, bool)
                        or prediction not in (0, 1)):
                    valid_binary_values = False
                    break
        if not valid_binary_values:
            requested_metrics = []
            output_task = 'invalid_classification_mapping'
            metric_reason = (
                'No se calcularon métricas binarias: la correspondencia de categorías '
                'del modelo no coincide con las etiquetas del benchmark.')
        else:
            metric_targets = normalized_targets
    metric_names = []
    if targets is not None:
        if requested_metrics:
            candidates = requested_metrics
        elif output_task == 'invalid_classification_mapping':
            candidates = []
        elif output_task in {'classification', 'binary_classification', 'clustering'}:
            candidates = catalog.metrics.available
        elif output_task is None:
            # An explicit metric request is the investigator's task choice when
            # a generic model has not declared output semantics. Do not guess a
            # default metric for an untyped output.
            candidates = []
        elif 'metrics' in spec:
            candidates = []
        else:
            candidates = ['mae', 'mse']
        for name in candidates:
            try:
                compatible_task = getattr(catalog.metrics.get(name), 'compatible_task', None)
            except KeyError:
                continue
            if output_task in {'classification', 'binary_classification'}:
                compatible = compatible_task == 'classification'
            elif output_task == 'clustering':
                compatible = compatible_task == 'clustering'
            elif output_task is None:
                compatible = bool(requested_metrics)
            else:
                compatible = compatible_task in (None, 'regression')
            if compatible:
                metric_names.append(name)
    metric_sessions = data.get('sessions') or []
    if (is_group_model
            and output.metadata.get('discretizer_scope') in {'session_local', 'per_session'}
            and len({str(metric_sessions[index]) for index in metric_indices
                     if 0 <= index < len(metric_sessions)}) > 1):
        metric_names = []
        metric_reason = (
            'Los estados son locales por sesión; no se calculan métricas globales '
            'mezclando IDs de discretizadores independientes.')
    if is_group_model and operation != 'infer':
        metric_names = []
        metric_reason = (
            'Las métricas VAME contra etiquetas humanas se calculan al inferir con el '
            'modelo guardado, no sobre sus grupos de entrenamiento.')
    metrics = {}
    if targets is not None:
        if metric_indices:
            selected_positions = [selected.index(index) for index in metric_indices]
            evaluation_dataset = Dataset([inputs[i] for i in metric_indices],
                                         [metric_targets[i] for i in metric_indices])
            metric_output = ModelOutput(
                [output.predictions[position] for position in selected_positions],
                metadata=output.metadata)
            metrics = {name: float(ExecutionObserver(pipeline_progress('evaluating', 4)).call(
                catalog.metrics.get(name), 'evaluate',
                dataset=evaluation_dataset, output=metric_output, model=model))
                for name in metric_names}
    if metric_reason is None:
        if targets is None:
            metric_reason = 'Esta revisión no tiene etiquetas de referencia para calcular métricas.'
        elif not metric_indices:
            metric_reason = 'No quedaron predicciones y etiquetas válidas en la máscara de evaluación.'
        elif not metric_names:
            metric_reason = 'No hay métricas compatibles seleccionadas para esta salida del modelo.'
    artifact_stage = 4 if operation == 'infer' else 5
    artifact_total = 1 if inference_from is not None else 2
    report_progress(
        'saving', 'Guardando modelo y resultados', artifact_stage,
        phase_step=0, phase_total=artifact_total, unit_label='artefactos')
    if is_group_model and not metric_names:
        evaluation_status = 'inspection_only_no_group_metric'
    elif targets is None:
        evaluation_status = 'inspection_only_no_reference'
    elif targets is not None and metric_indices and not metric_names:
        evaluation_status = 'metrics_not_requested'
    elif targets is not None and not metric_indices:
        evaluation_status = (
            'inspection_only_no_valid_predictions'
            if any(evaluation_mask[index] for index in selected)
            else 'inspection_only_no_valid_labels'
        )
    else:
        evaluation_status = (
            'rankable' if metric_indices else 'inspection_only_no_reference'
        )
    metric_definitions = [catalog.metrics.describe(name) for name in metric_names]
    lineage = {'execution_id': execution_id, 'spec': spec, 'fingerprint': fingerprint(spec)}
    if callable(bind_progress):
        bind_progress(None)
    reference = (ArtifactRef.from_dict(inference_from['model_ref'])
                 if inference_from is not None else store.save(
                     kind='models', artifact_id=execution_id, value=model, metadata=lineage))
    if inference_from is None:
        report_progress(
            'saving', 'Modelo guardado; guardando predicciones', artifact_stage,
            phase_step=1, phase_total=artifact_total, unit_label='artefactos')
    output_ref = store.save(kind='outputs', artifact_id=execution_id, value=output, metadata=lineage)
    report_progress(
        'saving', 'Resultados guardados', artifact_stage,
        phase_step=artifact_total, phase_total=artifact_total, unit_label='artefactos')
    configuration_snapshot = getattr(model, 'configuration_snapshot', None)
    resolved_config = (json_compatible(configuration_snapshot())
                       if callable(configuration_snapshot) else None)
    result = {'configuration': {'requested': deepcopy(spec.get('config', {})),
                                 'resolved': resolved_config,
                                 'normalized': catalog.normalize(spec['model'], spec.get('config', {}))},
              'component_snapshot': snapshot,
              'validation_problems': [p.to_dict() for p in validation_problems],
              'execution_id': execution_id, 'model': spec['model'], 'model_version': descriptor.version,
              'capabilities': list(descriptor.capabilities), 'model_ref': reference.to_dict(),
              'output_ref': output_ref.to_dict(), 'spec': spec, 'fingerprint': fingerprint(spec),
              'data_fingerprint': data_fingerprint, 'resolved_data': data, 'indices': selected,
              'alignment': {'source_ids': list(alignment.source_ids),
                            'output_indices': [alignment.output_indices[i] for i in selected]},
              'predictions': predictions, 'metrics': metrics, 'fitted_steps': fitted,
              'metric_reason': metric_reason,
              'prediction_mask': prediction_mask,
              'metric_indices': metric_indices,
              'evaluation_mask': [data.get('evaluation_mask', [True] * len(inputs))[i]
                                  for i in selected],
              'evaluation_status': evaluation_status,
              'output_metadata': json_compatible(dict(output.metadata)),
              'metric_definitions': metric_definitions,
              'preparation_trace': {'train': training_trace, 'evaluation': evaluation_trace},
              'source_execution': (inference_from['execution_id'] if inference_from else
                  update_from['execution_id'] if update_from else (
                  checkpoint['source_execution'] if checkpoint else None)),
              'partition': 'training groups' if 'group' in descriptor.capabilities else partition_name}
    store.save(kind='results', artifact_id=execution_id, value=result, metadata=lineage)
    return result


def infer(result, inputs, store_root, catalog=None):
    if 'infer' not in result['capabilities']:
        raise ValueError('Model does not support inference on new samples')
    if result.get('output_metadata', {}).get('requires_registered_data_for_inference'):
        raise ValueError('This model requires inference from a registered pose dataset')
    if not isinstance(inputs, list) or not inputs:
        raise ValueError('Provide a nonempty list of inputs')
    json_compatible(inputs)
    if result['spec'].get('connector', 'numeric_json') == 'numeric_json' and any(type(x) not in (int, float) or not isfinite(x) for x in inputs):
        raise ValueError('The numeric connector requires finite numeric inputs')
    model = FileArtifactStore(store_root).load(ArtifactRef.from_dict(result['model_ref']))
    prepared, _ = transform(inputs, result['spec'].get('steps', []), result['fitted_steps'], catalog or default_catalog())
    output = model.predict(prepared)
    if not isinstance(output, ModelOutput) or len(output.predictions) != len(inputs):
        raise ValueError('Prediction alignment mismatch')
    return json_compatible(list(output.predictions))


def evaluate(result, inputs, targets, store_root, catalog=None):
    """Evaluate a recovered model without fitting it again."""
    catalog = catalog or default_catalog()
    predictions = infer(result, inputs, store_root, catalog)
    if not isinstance(targets, list) or len(targets) != len(inputs):
        raise ValueError('Targets must align with inputs')
    model = FileArtifactStore(store_root).load(ArtifactRef.from_dict(result['model_ref']))
    dataset = Dataset(inputs, targets)
    metrics = {name: float(catalog.metrics.get(name).evaluate(
        dataset=dataset, output=ModelOutput(predictions), model=model))
        for name in result['spec'].get('metrics', ['mae', 'mse'])}
    return {'source_execution': result['execution_id'], 'inputs': inputs, 'targets': targets,
            'predictions': predictions, 'metrics': metrics,
            'fingerprint': fingerprint({'inputs': inputs, 'targets': targets})}
