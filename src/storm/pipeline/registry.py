from __future__ import annotations

import inspect
from types import ModuleType
from typing import Any, Mapping

from storm.config import JsonValue
from storm.pipeline.step import PipelineStep
from storm.plugins import discover_implementations


class StepRegistry:
    """Registry of steps that supports explicit and reflection-based registration."""

    def __init__(self) -> None:
        self._steps: dict[str, type[PipelineStep]] = {}

    def register(
        self,
        step: type[PipelineStep],
        *,
        replace: bool = False,
    ) -> None:
        if not inspect.isclass(step) or not issubclass(step, PipelineStep):
            raise TypeError("A step must inherit from PipelineStep.")
        if inspect.isabstract(step):
            raise TypeError("An abstract PipelineStep cannot be registered.")
        name = step.registered_name()
        current = self._steps.get(name)
        if current is step:
            return
        if current is not None and not replace:
            raise ValueError(f"Pipeline step '{name}' is already registered.")
        self._steps[name] = step

    def discover(self, package: str | ModuleType) -> tuple[str, ...]:
        """Register concrete PipelineStep subclasses found below one package."""
        implementations = discover_implementations(package, PipelineStep)
        for implementation in implementations:
            self.register(implementation)
        return tuple(sorted(implementation.registered_name() for implementation in implementations))

    def build(
        self,
        step_type: str,
        config: Mapping[str, JsonValue],
    ) -> PipelineStep:
        try:
            step_class = self._steps[step_type]
        except KeyError as error:
            raise KeyError(f"No pipeline step registered for '{step_type}'.") from error
        try:
            return step_class(**dict(config))
        except TypeError as error:
            raise TypeError(
                f"Could not build pipeline step '{step_type}' from its config."
            ) from error

    @property
    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._steps))
