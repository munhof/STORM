from __future__ import annotations

from storm.metrics.base import Metric


class MetricRegistry:
    """Explicit registry keeping metric implementations outside study specs."""

    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}

    def register(self, name: str, metric: Metric, *, replace: bool = False) -> None:
        if not name:
            raise ValueError("Metric name must not be empty.")
        if name in self._metrics and not replace:
            raise ValueError(f"Metric '{name}' is already registered.")
        if not callable(getattr(metric, "evaluate", None)):
            raise TypeError("A metric must provide evaluate().")
        self._metrics[name] = metric

    def get(self, name: str) -> Metric:
        try:
            return self._metrics[name]
        except KeyError as error:
            raise KeyError(f"No metric registered for '{name}'.") from error

