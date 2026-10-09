"""Versioned experiment DAGs. Runtime values never enter the scientific plan."""
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import product
from time import monotonic
from typing import Callable

from storm.config import fingerprint, json_compatible
from storm.contracts import ValidationProblem
from storm.graphs import ordered_nodes, validate_graph


class ExperimentValidationError(ValueError):
    def __init__(self, problems):
        self.problems = problems
        super().__init__('; '.join(f'{p.component}.{p.field}: {p.message}' for p in problems))


@dataclass(frozen=True)
class NodeOperation:
    name: str
    version: str
    inputs: dict
    outputs: dict
    schema: dict
    execute: Callable
    kind: str = 'transform'
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    # A plugin owning noncopyable resources must supply an isolating fork.
    fork: Callable = deepcopy
    validate_config: Callable | None = None
    preview: Callable | None = None
    descriptor: dict = field(default_factory=dict)

    def describe(self):
        return json_compatible({'name': self.name, 'version': self.version,
            'kind': self.kind, 'inputs': self.inputs, 'outputs': self.outputs,
            'schema': self.schema, 'reads': self.reads, 'writes': self.writes,
            'bounded_preview': self.preview is not None, 'descriptor': self.descriptor})


class ExperimentRegistry:
    def __init__(self):
        self.operations = {}

    def register(self, operation):
        if operation.name in self.operations:
            raise ValueError(f'Duplicate operation: {operation.name}')
        fingerprint(operation.describe())
        self.operations[operation.name] = operation

    def describe(self):
        return {name: op.describe() for name, op in self.operations.items()}

    def validate(self, graph):
        problems = validate_graph(graph, self.describe())
        if problems:
            return problems
        try:
            fingerprint(scientific_spec(graph))
        except (ValueError, TypeError) as error:
            problems.append(ValidationProblem('experiment.serialization', 'error', 'root', '', 'graph', str(error)))
        for node in graph['nodes']:
            operation = self.operations[node['type']]
            if operation.validate_config is not None:
                from storm.suite import Catalog
                try:
                    resolved_config = Catalog._validate_value(node['id'], node.get('config', {}), operation.schema)
                    operation.validate_config(resolved_config)
                except (TypeError, ValueError, KeyError) as error:
                    problems.append(ValidationProblem('experiment.config', 'error', 'root',
                        node['id'], 'config', str(error)))
            if node.get('version', operation.version) != operation.version:
                problems.append(ValidationProblem('experiment.version', 'error', 'root',
                    node['id'], 'version', 'Unsupported component version'))
        return problems

    def normalize(self, graph):
        from storm.suite import Catalog
        problems = self.validate(graph)
        if problems:
            raise ExperimentValidationError(problems)
        normalized = scientific_spec(graph)
        for node in normalized['nodes']:
            operation = self.operations[node['type']]
            if node.get('version', operation.version) != operation.version:
                raise ValueError(f"Unsupported version for {node['id']}")
            node['version'] = operation.version
            node['config'] = Catalog._validate_value(node['id'], node.get('config', {}), operation.schema)
        fingerprint(normalized)  # Reject NaN and runtime objects before any execution.
        return normalized


def scientific_spec(graph):
    unknown = set(graph) - {'version', 'nodes', 'edges', 'visual'}
    if unknown:
        raise ValueError(f'Unknown experiment fields: {sorted(unknown)}')
    result = deepcopy({key: graph[key] for key in ('version', 'nodes', 'edges')})
    for node in result['nodes']:
        if set(node) - {'id', 'type', 'version', 'config'}:
            raise ValueError('Node layout belongs in visual, outside the scientific specification')
    result['nodes'].sort(key=lambda node: node['id'])
    result['edges'].sort(key=lambda edge: (edge['target'], edge['source']))
    return result


def scientific_fingerprint(graph):
    return fingerprint(scientific_spec(graph))


@dataclass
class ExperimentResult:
    status: str
    requested: dict
    resolved: dict
    fingerprint: str
    records: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)

    def manifest(self):
        return {'version': '1', 'status': self.status, 'requested': self.requested,
                'resolved': self.resolved, 'fingerprint': self.fingerprint,
                'nodes': deepcopy(self.records)}


class ExperimentExecutor:
    def __init__(self, registry):
        self.registry = registry

    @classmethod
    def run_legacy(cls, callback, *, semantics):
        """Run a legacy atomic operation through the DAG scheduler without reinterpretation.

        The operation retains its historical partition/metric lifecycle. Migration
        to individual scientific nodes is explicit, not inferred from old plans.
        """
        registry = ExperimentRegistry()
        errors = []
        def execute(inputs, config):
            try:
                return {'result': callback()}
            except Exception as error:
                errors.append(error)
                raise
        registry.register(NodeOperation(semantics, '1', {}, {'result': 'legacy_result'},
            {'type': 'object', 'properties': {}}, execute, kind='legacy'))
        result = cls(registry).run({'version': '1', 'nodes': [{'id': 'legacy', 'type': semantics}], 'edges': []})
        if errors:
            raise errors[0]
        return result.outputs['legacy']['result']

    def run(self, graph, *, preview=False):
        resolved = self.registry.normalize(graph)
        result = ExperimentResult('complete', scientific_spec(graph), resolved,
                                  scientific_fingerprint(resolved))
        nodes = {node['id']: node for node in resolved['nodes']}
        for name in ordered_nodes(resolved):
            node = nodes[name]
            operation = self.registry.operations[node['type']]
            edges = [edge for edge in resolved['edges'] if edge['target'].split('.')[0] == name]
            parents = [edge['source'] for edge in edges]
            record = {'status': 'blocked', 'parents': parents,
                      'type': node['type'], 'version': operation.version}
            result.records[name] = record
            if any(result.records[p.split('.')[0]]['status'] != 'completed' for p in parents):
                record['error'] = 'An upstream node failed or was blocked'
                continue
            started = monotonic()
            try:
                inputs = {}
                for edge in edges:
                    source, port = edge['source'].split('.')
                    inlet = edge['target'].split('.')[1]
                    producer = self.registry.operations[nodes[source]['type']]
                    inputs[inlet] = producer.fork(result.outputs[source][port])
                callback = operation.preview if preview and operation.preview is not None else operation.execute
                if preview and operation.kind == 'load' and operation.name != 'data.inline' and operation.preview is None:
                    raise ValueError('External sources require a bounded preview callback')
                outputs = callback(inputs, deepcopy(node['config']))
                if not isinstance(outputs, dict) or set(outputs) != set(operation.outputs):
                    raise ValueError('Node must produce exactly its declared output ports')
                from storm.pipeline import PipelineContext
                for port, value in outputs.items():
                    if operation.outputs[port] == 'context' and not isinstance(value, PipelineContext):
                        raise TypeError(f'{port} must produce PipelineContext')
                result.outputs[name] = outputs
                record['status'] = 'completed'
            except Exception as error:
                record.update(status='failed', error=str(error), error_type=type(error).__name__)
            record['duration_seconds'] = monotonic() - started
        statuses = [r['status'] for r in result.records.values()]
        if any(s != 'completed' for s in statuses):
            # A successful source alone does not make a failed workflow partial.
            leaves = set(nodes) - {edge['source'].split('.')[0] for edge in resolved['edges']}
            result.status = ('partial' if any(result.records[n]['status'] == 'completed' for n in leaves)
                             else 'failed')
        return result


def _set_parameter(graph, path, value):
    parts = path.split('.')
    node = next((n for n in graph['nodes'] if n['id'] == parts[0]), None)
    if node is None or len(parts) < 2:
        raise ValueError(f'Parameter must name node.field: {path}')
    config = node.setdefault('config', {})
    for part in parts[1:-1]:
        config = config.setdefault(part, {})
    config[parts[-1]] = deepcopy(value)


def expand_study(study, registry):
    """Resolve the complete finite batch before the caller creates any jobs."""
    variants = study.get('variants', [{'id': 'default', 'parameters': {}}])
    seeds = study.get('seeds', [0])
    sweep = study.get('sweep', {})
    if (not isinstance(variants, list) or not variants or
            any(not isinstance(v, dict) or not isinstance(v.get('id'), str) or not v['id'] for v in variants)
            or len({v['id'] for v in variants}) != len(variants)):
        raise ValueError('Variant IDs must be nonempty and unique')
    if (not isinstance(seeds, list) or not seeds or any(type(s) is not int for s in seeds)
            or len(set(seeds)) != len(seeds)):
        raise ValueError('Seeds must be a finite nonempty list of distinct integers')
    if not isinstance(sweep, dict) or any(not isinstance(v, list) or not v for v in sweep.values()):
        raise ValueError('Each sweep must be a finite nonempty list')
    paths = sorted(sweep)
    runs, problems = [], []
    for variant in variants:
        for combination in product(*(sweep[path] for path in paths)):
            for seed in seeds:
                graph = deepcopy(study['graph'])
                parameters = {**variant.get('parameters', {}), **dict(zip(paths, combination))}
                try:
                    for path, value in parameters.items():
                        _set_parameter(graph, path, value)
                    resolved = registry.normalize(graph)
                except ExperimentValidationError as error:
                    problems.extend(error.problems)
                    continue
                run = {'variant_id': variant['id'], 'seed': seed, 'parameters': parameters,
                       'graph': resolved}
                run['run_id'] = fingerprint(run).replace('sha256:', 'run-')
                runs.append(run)
    if problems:
        raise ExperimentValidationError(problems)
    if len({r['run_id'] for r in runs}) != len(runs):
        raise ValueError('Duplicate sweep configurations')
    return runs
