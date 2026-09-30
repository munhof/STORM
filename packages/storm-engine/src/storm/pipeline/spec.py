from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from storm.config import JsonValue, fingerprint, json_compatible


@dataclass(frozen=True)
class StepSpec:
    """Serializable selection and configuration of one pipeline step."""

    step_type: str
    config: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.step_type:
            raise ValueError("StepSpec step_type must not be empty.")
        object.__setattr__(self, "config", dict(json_compatible(dict(self.config))))

    def to_dict(self) -> dict[str, JsonValue]:
        return {"type": self.step_type, "config": dict(self.config)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StepSpec":
        return cls(
            step_type=str(data["type"]),
            config=dict(data.get("config") or {}),
        )


@dataclass(frozen=True)
class PipelineSpec:
    """Serializable definition of an ordered data-processing pipeline."""

    pipeline_id: str
    steps: tuple[StepSpec, ...]
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.pipeline_id:
            raise ValueError("PipelineSpec pipeline_id must not be empty.")
        object.__setattr__(self, "steps", tuple(self.steps))
        object.__setattr__(
            self,
            "metadata",
            dict(json_compatible(dict(self.metadata))),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "pipeline_id": self.pipeline_id,
            "steps": [step.to_dict() for step in self.steps],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PipelineSpec":
        return cls(
            pipeline_id=str(data["pipeline_id"]),
            steps=tuple(
                StepSpec.from_dict(step) for step in data.get("steps") or []
            ),
            metadata=dict(data.get("metadata") or {}),
        )

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.to_dict())
