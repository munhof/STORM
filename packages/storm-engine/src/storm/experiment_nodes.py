"""Dependency-free reference operations and plugin bridges for experiment DAGs."""
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import random
from time import perf_counter

from storm.adapters import ModelAdapter, GroupModelAdapter
from storm.alignment import ObservationSeries, TimeAxis, align_identity, align_temporal
from storm.experiments import ExperimentExecutor, ExperimentRegistry, NodeOperation, expand_study
from storm.models import ModelOutput
from storm.pipeline import PipelineContext
from storm.pipeline.context import ContentDescriptor
from storm.runs import Dataset


def object_schema(properties=None, required=()):
    return {'type': 'object', 'properties': properties or {}, 'required': list(required)}


def _inline(inputs, config):
    values, partitions = config['inputs'], config['partitions']
    count = len(values)
    for key in ('ids', 'sessions', 'partitions'):
        if len(config[key]) != count:
            raise ValueError(f'{key} must align with every input observation')
    if config.get('targets') and len(config['targets']) != count:
        raise ValueError('targets must align with observations')
    if config.get('times') and len(config['times']) != count:
        raise ValueError('times must align with observations')
    if len(set(zip(config['sessions'], config['ids']))) != count:
        raise ValueError('Duplicate observation identities')
    session_partition = {}
    for session, partition in zip(config['sessions'], partitions):
        if partition not in ('train', 'validation', 'test'):
            raise ValueError('Unknown partition')
        if session in session_partition and session_partition[session] != partition:
            raise ValueError('A session cannot cross partitions')
        session_partition[session] = partition
    outputs = {}
    for partition in ('train', 'validation'):
        indices = [i for i, p in enumerate(partitions) if p == partition]
        metadata = {'observation_ids': [config['ids'][i] for i in indices],
            'sessions': [config['sessions'][i] for i in indices],
            'partitions': [partition] * len(indices), 'partition': partition,
            'observation_indices': indices, 'task': config.get('task', 'regression'),
            'units': config.get('units')}
        source_values = {key: [config[key][i] for i in indices]
                         for key in ('inputs', 'targets', 'ids', 'sessions', 'times')
                         if config.get(key)}
        source_values.update(partition=partition, units=config.get('units'),
                             time_axis=config.get('time_axis'))
        metadata['source_hashes'] = [hashlib.sha256(json.dumps(
            source_values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()]
        if config.get('times'):
            metadata['times'] = [config['times'][i] for i in indices]
            metadata['time_axis'] = config['time_axis']
        outputs[partition] = PipelineContext(data=[values[i] for i in indices],
            targets=[config['targets'][i] for i in indices] if config.get('targets') else None,
            metadata=metadata, schema={'data': ContentDescriptor(dtype='number',
                units=config.get('units'), identity='metadata.observation_ids',
                time='metadata.times' if config.get('times') else None)})
    return outputs


def _scale(inputs, config):
    from storm.suite import _subtract_center
    context = inputs['context']
    def multiply(value):
        return [multiply(v) for v in value] if isinstance(value, (list, tuple)) else value * config['factor']
    context.data = [multiply(value) for value in context.data]
    return {'context': context}


def _windows(inputs, config):
    context = inputs['context']
    offsets = config['offsets']
    if offsets != sorted(set(offsets)) or 0 not in offsets:
        raise ValueError('Window offsets must be sorted, unique and include zero')
    m = context.metadata
    count = len(context.data)
    frames = m.get('times', m['observation_indices'])
    lookup = {(session, partition, frame): i for i, (session, partition, frame) in
        enumerate(zip(m['sessions'], m['partitions'], frames))}
    if len(lookup) != count:
        raise ValueError('Ambiguous duplicate window observations')
    selected, windows, parents = [], [], []
    for i in range(count):
        indices = [lookup.get((m['sessions'][i], m['partitions'][i], frames[i] + offset))
                   for offset in offsets]
        if any(j is None for j in indices):
            continue
        selected.append(i)
        windows.append([context.data[j] for j in indices])
        parents.append([m['observation_ids'][j] for j in indices])
    context.data = windows
    if context.targets is not None:
        context.targets = [context.targets[i] for i in selected]
    for key in ('sessions', 'partitions', 'observation_ids', 'observation_indices', 'times'):
        if key in m:
            m[key] = [m[key][i] for i in selected]
    context.state['window_parents'] = parents
    return {'context': context}


def _center(inputs, config):
    from storm.suite import _fit_center, _subtract_center
    train, validation = inputs['train'], inputs['validation']
    _partitions(train, validation)
    if not train.data:
        raise ValueError('Centering requires training observations')
    center = _fit_center(train.data)
    for context in (train, validation):
        context.data = [_subtract_center(row, center) for row in context.data]
        context.state['center'] = deepcopy(center)
    return inputs


def _partitions(train, validation):
    for context, expected in ((train, 'train'), (validation, 'validation')):
        if context.metadata.get('partition') != expected or any(
                p != expected for p in context.metadata.get('partitions', [])):
            raise ValueError(f'Expected {expected} partition')
    if set(train.metadata.get('sessions', [])) & set(validation.metadata.get('sessions', [])):
        raise ValueError('Training and validation cannot share sessions')


def _resolve(context, path):
    if path == 'none':
        return None
    head, *parts = path.split('.')
    if head not in ('data', 'targets', 'state', 'artifacts', 'metadata'):
        raise ValueError(f'Unknown context binding: {path}')
    value = getattr(context, head)
    for part in parts:
        value = value[part]
    return value


def _select(inputs, config):
    context = inputs['context']
    data, targets = _resolve(context, config['data']), _resolve(context, config['targets'])
    if len(data) != len(context.metadata['observation_ids']):
        raise ValueError('Bound model input must align with observation identities')
    if targets is not None and len(targets) != len(data):
        raise ValueError('Bound targets must align with inputs')
    context.data, context.targets = data, targets
    context.state['bindings'] = deepcopy(config)
    return {'context': context}


def _series(context):
    m = context.metadata
    return ObservationSeries(tuple(m['observation_ids']), tuple(m['sessions']),
        tuple(m['partitions']), tuple(m.get('times', [0] * len(context.data))),
        context.data, TimeAxis(**m.get('time_axis',
            {'clock': 'identity', 'unit': 'none', 'origin': 'none', 'scope': 'session'})))


def _join(inputs, config):
    reference, source = inputs['reference'], inputs['source']
    if config['method'] == 'identity':
        aligned = align_identity(_series(reference), _series(source))
    else:
        aligned = align_temporal(_series(reference), _series(source), method=config['method'],
                                  tolerance=config.get('tolerance'))
    # Keep reference values and expose aligned auxiliary data with explicit masks.
    reference.state['aligned'] = aligned.values
    reference.state['alignment_mask'] = aligned.valid
    reference.state['alignment_parents'] = aligned.parents
    reference.schema['state.aligned'] = source.schema.get('data', ContentDescriptor('unknown'))
    return {'context': reference}


class ScientificRegistry(ExperimentRegistry):
    def validate(self, graph):
        from storm.contracts import ValidationProblem
        problems = super().validate(graph)
        if problems:
            return problems
        nodes = {node['id']: node for node in graph['nodes']}
        for node in graph['nodes']:
            name, kind = node['id'], self.operations[node['type']].kind
            if kind == 'model':
                for edge in graph['edges']:
                    if edge['target'].split('.')[0] == name:
                        source = nodes[edge['source'].split('.')[0]]
                        if self.operations[source['type']].kind != 'adapt':
                            problems.append(ValidationProblem('experiment.binding', 'error', 'root',
                                name, edge['target'].split('.')[1], 'Bind model inputs explicitly through an adapter'))
            if node['type'] == 'join.align' and node.get('config', {}).get('method', 'exact') not in ('identity', 'exact'):
                if 'tolerance' not in node.get('config', {}):
                    problems.append(ValidationProblem('experiment.tolerance', 'error', 'root',
                        name, 'config.tolerance', 'Non-exact alignment requires explicit tolerance'))
        return problems


@dataclass
class SavedModelHandle:
    model: object
    adapter: object
    source: dict
    outputs: dict | None = None


def _load_saved_model(inputs, config, catalog):
    from storm.artifacts import ArtifactRef, FileArtifactStore
    source = deepcopy(config['source'])
    descriptors = {item['name']: item for item in catalog.describe()}
    descriptor = descriptors.get(source.get('model'))
    if descriptor is None:
        raise ValueError('The saved model plugin is not installed')
    if descriptor['version'] != source.get('model_version'):
        raise ValueError('Saved model version differs from the installed plugin; explicit migration required')
    capabilities = set(descriptor['capabilities'])
    if 'infer' not in capabilities:
        raise ValueError('The saved model plugin does not support inference')
    source['capabilities'] = list(descriptor['capabilities'])
    source['input_contract'] = descriptor.get('input_contract')
    if source.get('bundle') and capabilities & {'train', 'group', 'update'}:
        raise ValueError('A trainable model is not a pretrained bundle; select a fitted run')
    root = getattr(catalog, 'experiment_artifact_root', None)
    if not root:
        raise ValueError('Model recovery requires a configured artifact store')
    store = FileArtifactStore(root)
    outputs = None
    if source.get('model_ref'):
        model = store.load(ArtifactRef.from_dict(source['model_ref']))
    elif source.get('outputs_ref') and source.get('model_node'):
        outputs = store.load(ArtifactRef.from_dict(source['outputs_ref']))
        try:
            model = outputs[source['model_node']]['predictions'].artifacts['model']
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError('The source run does not contain this fitted model') from error
    elif source.get('bundle'):
        model_name = source['model']
        if model_name not in {item['name'] for item in catalog.describe()}:
            raise ValueError('The imported model plugin is not installed in this worker')
        model = catalog.build(model_name, source.get('config', {}))
    else:
        raise ValueError('Choose a recoverable run or an installed inference bundle')
    adapter = catalog.model_adapters.get(source.get('model'),
        GroupModelAdapter() if 'group' in capabilities else ModelAdapter())
    return {'model': SavedModelHandle(model, adapter, source, outputs)}


def _saved_context(inputs, config):
    handle = inputs['model']
    try:
        context = handle.outputs[config['node']][config['port']]
    except (TypeError, KeyError) as error:
        raise ValueError('The source run does not contain this context') from error
    if not isinstance(context, PipelineContext):
        raise ValueError('Select a context output from the source run')
    if context.metadata.get('partition') not in ('train', 'validation'):
        raise ValueError('Recovered context cannot expose reserved test data')
    return {'context': deepcopy(context)}


def _prepare_saved(inputs, config):
    """Replay supported preparation using frozen training state, never refit."""
    from storm.suite import _subtract_center
    handle, incoming = inputs['model'], inputs['context']
    graph = handle.source.get('source_graph')
    if not graph or handle.outputs is None:
        raise ValueError('Saved preparation requires graph outputs and its source graph')
    nodes = {node['id']: node for node in graph['nodes']}
    edges = {edge['target']: edge['source'] for edge in graph['edges']}
    active = set()

    def replay(endpoint):
        if endpoint in active:
            raise ValueError('Saved preparation replay contains a cycle')
        active.add(endpoint)
        try:
            name, port = endpoint.split('.')
            node = nodes[name]
            kind = node['type']
            if kind.startswith('data.'):
                return deepcopy(incoming)
            if kind == 'transform.center':
                context = replay(edges[name + '.validation'])
                center = deepcopy(handle.outputs[name]['train'].state['center'])
                context.data = [_subtract_center(value, center) for value in context.data]
                context.state['center'] = center
                return context
            handlers = {'adapter.select': _select, 'transform.scale': _scale,
                        'transform.windows': _windows}
            if kind not in handlers:
                raise ValueError(f'Saved preparation replay adapter required for {kind}; refitting is forbidden')
            context = replay(edges[name + '.context'])
            return handlers[kind]({'context': context}, node.get('config', {}))['context']
        finally:
            active.remove(endpoint)

    try:
        context = replay(edges[handle.source['model_node'] + '.validation'])
    except (KeyError, TypeError) as error:
        raise ValueError('Incomplete saved preparation evidence; replay is unavailable') from error
    return {'context': context}


def _infer_saved(inputs, config):
    handle, context = inputs['model'], inputs['validation']
    if context.metadata.get('partition') != 'validation':
        raise ValueError('Saved model inference requires validation; use frozen selection for reserved test')
    contract = handle.source.get('input_contract') or {}
    if contract.get('preparation') == 'internal' and context.metadata.get('prepared_steps'):
        raise ValueError('The recovered model declares internal preparation; use its source inputs')
    missing = set(contract.get('required_steps', []))-set(context.metadata.get('prepared_steps', []))
    if missing:
        raise ValueError(f'Missing preparation required by the saved model: {sorted(missing)}')
    expected = contract.get('shape')
    if expected:
        def shape(value):
            dims=[]
            while hasattr(value,'__len__') and not isinstance(value,(str,bytes)):
                dims.append(len(value))
                if not len(value): break
                value=value[0]
            return tuple(dims)
        actual=shape(context.data[0]) if len(context.data) else ()
        if len(actual)!=len(expected) or any(want is not None and want!=got for want,got in zip(expected,actual)):
            raise ValueError(f'Saved model requires input shape {tuple(expected)}; received {actual}')
    started = perf_counter()
    output = handle.adapter.predict(handle.model, context)
    inference_seconds = perf_counter() - started
    context.state['model_inputs'] = context.data
    context.data = list(output.predictions)
    context.metadata.update(output.metadata)
    context.metadata['timing'] = {'inference_seconds': inference_seconds}
    context.metadata['model_source'] = handle.source.get('label', handle.source.get('model', 'saved'))
    if handle.source.get('config', {}).get('training_population') == 'unknown':
        context.metadata['exploratory'] = True
    context.artifacts['model'] = handle.model
    return {'predictions': context}


def _infer_plugin(inputs, config, catalog, name):
    context = inputs['validation']
    if context.metadata.get('partition') != 'validation':
        raise ValueError('Inference-only models require validation; use frozen selection for reserved test')
    model = catalog.build(name, config)
    adapter = catalog.model_adapters.get(name, ModelAdapter())
    contract = catalog.get(name).input_contract
    handle = SavedModelHandle(model, adapter, {'model': name, 'config': config,
        'input_contract': asdict(contract) if contract is not None else None})
    return _infer_saved({'model': handle, 'validation': context}, {})


def experiment_registry(catalog=None):
    from storm.suite import default_catalog
    catalog = catalog or default_catalog()
    registry = ScientificRegistry()
    from storm.feature_nodes import feature_operations
    for operation in feature_operations():
        registry.register(operation)
    registry.seeders = tuple(catalog.seeders)
    array = {'type': 'array'}
    strings = {'type': 'array', 'items': {'type': 'string'}}
    source_schema = object_schema({'inputs': array, 'targets': array, 'ids': strings,
        'sessions': strings, 'partitions': strings, 'times': {'type': 'array', 'items': {'type': 'number'}},
        'time_axis': object_schema({k: {'type': 'string'} for k in ('clock', 'unit', 'origin', 'scope')},
                                   ('clock', 'unit', 'origin', 'scope')),
        'units': {'type': 'string'}, 'task': {'type': 'string', 'default': 'regression',
                                             'enum': ['regression', 'classification', 'clustering']}},
        ('inputs', 'ids', 'sessions', 'partitions'))
    from storm.feature_nodes import supervised_feature_operation
    registry.register(supervised_feature_operation())
    registry.register(NodeOperation('data.inline', '1', {}, {'train': 'context', 'validation': 'context'},
        source_schema, _inline, kind='load', writes=('data', 'targets', 'metadata', 'schema')))
    registry.register(NodeOperation('transform.scale', '1', {'context': 'context'}, {'context': 'context'},
        object_schema({'factor': {'type': 'number', 'default': 1}}), _scale, reads=('data',), writes=('data',)))
    registry.register(NodeOperation('transform.windows', '1', {'context': 'context'}, {'context': 'context'},
        object_schema({'offsets': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 1}},
                      ('offsets',)), _windows, reads=('data', 'metadata'),
        writes=('data', 'targets', 'metadata', 'state.window_parents')))
    registry.register(NodeOperation('transform.center', '1', {'train': 'context', 'validation': 'context'},
        {'train': 'context', 'validation': 'context'}, object_schema(), _center,
        reads=('data', 'metadata.partitions'), writes=('data', 'state.center')))
    registry.register(NodeOperation('adapter.select', '1', {'context': 'context'}, {'context': 'context'},
        object_schema({'data': {'type': 'string'}, 'targets': {'type': 'string'}}, ('data', 'targets')),
        _select, kind='adapt', reads=('data', 'targets', 'state', 'artifacts'), writes=('data', 'targets')))
    registry.register(NodeOperation('join.align', '1', {'reference': 'context', 'source': 'context'},
        {'context': 'context'}, object_schema({'method': {'type': 'string', 'default': 'exact',
            'enum': ['identity', 'exact', 'previous', 'nearest', 'linear']},
            'tolerance': {'type': 'number', 'minimum': 0}}), _join, kind='join',
        reads=('data', 'metadata'), writes=('state.aligned', 'state.alignment_mask', 'state.alignment_parents')))
    for descriptor in catalog.describe():
        capabilities = set(descriptor['capabilities'])
        if 'infer' not in capabilities or not {'train', 'group'} & capabilities:
            continue
        name = descriptor['name']
        def train(inputs, config, name=name, capabilities=capabilities):
            training, validation = inputs['train'], inputs['validation']
            _partitions(training, validation)
            if not training.data or not validation.data:
                raise ValueError('Explicit nonempty training and validation partitions are required')
            if 'bindings' not in training.state or 'bindings' not in validation.state:
                raise ValueError('Choose model inputs with adapter.select for both partitions')
            contract = catalog.get(name).input_contract
            if contract is not None:
                for context in (training, validation):
                    prepared = context.metadata.get('prepared_steps', [])
                    missing = set(contract.required_steps) - set(prepared)
                    if missing:
                        raise ValueError(f'Missing declared model preparation: {sorted(missing)}')
                    if contract.preparation == 'internal' and prepared:
                        raise ValueError('This model declares internal preparation; bind its source context')
                    if contract.shape is not None:
                        def shape(value):
                            dimensions = []
                            while hasattr(value, '__len__') and not isinstance(value, (str, bytes)):
                                dimensions.append(len(value))
                                if not len(value):
                                    break
                                value = value[0]
                            return tuple(dimensions)
                        if shape(context.data[0]) != contract.shape:
                            raise ValueError(f'Model input shape must be {contract.shape}')
            model = catalog.build(name, config)
            adapter = catalog.model_adapters.get(name,
                GroupModelAdapter() if 'group' in capabilities else ModelAdapter())
            started = perf_counter()
            adapter.fit(model, training)
            training_seconds = perf_counter() - started
            started = perf_counter()
            output = adapter.predict(model, validation)
            inference_seconds = perf_counter() - started
            validation.state['model_inputs'] = validation.data
            validation.data = list(output.predictions)
            validation.metadata.update(output.metadata)
            validation.metadata['timing'] = {'training_seconds': training_seconds,
                                             'inference_seconds': inference_seconds}
            if 'group' in capabilities:
                validation.metadata['task'] = 'clustering'
            validation.artifacts['model'] = model
            return {'predictions': validation}
        registry.register(NodeOperation('model.' + name, descriptor['version'],
            {'train': 'context', 'validation': 'context'}, {'predictions': 'context'},
            descriptor['schema'], train, kind='model', reads=('data', 'targets', 'state', 'artifacts'),
            writes=('data', 'artifacts.model'),
            validate_config=lambda config, name=name: catalog.normalize(name, config),
            descriptor={**(descriptor.get('descriptor') or {}),
                        'input_contract': descriptor.get('input_contract')}))
    for descriptor in catalog.describe():
        capabilities = set(descriptor['capabilities'])
        if 'infer' in capabilities and not {'train','group'} & capabilities:
            name = descriptor['name']
            def infer(inputs, config, name=name):
                return _infer_plugin(inputs, config, catalog, name)
            registry.register(NodeOperation('model.'+name, descriptor['version'],
                {'validation':'context'}, {'predictions':'context'}, descriptor['schema'], infer,
                kind='model', reads=('data','metadata','state'), writes=('data','metadata','artifacts.model'),
                validate_config=lambda config,name=name: catalog.normalize(name,config),
                descriptor={**(descriptor.get('descriptor') or {}),
                    'label': descriptor['descriptor'].get('label',name) if descriptor.get('descriptor') else name,
                    'input_contract':descriptor.get('input_contract')}))
    registry.register(NodeOperation('adapter.saved_context', '1',
        {'model': 'model'}, {'context': 'context'}, object_schema({
            'node': {'type': 'string'}, 'port': {'type': 'string', 'default': 'context'}}, ('node',)),
        _saved_context, kind='adapt', writes=('data', 'targets', 'metadata', 'state', 'artifacts'),
        descriptor={'label': 'Contexto de la corrida guardada',
            'description': 'Recupera datos preparados, identidad y partición del artefacto original, sin volver a ajustarlos.'}))
    registry.register(NodeOperation('adapter.saved_preparation', '1',
        {'model': 'model', 'context': 'context'}, {'context': 'context'}, object_schema(),
        _prepare_saved, kind='adapt', reads=('data', 'targets', 'metadata', 'state'),
        writes=('data', 'targets', 'state'), descriptor={
            'label': 'Preparación del modelo guardado',
            'description': 'Reutiliza centrado ajustado, escala, ventanas y enlaces del grafo original. Otros pasos requieren un adaptador de replay.'}))
    registry.register(NodeOperation('model.saved', '1', {}, {'model':'model'},
        object_schema({'source':{'type':'object'}},('source',)),
        lambda inputs,config: _load_saved_model(inputs,config,catalog),kind='adapt',
        writes=('artifacts.model',),descriptor={'category':'models','label':'Recuperar modelo entrenado o bundle',
            'description':'Carga un artefacto de una corrida anterior o un bundle de inferencia instalado.'}))
    registry.register(NodeOperation('model.infer_saved', '1', {'model':'model','validation':'context'},
        {'predictions':'context'},object_schema(),_infer_saved,kind='model',
        reads=('data','metadata','state','artifacts.model'),writes=('data','metadata','artifacts.model'),
        descriptor={'category':'models','label':'Inferencia con modelo recuperado'}))
    for name in catalog.metrics.available:
        metric = catalog.metrics.get(name)
        task = getattr(metric, 'compatible_task', None)
        if task is None:
            continue  # Scientific task declarations are mandatory in the new API.
        def evaluate(inputs, config, name=name, task=task):
            context = inputs['context']
            if context.metadata.get('task') != task:
                raise ValueError('Metric task is incompatible with model outputs')
            metric = catalog.metrics.get(name)
            count = len(context.data)
            masks = [context.metadata.get(key, [True] * count) for key in ('prediction_mask', 'evaluation_mask')]
            if any(len(mask) != count or any(type(v) is not bool for v in mask) for mask in masks):
                raise ValueError('Evaluation masks must align with output observations')
            selected = [i for i in range(count) if all(mask[i] for mask in masks)]
            if not selected:
                raise ValueError('No valid observations for this metric')
            features = context.state.get('model_inputs', context.data)
            dataset = Dataset(inputs=[features[i] for i in selected],
                targets=[context.targets[i] for i in selected] if context.targets is not None else None)
            value = metric.evaluate(dataset=dataset,
                output=ModelOutput([context.data[i] for i in selected]), model=context.artifacts.get('model'))
            return {'metric': {**catalog.metrics.describe(name), 'task': task,
                'partition': context.metadata['partition'], 'value': float(value), 'parameters': {},
                'observation_ids': [context.metadata['observation_ids'][i] for i in selected]}}
        registry.register(NodeOperation('metric.' + name, catalog.metrics.describe(name)['version'],
            {'context': 'context'}, {'metric': 'metric'}, object_schema(), evaluate, kind='evaluate',
            reads=('data', 'targets', 'metadata.partitions'), writes=()))
    for name, adapter in catalog.data_adapters.items():
        def load(inputs, config, adapter=adapter):
            value = adapter(config)
            return {'context': value} if adapter.outputs == ('context',) else value
        def preview(inputs, config, adapter=adapter):
            value = adapter.preview(config)
            return {'context': value} if adapter.outputs == ('context',) else value
        registry.register(NodeOperation('data.' + name, adapter.version, {},
            {port: 'context' for port in adapter.outputs}, adapter.schema, load, kind='load',
            preview=preview if adapter.preview is not None else None,
            descriptor={'contents': adapter.contents}, writes=('data', 'targets', 'metadata', 'schema')))
    for operation in catalog.experiment_nodes:
        registry.register(operation)
    return registry


def run_study(study, registry):
    runs = expand_study(study, registry)
    for run in runs:
        random.seed(run['seed'])
        for seeder in getattr(registry, 'seeders', ()):
            seeder(run['seed'])
        run['result'] = ExperimentExecutor(registry).run(run['graph'])
    return runs
