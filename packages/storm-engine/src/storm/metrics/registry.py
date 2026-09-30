from __future__ import annotations

from storm.metrics.base import Metric


class MetricRegistry:
    """Explicit registry keeping metric implementations outside study specs."""

    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}
        self._descriptors: dict[str, dict[str, str]] = {}

    def register(self, name: str, metric: Metric, *, replace: bool = False,
                 version: str = '1', direction: str = 'minimize') -> None:
        if not name:
            raise ValueError("Metric name must not be empty.")
        if name in self._metrics and not replace:
            raise ValueError(f"Metric '{name}' is already registered.")
        if not callable(getattr(metric, "evaluate", None)):
            raise TypeError("A metric must provide evaluate().")
        if direction not in ('minimize', 'maximize'):
            raise ValueError("Metric direction must be minimize or maximize")
        self._metrics[name] = metric
        self._descriptors[name] = {'version': str(version), 'direction': direction}

    def get(self, name: str) -> Metric:
        try:
            return self._metrics[name]
        except KeyError as error:
            raise KeyError(f"No metric registered for '{name}'.") from error

    @property
    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._metrics))

    def describe(self, name: str) -> dict[str, str]:
        self.get(name)
        return {'name': name, **self._descriptors[name]}
