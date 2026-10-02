"""Versioned execution events independent of frameworks and scientific backends."""
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4


class ExecutionObserver:
    def __init__(self, callback=None, **context):
        self.callback = callback
        self.context = context

    def emit(self, **fields):
        if self.callback is not None:
            self.callback({**self.context, 'schema_version': 1,
                           'timestamp': datetime.now(timezone.utc).isoformat(), **fields})

    def call(self, component, operation, *args, **kwargs):
        method = component if operation == "__call__" else getattr(component, operation)
        span = str(uuid4())
        fields = {'component': getattr(component, '__name__', type(component).__name__),
                  'component_version': str(getattr(component, 'version', 'unknown')),
                  'operation': operation, 'span_id': span}
        started = monotonic()
        self.emit(**fields, status='started', label=f'{fields["component"]}: {operation}')
        try:
            result = method(*args, **kwargs)
        except Exception as error:
            self.emit(**fields, status='failed', duration_seconds=monotonic()-started,
                      error_type=type(error).__name__, error_message=str(error))
            raise
        self.emit(**fields, status='completed', duration_seconds=monotonic()-started)
        return result

    def bind(self, component):
        """Discover the optional reporting capability without inspecting user data."""
        setter = getattr(component, 'set_progress_callback', None)
        if not callable(setter):
            return None
        last = {'time': float('-inf'), 'key': None}
        def report(update):
            key = (update.get('phase'), update.get('epoch'))
            now = monotonic()
            batch = update.get('batch_step')
            if (batch is not None and batch not in (0, update.get('batch_total'))
                    and key == last['key'] and now-last['time'] < 1):
                return
            last.update(time=now, key=key)
            self.emit(component=type(component).__name__, status='progress', **update)
        setter(report if self.callback is not None else None)
        return setter
