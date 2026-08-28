from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from storm.artifacts import ArtifactRef, ArtifactStore
from storm.runs.spec import DataRef


@dataclass(frozen=True)
class RunRecord:
    """Persistable lineage and outcome of one successful run."""

    study_id: str
    run_id: str
    execution_id: str
    model_type: str
    data_ref: DataRef
    spec_fingerprint: str
    seed: int
    metrics: Mapping[str, float]
    model_artifact: ArtifactRef
    output_artifact: ArtifactRef
    started_at: str
    finished_at: str
    duration_seconds: float
    tags: Mapping[str, Any] = field(default_factory=dict)


class RunResult:
    """Runtime result handle that can recover persisted model and output objects."""

    def __init__(
        self,
        record: RunRecord,
        *,
        artifacts: ArtifactStore,
        run_artifact: ArtifactRef,
    ) -> None:
        self._record = record
        self._artifacts = artifacts
        self.run_artifact = run_artifact

    def __getattr__(self, name: str) -> Any:
        return getattr(self._record, name)

    @property
    def record(self) -> RunRecord:
        return self._record

    def load_model(self) -> Any:
        return self._artifacts.load(self._record.model_artifact)

    def load_output(self) -> Any:
        return self._artifacts.load(self._record.output_artifact)

    @classmethod
    def recover(
        cls,
        artifacts: ArtifactStore,
        *,
        artifact_id: str,
    ) -> "RunResult":
        reference = artifacts.resolve(kind="runs", artifact_id=artifact_id)
        record = artifacts.load(reference)
        if not isinstance(record, RunRecord):
            raise TypeError(f"Artifact '{artifact_id}' does not contain a RunRecord.")
        return cls(record, artifacts=artifacts, run_artifact=reference)
