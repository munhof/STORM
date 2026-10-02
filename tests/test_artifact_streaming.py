import pytest


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
