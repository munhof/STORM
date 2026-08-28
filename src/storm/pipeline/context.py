from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StepExecution:
    """Trace of one attempted pipeline step."""

    index: int
    step_type: str
    step_version: str
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "step_type": self.step_type,
            "step_version": self.step_version,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass
class PipelineContext:
    """Domain-neutral mutable state shared by pipeline steps."""

    data: Any = None
    targets: Any = None
    dataset_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    executions: list[StepExecution] = field(default_factory=list)

    def get_info(self, name: str, default: Any = None) -> Any:
        """Find a named value in state, artifacts or metadata."""
        for container in (self.state, self.artifacts, self.metadata):
            if name in container:
                return container[name]
        return default

    def summarize(self) -> dict[str, Any]:
        """Return a lightweight description without assuming a data library."""
        return {
            "data": _summarize_value(self.data),
            "targets": _summarize_value(self.targets),
            "dataset_id": self.dataset_id,
            "metadata_keys": sorted(self.metadata),
            "artifact_keys": sorted(self.artifacts),
            "state_keys": sorted(self.state),
            "executed_steps": len(self.executions),
        }


def _summarize_value(value: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {"type": type(value).__name__}
    if value is None:
        return summary
    shape = getattr(value, "shape", None)
    if shape is not None:
        summary["shape"] = tuple(shape)
        return summary
    try:
        summary["length"] = len(value)
    except TypeError:
        pass
    return summary
