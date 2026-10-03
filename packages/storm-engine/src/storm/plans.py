"""Immutable, independent execution specifications for an editable plan."""
from copy import deepcopy


def branch_specs(spec):
    names = spec.get('branch_models') or []
    configs = spec.get('branch_configs') or {}
    overrides = spec.get('branch_overrides') or {}
    if (not isinstance(names, list) or any(not isinstance(n, str) for n in names)
            or not isinstance(configs, dict) or not isinstance(overrides, dict)):
        raise ValueError('Branches and configurations must be a list and objects')
    if set(overrides) - set(names):
        raise ValueError('Overrides must refer to declared branches')
    root = deepcopy(spec)
    for key in ('branch_models', 'branch_configs', 'branch_overrides', 'execution_variant'):
        root.pop(key, None)
    result = [('root', root)]
    for name in dict.fromkeys(names):
        if name == spec.get('model'):
            continue
        override = overrides.get(name, {})
        if not isinstance(override, dict):
            raise ValueError('Branch overrides must be objects')
        allowed = {'data', 'connector', 'dataset_revision_id', 'preparation_revision_id', 'steps'}
        if set(override) - allowed:
            raise ValueError('Unknown branch override fields')
        branch = deepcopy(root)
        branch.update(model=name, config=deepcopy(configs.get(name, {})))
        if {'data', 'dataset_revision_id', 'steps', 'preparation_revision_id'} & set(override):
            branch.pop('data_summary', None)
            branch.pop('preapplied_steps', None)
        if {'data', 'dataset_revision_id', 'steps'} & set(override) and 'preparation_revision_id' not in override:
            branch.pop('preparation_revision_id', None)
        if 'data' in override and 'dataset_revision_id' not in override:
            branch.pop('dataset_revision_id', None)
        branch.update(deepcopy(override))
        result.append((name, branch))
    return result
