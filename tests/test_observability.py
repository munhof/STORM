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


def test_completed_event_keeps_an_explicit_operation_label():
    events = []
    observer = ExecutionObserver(events.append)
    observer.call(lambda: 1, '__call__')
    assert events[-1]['label'].endswith(': __call__ completado')


def test_reflected_callback_accepts_component_failure_status():
    events = []
    class Adapter:
        def set_progress_callback(self, callback):
            self.callback = callback
    adapter = Adapter()
    ExecutionObserver(events.append).bind(adapter)
    adapter.callback({'status': 'failed', 'phase': 'training', 'batch_step': 1,
                      'batch_total': 4, 'failure_kind': 'nonfinite_loss'})
    assert events[-1]['status'] == 'failed'
