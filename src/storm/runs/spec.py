from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from storm.config import JsonValue, fingerprint, json_compatible


@dataclass(frozen=True)
class DataRef:
    """Serializable identity of the data consumed by a study."""

    identifier: str
    fingerprint: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.identifier or not self.fingerprint:
            raise ValueError("DataRef identifier and fingerprint must not be empty.")
        object.__setattr__(self, "metadata", dict(json_compatible(dict(self.metadata))))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "identifier": self.identifier,
            "fingerprint": self.fingerprint,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DataRef":
        return cls(
            identifier=str(data["identifier"]),
            fingerprint=str(data["fingerprint"]),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class RunSpec:
    """Serializable configuration for one model training run."""

    run_id: str
    model_type: str
    model_config: Mapping[str, JsonValue]
    seed: int = 0
    tags: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id or not self.model_type:
            raise ValueError("RunSpec run_id and model_type must not be empty.")
        object.__setattr__(
            self, "model_config", dict(json_compatible(dict(self.model_config)))
        )
        object.__setattr__(self, "tags", dict(json_compatible(dict(self.tags))))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "run_id": self.run_id,
            "model_type": self.model_type,
            "model_config": dict(self.model_config),
            "seed": self.seed,
            "tags": dict(self.tags),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunSpec":
        return cls(
            run_id=str(data["run_id"]),
            model_type=str(data["model_type"]),
            model_config=dict(data.get("model_config") or {}),
            seed=int(data.get("seed", 0)),
            tags=dict(data.get("tags") or {}),
        )

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.to_dict())

