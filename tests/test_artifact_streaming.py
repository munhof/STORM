import pytest


class UnserializableCheckpoint:
    def __reduce__(self):
        raise RuntimeError('serialization interrupted')


def test_failed_manifest_publication_keeps_previous_checkpoint(tmp_path, monkeypatch):
    from storm.artifacts import FileArtifactStore
    store = FileArtifactStore(tmp_path)
    previous = store.save(kind='checkpoints', artifact_id='live', value={'epoch': 1})
    original = store._atomic_write
    def interrupted(path, content):
        if path.name == 'manifest.json':
            raise RuntimeError('manifest interrupted')
        return original(path, content)
    monkeypatch.setattr(store, '_atomic_write', interrupted)
    with pytest.raises(RuntimeError, match='manifest interrupted'):
        store.save(kind='checkpoints', artifact_id='live', value={'epoch': 2})
    assert store.load(previous) == {'epoch': 1}
    monkeypatch.setattr(store, '_atomic_write', original)
    current = store.save(kind='checkpoints', artifact_id='live', value={'epoch': 2})
    assert store.load(current) == {'epoch': 2}


def test_failed_checkpoint_serialization_keeps_previous_artifact(tmp_path):
    from storm.artifacts import FileArtifactStore

    store = FileArtifactStore(tmp_path)
    reference = store.save(kind='checkpoints', artifact_id='continuation',
                           value={'epoch': 1, 'optimizer': {'step': 10}})
    with pytest.raises(RuntimeError, match='serialization interrupted'):
        store.save(kind='checkpoints', artifact_id='continuation',
                   value=UnserializableCheckpoint())
    assert store.load(reference) == {'epoch': 1, 'optimizer': {'step': 10}}
    assert {path.name for path in (tmp_path / reference.uri).iterdir()} == {
        'payload.pkl', 'manifest.json'}


def test_bundle_exports_the_payload_selected_by_the_manifest(tmp_path):
    import hashlib
    import io
    import json
    import zipfile
    from storm.artifacts import FileArtifactStore
    from storm_studio.study_bundles import _add_artifacts

    store = FileArtifactStore(tmp_path)
    store.save(kind='checkpoints', artifact_id='live', value={'epoch': 1})
    reference = store.save(kind='checkpoints', artifact_id='live', value={'epoch': 2})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        _add_artifacts(archive, [], set(), {('checkpoints', 'live'): reference.to_dict()}, tmp_path)
    with zipfile.ZipFile(stream) as archive:
        base = 'artifacts/checkpoints/live/'
        manifest = json.loads(archive.read(base + 'manifest.json'))
        payload = archive.read(base + manifest['payload_file'])
        assert 'sha256:' + hashlib.sha256(payload).hexdigest() == reference.digest


def test_artifact_round_trip_avoids_full_payload_byte_buffers(tmp_path, monkeypatch):
    from pathlib import Path
    from storm.artifacts import FileArtifactStore
    import storm.artifacts.local as local

    monkeypatch.setattr(local.pickle, 'dumps', lambda *_args, **_kwargs:
                        pytest.fail('saving must not buffer the full serialized payload'))
    original_read = Path.read_bytes
    def read(path):
        if path.name == 'payload.pkl':
            pytest.fail('loading must not buffer the full serialized payload')
        return original_read(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    value = {'inputs': list(range(10000)), 'reserved_evaluation': [True, False]}
    store = FileArtifactStore(tmp_path)
    reference = store.save(kind='checkpoints', artifact_id='streamed', value=value)
    assert store.load(reference) == value
    with (tmp_path / reference.uri / 'payload.pkl').open('ab') as handle:
        handle.write(b'corruption')
    with pytest.raises(ValueError, match='digest verification'):
        store.load(reference)
