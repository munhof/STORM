"""Declarative preflight contracts; no web or scientific runtime dependencies."""
from dataclasses import asdict, dataclass
from math import isfinite
from typing import Callable, Protocol, runtime_checkable


@dataclass(frozen=True)
class ModelInputContract:
    version: str = '1'
    input_type: str = 'unknown'
    preparation: str = 'unknown'
    required_steps: tuple[str, ...] = ()
    granularity: str = 'observation'
    shape: tuple[int, ...] | None = None

    def __post_init__(self):
        if self.preparation not in ('external', 'internal', 'unknown'):
            raise ValueError('Unknown preparation mode')
        if self.granularity not in ('observation', 'session'):
            raise ValueError('Unknown input granularity')


@dataclass(frozen=True)
class ValidationProblem:
    code: str
    severity: str
    branch: str
    component: str
    field: str
    message: str

    def to_dict(self):
        return asdict(self)


@runtime_checkable
class PreparationResolver(Protocol):
    def __call__(self, steps: list[dict], feature_names: list[str]) -> list[dict]: ...


@runtime_checkable
class ProgressReporter(Protocol):
    def set_progress_callback(self, callback: Callable[[dict], None] | None) -> None: ...


class PlanValidationError(ValueError):
    def __init__(self, problems):
        self.problems = problems
        super().__init__('; '.join(f'{p.branch}: {p.message}' for p in problems
                                  if p.severity == 'error'))


def require_valid_plan(spec, catalog, data_summary=None):
    problems = validate_plan(spec, catalog, data_summary)
    if any(p.severity == 'error' for p in problems):
        raise PlanValidationError(problems)
    return problems


def _shape(value):
    result = []
    while isinstance(value, (list, tuple)):
        result.append(len(value))
        value = value[0] if value else None
    return result


def _materialized_steps(summary):
    preparation = (summary or {}).get('preparation') or {}
    fingerprint = preparation.get('fingerprint', '')
    verified = (preparation.get('validated') is True and isinstance(fingerprint, str)
                and fingerprint.startswith('sha256:') and len(fingerprint) == 71
                and all(c in '0123456789abcdef' for c in fingerprint[7:]))
    steps = preparation.get('resolved_steps', [])
    return steps if verified and isinstance(steps, list) else []


def validate_plan(spec, catalog, data_summary=None):
    """Validate each branch without loading sources or constructing models.

    A host may supply verified preparation metadata in data_summary. Bare legacy
    preapplied_steps are not evidence that a dataset has been prepared.
    Unknown scientific facts produce warnings, never inferred compatibility.
    """
    problems = []
    if not isinstance(spec, dict):
        return [ValidationProblem('plan.invalid', 'error', 'root', '', '', 'Plan must be an object')]
    summary = dict(data_summary or {})
    data = spec.get('data') or {}
    if isinstance(data, dict):
        summary.setdefault('feature_names', data.get('feature_names', []))
        if isinstance(data.get('inputs'), list) and data['inputs']:
            summary.setdefault('shape', _shape(data['inputs'][0]))
    names = spec.get('branch_models') or []
    configs = spec.get('branch_configs') or {}
    if not isinstance(names, list) or not isinstance(configs, dict):
        return [ValidationProblem('branches.invalid', 'error', 'root', '', 'branch_models',
                                  'Branches and configurations must be a list and object')]
    branches = [('root', spec.get('model'), spec.get('config', {}))]
    branches.extend((str(name), name, configs.get(name, {})) for name in names
                    if isinstance(name, str) and name != spec.get('model'))
    for branch, name, config in branches:
        def add(code, field, message, severity='error'):
            problems.append(ValidationProblem(code, severity, branch, str(name or ''), field, message))
        try:
            component = catalog.get(name)
        except (KeyError, TypeError):
            add('component.unknown', 'model', 'Select a registered model')
            continue
        try:
            catalog.normalize(name, config)
        except (ValueError, TypeError, KeyError) as error:
            add('config.invalid', 'config' if branch == 'root' else 'branch_configs', str(error))
        steps = spec.get('steps') or []
        if not isinstance(steps, list):
            add('preparation.invalid', 'steps', 'Steps must be a list')
            continue
        if any(not isinstance(step, dict) or not isinstance(step.get('type'), str)
               or not isinstance(step.get('config', {}), dict) for step in steps):
            add('preparation.invalid', 'steps', 'Each step needs a type and object configuration')
            continue
        contract = component.input_contract
        prior = _materialized_steps(summary)
        configured = {step.get('type') for step in steps + prior if isinstance(step, dict)
                      and isinstance(step.get('type'), str)}
        if contract is None or contract.preparation == 'unknown':
            add('input.unknown', 'model', 'Input requirements are unknown for this legacy component', 'warning')
        elif contract.preparation == 'internal' and (steps or prior or spec.get('preapplied_steps')):
            add('preparation.external_forbidden', 'steps', 'This model rejects external preparation steps')
        if contract:
            for required in contract.required_steps:
                if required not in configured:
                    add('preparation.required_step', 'steps', f'Model requires preparation step: {required}')
            if contract.granularity == 'session':
                full = summary.get('full_sessions')
                if full is False:
                    add('input.full_sessions', 'dataset_revision_id', 'Model requires complete session sources')
                elif full is not True:
                    add('input.sessions_unknown', 'dataset_revision_id', 'Complete session sources must be verified by the worker', 'warning')
        resolved = steps
        resolver = getattr(catalog, 'preparation_resolver', None)
        if resolver and steps:
            if summary.get('feature_names'):
                try:
                    resolved = resolver(steps, summary['feature_names'])
                except (ValueError, TypeError, KeyError) as error:
                    add('preparation.resolve', 'steps', str(error))
                    continue
            else:
                add('preparation.schema_unknown', 'steps', 'Feature schema is unavailable for declarative preparation', 'warning')
                for step in steps:
                    if step['type'] not in (*catalog.steps.available, 'center'):
                        add('preparation.invalid', 'steps', f"Unknown preparation step: {step['type']}")
                resolved = []
        for step in resolved:
            try:
                if not isinstance(step, dict):
                    raise ValueError('Each step must be an object')
                if step.get('type') == 'center':
                    continue
                if step.get('type') == 'scale':
                    if not isfinite(float(step['factor'])):
                        raise ValueError('Scale must be finite')
                else:
                    catalog.steps.build(step['type'], step.get('config', {}))
            except (ValueError, KeyError, TypeError) as error:
                add('preparation.invalid', 'steps', str(error))
        # Only compare materialized shapes. The engine cannot infer the output
        # schema of arbitrary scientific transformations.
        shape = summary.get('shape') if not steps else None
        if contract and not steps and summary.get('input_type') is not None:
            if contract.input_type != 'unknown' and summary['input_type'] != contract.input_type:
                add('input.type', 'data', f'Expected input type {contract.input_type}')
        if contract and contract.shape:
            if shape is None:
                add('input.shape_unknown', 'data', 'Input shape must be verified by the worker', 'warning')
            elif tuple(shape) != contract.shape:
                add('input.shape', 'data', f'Expected input shape {contract.shape}, received {tuple(shape)}')
    return problems
