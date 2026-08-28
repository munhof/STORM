from __future__ import annotations

import inspect
from types import ModuleType
from typing import Mapping

from storm.config import JsonValue
from storm.plugins import discover_implementations
from storm.visualization.base import Visualization


class VisualizationRegistry:
    """Registry for explicitly registered or reflection-discovered visualizations."""

    def __init__(self) -> None:
        self._visualizations: dict[str, type[Visualization]] = {}

    def register(
        self,
        visualization: type[Visualization],
        *,
        replace: bool = False,
    ) -> None:
        if not inspect.isclass(visualization) or not issubclass(
            visualization, Visualization
        ):
            raise TypeError("A visualization must inherit from Visualization.")
        if inspect.isabstract(visualization):
            raise TypeError("An abstract Visualization cannot be registered.")
        name = visualization.registered_name()
        current = self._visualizations.get(name)
        if current is visualization:
            return
        if current is not None and not replace:
            raise ValueError(f"Visualization '{name}' is already registered.")
        self._visualizations[name] = visualization

    def discover(self, package: str | ModuleType) -> tuple[str, ...]:
        """Register concrete Visualization subclasses found below one package."""
        implementations = discover_implementations(package, Visualization)
        for implementation in implementations:
            self.register(implementation)
        return tuple(sorted(implementation.registered_name() for implementation in implementations))

    def build(
        self,
        visualization_type: str,
        config: Mapping[str, JsonValue],
    ) -> Visualization:
        try:
            visualization_class = self._visualizations[visualization_type]
        except KeyError as error:
            raise KeyError(
                f"No visualization registered for '{visualization_type}'."
            ) from error
        try:
            return visualization_class(**dict(config))
        except TypeError as error:
            raise TypeError(
                f"Could not build visualization '{visualization_type}' from its config."
            ) from error

    @property
    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._visualizations))
