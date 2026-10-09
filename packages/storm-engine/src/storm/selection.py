"""Explicit held-out evaluation for frozen reference-node workflows.

Custom data sources and transformations must provide a replay adapter before
held-out evaluation is enabled. Unknown preparation never falls back to refit.
"""
from copy import deepcopy
from dataclasses import dataclass
from storm.adapters import ModelAdapter
from storm.experiments import scientific_fingerprint
from storm.pipeline import PipelineContext


@dataclass(frozen=True)
class FrozenSelection:
    graph: dict
    fingerprint: str
    model_node: str
    outputs: dict


def freeze_selection(result, model_node):
    if result.status != 'complete' or model_node not in result.outputs:
        raise ValueError('Select a model from a complete validation run')
    context = result.outputs[model_node].get('predictions')
    if context is None or context.metadata.get('partition') != 'validation':
        raise ValueError('Selection requires independent validation predictions')
    return FrozenSelection(deepcopy(result.resolved), result.fingerprint,
                           model_node, deepcopy(result.outputs))


def evaluate_reserved(selection):
    from storm.experiment_nodes import _select, _scale, _windows
    from storm.suite import _subtract_center
    if scientific_fingerprint(selection.graph) != selection.fingerprint:
        raise ValueError('The frozen scientific selection was modified')
    nodes = {n['id']: n for n in selection.graph['nodes']}
    edges = {e['target']: e['source'] for e in selection.graph['edges']}
    def replay(endpoint):
        name, port = endpoint.split('.')
        node, config = nodes[name], nodes[name].get('config', {})
        if node['type'] == 'data.inline':
            indices = [i for i, p in enumerate(config['partitions']) if p == 'test']
            if not indices:
                raise ValueError('No reserved test observations')
            return PipelineContext(data=[config['inputs'][i] for i in indices],
                targets=[config['targets'][i] for i in indices] if config.get('targets') else None,
                metadata={'partition': 'test', 'partitions': ['test'] * len(indices),
                    'sessions': [config['sessions'][i] for i in indices],
                    'observation_ids': [config['ids'][i] for i in indices],
                    'observation_indices': indices})
        if node['type'] == 'transform.center':
            context = replay(edges[name + '.validation'])
            center = selection.outputs[name]['train'].state['center']
            context.data = [_subtract_center(v, center) for v in context.data]
            context.state['center'] = deepcopy(center)
            return context
        handlers = {'adapter.select': _select, 'transform.scale': _scale, 'transform.windows': _windows}
        if node['type'] not in handlers:
            raise ValueError(f"Held-out replay adapter required for {node['type']}; refitting is forbidden")
        return handlers[node['type']]({'context': replay(edges[name + '.context'])}, config)['context']
    context = replay(edges[selection.model_node + '.validation'])
    model = deepcopy(selection.outputs[selection.model_node]['predictions'].artifacts['model'])
    output = ModelAdapter().predict(model, context)
    return {'selection_fingerprint': selection.fingerprint, 'model_node': selection.model_node,
        'partition': 'test', 'predictions': list(output.predictions), 'targets': context.targets,
        'observation_ids': context.metadata['observation_ids']}
