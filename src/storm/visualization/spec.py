from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from storm.config import JsonValue, json_compatible


@dataclass(frozen=True)
class VisualizationSpec:
    """Serializable selection and configuration of a visualization plugin."""

    visualization_type: str
    config: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.visualization_type:
            raise ValueError("VisualizationSpec visualization_type must not be empty.")
        object.__setattr__(self, "config", dict(json_compatible(dict(self.config))))

    def to_dict(self) -> dict[str, JsonValue]:
        return {"type": self.visualization_type, "config": dict(self.config)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VisualizationSpec":
        return cls(
            visualization_type=str(data["type"]),
            config=dict(data.get("config") or {}),
        )
