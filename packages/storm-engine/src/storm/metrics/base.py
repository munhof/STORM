from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from storm.models import Model, ModelOutput
    from storm.runs.data import Dataset


class Metric(Protocol):
    """Evaluate one named scalar from a model run."""

    def evaluate(
        self,
        *,
        dataset: Dataset,
        output: ModelOutput,
        model: Model,
    ) -> int | float: ...
