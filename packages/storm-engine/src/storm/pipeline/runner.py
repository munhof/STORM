from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from time import monotonic

from storm.observability import ExecutionObserver
from storm.pipeline.context import PipelineContext, StepExecution
from storm.pipeline.registry import StepRegistry
from storm.pipeline.spec import PipelineSpec
from storm.pipeline.step import PipelineStep


class PipelineRunner:
    """Execute domain-neutral processing steps sequentially and record their trace."""

    def __init__(
        self,
        steps: Sequence[PipelineStep],
        *,
        spec: PipelineSpec | None = None,
    ) -> None:
        self.steps = tuple(steps)
        self.spec = spec

    @classmethod
    def from_spec(
        cls,
        spec: PipelineSpec,
        *,
        registry: StepRegistry,
    ) -> "PipelineRunner":
        return cls(
            tuple(
                registry.build(step.step_type, step.config) for step in spec.steps
            ),
            spec=spec,
        )

    def run(self, context: PipelineContext) -> PipelineContext:
        current = context
        for index, step in enumerate(self.steps):
            started_at = _utc_now()
            timer = monotonic()
            name = step.registered_name()
            version = str(step.version)
            try:
                updated = ExecutionObserver(current.progress_callback).call(step, 'process', current)
                if not isinstance(updated, PipelineContext):
                    raise TypeError(
                        f"Pipeline step '{name}' must return PipelineContext."
                    )
            except Exception as error:
                finished_at = _utc_now()
                current.executions.append(
                    StepExecution(
                        index=index,
                        step_type=name,
                        step_version=version,
                        status="failed",
                        started_at=started_at,
                        finished_at=finished_at,
                        duration_seconds=monotonic() - timer,
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
                )
                raise
            finished_at = _utc_now()
            current = updated
            current.executions.append(
                StepExecution(
                    index=index,
                    step_type=name,
                    step_version=version,
                    status="completed",
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=monotonic() - timer,
                )
            )
        return current


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
