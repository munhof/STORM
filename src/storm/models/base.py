from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Self


@dataclass(frozen=True)
class ModelOutput:
    """Domain-neutral output produced by a model or external adapter."""

    predictions: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)


class Model(Protocol):
    """Small lifecycle contract required by the initial STORM run engine."""

    def fit(self, inputs: Any, targets: Any = None) -> Self: ...

    def predict(self, inputs: Any) -> ModelOutput: ...

