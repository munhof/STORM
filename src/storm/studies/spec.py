from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from storm.config import JsonValue
from storm.runs import DataRef, RunSpec


@dataclass(frozen=True)
class StudySpec:
    """Serializable request for a set of comparable training runs."""

    study_id: str
    data: DataRef
    runs: tuple[RunSpec, ...]
    metrics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.study_id:
            raise ValueError("study_id must not be empty.")
        if not self.runs:
            raise ValueError("StudySpec must contain at least one run.")
        run_ids = [run.run_id for run in self.runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("Run IDs must be unique within a study.")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "study_id": self.study_id,
            "data": self.data.to_dict(),
            "runs": [run.to_dict() for run in self.runs],
            "metrics": list(self.metrics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StudySpec":
        return cls(
            study_id=str(data["study_id"]),
            data=DataRef.from_dict(data["data"]),
            runs=tuple(RunSpec.from_dict(item) for item in data["runs"]),
            metrics=tuple(str(name) for name in data.get("metrics", ())),
        )

