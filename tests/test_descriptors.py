"""Reflection describes Python parameters without constructing implementations."""
from dataclasses import dataclass
import json
import pytest
from storm.testing.models import ConstantModel


class ResolvedConstant(ConstantModel):
    def configuration_snapshot(self):
        return {'effective_value': self.value}


def test_nested_configuration_is_typed_and_defaults_are_independent():
    from storm.suite import Component, Catalog
    catalog = Catalog()
    catalog.register(Component('nested', lambda c: None, (), {'type': 'object', 'properties': {
        'options': {'type': 'object', 'default': {}, 'properties': {
            'epochs': {'type': 'integer', 'minimum': 2, 'default': 2},
            'points': {'type': 'array', 'items': {'type': 'string'}, 'default': []}}}}}))
    with pytest.raises(ValueError, match='options.epochs'):
        catalog.normalize('nested', {'options': {'epochs': 1}})
    with pytest.raises(ValueError, match=r'options.points\[0\]'):
        catalog.normalize('nested', {'options': {'points': [4]}})
    result = catalog.normalize('nested', {})
    assert result == {'options': {'epochs': 2, 'points': []}}
    result['options']['points'].append('nose')
    assert catalog.normalize('nested', {})['options']['points'] == []


def test_reflect_function_dataclass_and_declared_kwargs():
    from storm.descriptors import reflect_schema
    def options(epochs: int = 2, keypoints: list[str] = (), *, enabled: bool = True):
        raise AssertionError('must not execute')
    schema = reflect_schema(options)
    assert schema['properties']['epochs'] == {'type': 'integer', 'default': 2}
    assert schema['properties']['keypoints']['items'] == {'type': 'string'}
    json.dumps(schema)
    @dataclass
    class Config:
        states: int = 10
    assert reflect_schema(Config)['properties']['states']['default'] == 10
    def arbitrary(**kwargs): pass
    with pytest.raises(ValueError, match='declared schema'):
        reflect_schema(arbitrary)
    declared = {'type': 'object', 'properties': {'rate': {'type': 'number'}}}
    assert reflect_schema(arbitrary, declared=declared) == declared


def test_descriptor_snapshot_does_not_build_model():
    from storm.suite import Component, Catalog
    def forbidden(config): raise AssertionError('constructed model')
    catalog = Catalog()
    catalog.register(Component('example', forbidden, (), {}, descriptor={
        'version': '1', 'backend': 'example', 'effects': ['architecture']}))
    result = catalog.describe()[0]
    assert result['descriptor']['backend'] == 'example'
    assert result['descriptor_fingerprint'].startswith('sha256:')
    json.dumps(result)


def test_execution_keeps_requested_and_normalized_config_and_descriptor(tmp_path):
    from storm.suite import default_catalog, execute
    result = execute({'model': 'constant', 'config': {}, 'data': {
        'inputs': [1, 2], 'targets': [1, 2], 'train': [0], 'test': [1]}}, tmp_path, 'trace', default_catalog())
    assert result['configuration']['requested'] == {}
    assert result['configuration']['normalized'] == {'value': 0}
    assert result['component_snapshot']['source_sha256'].startswith('sha256:')
    assert result['component_snapshot']['name'] == 'constant'


def test_checkpoint_rejects_changed_descriptor_with_same_version(tmp_path):
    from dataclasses import replace
    from storm.suite import default_catalog, execute
    from storm.artifacts import FileArtifactStore
    spec = {'model': 'online_mean', 'data': {'inputs': [1, 2], 'targets': [1, 2],
                                             'train': [0], 'test': [1]}}
    original = default_catalog()
    execute(spec, tmp_path, 'first', original)
    reference = FileArtifactStore(tmp_path).resolve(kind='checkpoints', artifact_id='first')
    changed = default_catalog()
    changed._components['online_mean'] = replace(original.get('online_mean'), descriptor={'algorithm': 'changed'})
    with pytest.raises(ValueError, match='component identity'):
        execute(spec, tmp_path, 'second', changed, resume_from=reference)


def test_plugin_can_record_resolved_runtime_configuration(tmp_path):
    from storm.suite import Component, default_catalog, execute
    catalog = default_catalog()
    catalog.register(Component('resolved', ResolvedConstant, ('train', 'infer'), {}))
    result = execute({'model': 'resolved', 'data': {'inputs': [1, 2], 'targets': [1, 2],
        'train': [0], 'test': [1]}}, tmp_path, 'resolved', catalog)
    assert result['configuration']['resolved'] == {'effective_value': 0}
