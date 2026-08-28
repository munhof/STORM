from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from storm.pipeline.context import PipelineContext


class PipelineStep(ABC):
    """Class-based extension interface for one processing step."""

    step_type: ClassVar[str] = ""
    version: ClassVar[str] = "1"

    @abstractmethod
    def process(self, context: PipelineContext) -> PipelineContext:
        """Transform the shared context and return the context to continue with."""

    @classmethod
    def registered_name(cls) -> str:
        """Return the stable config name, falling back to the class name."""
        return cls.step_type or cls.__name__
