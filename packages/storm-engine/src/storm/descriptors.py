"""Opt-in parameter reflection. Scientific contracts remain plugin declarations.

Call this in the backend environment and export the JSON descriptor to Studio.
Reflection never invokes a model, imports annotation strings or walks packages.
"""
from copy import deepcopy
from dataclasses import MISSING, fields, is_dataclass
import inspect
from importlib.util import find_spec
import hashlib
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from typing import get_args, get_origin, Literal


def component_snapshot(component):
    from dataclasses import asdict
    from storm.config import fingerprint
    descriptor = deepcopy(component.descriptor)
    source_hash = None
    try:
        source = inspect.getsourcefile(component.builder)
        if source:
            source_hash = 'sha256:' + hashlib.sha256(Path(source).read_bytes()).hexdigest()
    except (TypeError, OSError):
        pass
    dependencies = {}
    for package in (descriptor or {}).get('dependencies', []):
        try:
            dependencies[package] = version(package)
        except PackageNotFoundError:
            dependencies[package] = None
    snapshot = {'name': component.name, 'version': component.version,
        'schema': deepcopy(component.schema), 'descriptor': descriptor,
        'input_contract': asdict(component.input_contract) if component.input_contract else None,
        'builder': f'{getattr(component.builder, "__module__", type(component.builder).__module__)}.'
                   f'{getattr(component.builder, "__qualname__", type(component.builder).__qualname__)}',
        'source_sha256': source_hash, 'dependencies': dependencies}
    source_modules = {}
    for module in (descriptor or {}).get('source_modules', []):
        try:
            origin = find_spec(module).origin
            source_modules[module] = 'sha256:' + hashlib.sha256(Path(origin).read_bytes()).hexdigest()
        except (AttributeError, ImportError, ValueError, TypeError, OSError):
            source_modules[module] = None
    snapshot['source_modules'] = source_modules
    snapshot['identity_fingerprint'] = fingerprint({key: value for key, value in snapshot.items()
                                                    if key != 'dependencies'})
    snapshot['fingerprint'] = fingerprint(snapshot)
    return snapshot


def _type_schema(annotation):
    primitives = {int: 'integer', float: 'number', str: 'string', bool: 'boolean',
                  dict: 'object', list: 'array'}
    if annotation in primitives:
        return {'type': primitives[annotation]}
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is list:
        return {'type': 'array', 'items': _type_schema(args[0])}
    if origin is Literal:
        schema = _type_schema(type(args[0]))
        schema['enum'] = list(args)
        return schema
    raise ValueError(f'Unsupported annotation {annotation!r}; supply a declared schema')


def reflect_schema(target, *, declared=None):
    """Describe supported signatures/dataclasses; explicit schemas take precedence.

    Pydantic models can supply model_json_schema() as a declared schema. A host
    must validate that schema against its supported vocabulary before use.
    Unannotated parameters and **kwargs require explicit declarations.
    """
    if declared is not None:
        return deepcopy(declared)
    properties, required = {}, []
    if is_dataclass(target):
        parameters = [(f.name, f.type, f.default if f.default is not MISSING else
                       f.default_factory() if f.default_factory is not MISSING else
                       inspect.Parameter.empty) for f in fields(target) if f.init]
    else:
        parameters = []
        for parameter in inspect.signature(target).parameters.values():
            if parameter.name in ('self', 'cls'):
                continue
            if parameter.kind in (parameter.VAR_KEYWORD, parameter.VAR_POSITIONAL,
                                  parameter.POSITIONAL_ONLY):
                raise ValueError('Variadic or positional-only parameters require a declared schema')
            parameters.append((parameter.name, parameter.annotation, parameter.default))
    for name, annotation, default in parameters:
        descriptor = _type_schema(annotation)
        if default is inspect.Parameter.empty:
            required.append(name)
        else:
            descriptor['default'] = list(default) if isinstance(default, tuple) else deepcopy(default)
        properties[name] = descriptor
    return {'type': 'object', 'properties': properties, 'required': required,
            'additionalProperties': False}
