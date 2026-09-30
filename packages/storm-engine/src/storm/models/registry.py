from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from storm.config import JsonValue
from storm.models.base import Model


ModelBuilder = Callable[[Mapping[str, JsonValue]], Model]


class ModelRegistry:
    """Explicit runtime registry for framework models and external adapters."""

    def __init__(self) -> None:
        self._builders: dict[str, ModelBuilder] = {}

    @property
    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._builders))

    def register(
        self,
        model_type: str,
        builder: ModelBuilder,
        *,
        replace: bool = False,
    ) -> None:
        if not model_type:
            raise ValueError("model_type must not be empty.")
        if model_type in self._builders and not replace:
            raise ValueError(f"Model type '{model_type}' is already registered.")
        self._builders[model_type] = builder

    def build(self, model_type: str, config: Mapping[str, JsonValue]) -> Model:
        try:
            builder = self._builders[model_type]
        except KeyError as error:
            raise KeyError(f"No model builder registered for '{model_type}'.") from error
        model: Any = builder(dict(config))
        if not callable(getattr(model, "fit", None)) or not callable(
            getattr(model, "predict", None)
        ):
            raise TypeError(
                f"Builder for '{model_type}' did not return an object with fit() and predict()."
            )
        return model
