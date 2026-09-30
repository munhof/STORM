from __future__ import annotations

from dataclasses import dataclass

from storm.visualization.base import VisualizationRequest, VisualizationResult
from storm.visualization.registry import VisualizationRegistry
from storm.visualization.spec import VisualizationSpec


@dataclass(frozen=True)
class VisualizationManager:
    """Resolve and execute configured visualization plugins."""

    registry: VisualizationRegistry

    def render(
        self,
        spec: VisualizationSpec,
        request: VisualizationRequest,
    ) -> VisualizationResult:
        visualization = self.registry.build(
            spec.visualization_type,
            spec.config,
        )
        result = visualization.render(request)
        if not isinstance(result, VisualizationResult):
            raise TypeError("A Visualization must return VisualizationResult.")
        return result
