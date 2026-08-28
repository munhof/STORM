from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping


@dataclass(frozen=True)
class VisualizationRequest:
    """Domain-neutral values made available to a visualization plugin."""

    data: Any = None
    model: Any = None
    output: Any = None
    metrics: Mapping[str, int | float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisualizationResult:
    """Rendered value plus its portable media description."""

    content: Any
    media_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.media_type:
            raise ValueError("VisualizationResult media_type must not be empty.")


class Visualization(ABC):
    """Class-based extension interface for data, model or metric views."""

    visualization_type: ClassVar[str] = ""

    @abstractmethod
    def render(self, request: VisualizationRequest) -> VisualizationResult:
        """Render one request without assuming a specific graphics backend."""

    @classmethod
    def registered_name(cls) -> str:
        return cls.visualization_type or cls.__name__
