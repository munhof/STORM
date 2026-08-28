from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self

from storm.config import JsonValue
from storm.models import ModelOutput, ModelRegistry


class ConstantModel:
    """Predict one configured value for every input item."""

    def __init__(self, config: Mapping[str, JsonValue]) -> None:
        self.value = config.get("value", 0)

    def fit(self, inputs: Any, targets: Any = None) -> Self:
        return self

    def predict(self, inputs: Any) -> ModelOutput:
        return ModelOutput(
            predictions=[self.value for _ in inputs],
            metadata={"dummy_model": "constant"},
        )


class IdentityModel:
    """Echo each input as its prediction."""

    def __init__(self, config: Mapping[str, JsonValue]) -> None:
        if config:
            raise ValueError("IdentityModel does not accept configuration values.")

    def fit(self, inputs: Any, targets: Any = None) -> Self:
        return self

    def predict(self, inputs: Any) -> ModelOutput:
        return ModelOutput(
            predictions=list(inputs),
            metadata={"dummy_model": "identity"},
        )


class MeanRegressor:
    """Learn the arithmetic mean of supervised targets."""

    def __init__(self, config: Mapping[str, JsonValue]) -> None:
        if config:
            raise ValueError("MeanRegressor does not accept configuration values.")
        self.mean_: float | None = None

    def fit(self, inputs: Any, targets: Any = None) -> Self:
        if targets is None:
            raise ValueError("MeanRegressor requires targets.")
        values = [float(value) for value in targets]
        if not values:
            raise ValueError("MeanRegressor requires at least one target.")
        self.mean_ = sum(values) / len(values)
        return self

    def predict(self, inputs: Any) -> ModelOutput:
        if self.mean_ is None:
            raise RuntimeError("MeanRegressor must be fitted before predict().")
        return ModelOutput(
            predictions=[self.mean_ for _ in inputs],
            metadata={"dummy_model": "mean_regressor", "mean": self.mean_},
        )


def register_dummy_models(registry: ModelRegistry) -> None:
    """Register the dependency-free dummy model catalog."""
    registry.register("constant", ConstantModel)
    registry.register("identity", IdentityModel)
    registry.register("mean_regressor", MeanRegressor)
