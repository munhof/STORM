from __future__ import annotations

from numbers import Number
from typing import Any

from storm.pipeline import PipelineContext, PipelineStep


class IdentityStep(PipelineStep):
    """Return the pipeline context unchanged for smoke tests and examples."""

    step_type = "identity"
    version = "1"

    def process(self, context: PipelineContext) -> PipelineContext:
        return context


class ScaleStep(PipelineStep):
    """Multiply numeric values, coordinate vectors or windows by a factor."""

    step_type = "scale"
    version = "1"

    def __init__(self, *, factor: float) -> None:
        self.factor = float(factor)

    def process(self, context: PipelineContext) -> PipelineContext:
        context.data = _scale(context.data, self.factor)
        return context


def _scale(value: Any, factor: float) -> Any:
    if isinstance(value, Number):
        return float(value) * factor
    if isinstance(value, (str, bytes)):
        raise TypeError("ScaleStep does not accept text data.")
    try:
        return [_scale(item, factor) if isinstance(item, (list, tuple)) else float(item) * factor
                for item in value]
    except (TypeError, ValueError) as error:
        raise TypeError("ScaleStep requires numeric data.") from error
