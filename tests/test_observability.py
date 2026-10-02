import pytest
from storm.observability import ExecutionObserver


def test_reflected_operation_records_lifecycle_and_failure():
    events = []
    observer = ExecutionObserver(events.append)
    class Adapter:
        version = '1'
        def load(self):
            return [1]
        def fail(self):
            raise ValueError('invalid input')
    adapter = Adapter()
    assert observer.call(adapter, 'load') == [1]
    with pytest.raises(ValueError):
        observer.call(adapter, 'fail')
    assert [event['status'] for event in events] == ['started', 'completed', 'started', 'failed']
    assert events[0]['span_id'] == events[1]['span_id']
    assert events[-1]['error_type'] == 'ValueError'
    assert events[-1]['schema_version'] == 1
    assert events[1]['duration_seconds'] >= 0
