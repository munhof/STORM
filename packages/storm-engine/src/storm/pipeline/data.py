from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from storm.pipeline.context import PipelineContext
from storm.pipeline.runner import PipelineRunner
from storm.runs import DataRef, Dataset

if TYPE_CHECKING:
    from storm.studies import DataLoader


@dataclass(frozen=True)
class PipelineDataLoader:
    """Compose an application data loader with a traceable preparation pipeline."""

    loader: DataLoader
    runner: PipelineRunner

    def __call__(self, reference: DataRef) -> Dataset:
        dataset = self.loader(reference)
        if not isinstance(dataset, Dataset):
            raise TypeError("The wrapped data loader must return Dataset.")
        context = self.runner.run(
            PipelineContext(
                data=dataset.inputs,
                targets=dataset.targets,
                dataset_id=reference.identifier,
                metadata=dict(dataset.metadata),
            )
        )
        metadata = dict(context.metadata)
        metadata["pipeline_executions"] = [
            execution.to_dict() for execution in context.executions
        ]
        if self.runner.spec is not None:
            metadata["pipeline_id"] = self.runner.spec.pipeline_id
            metadata["pipeline_fingerprint"] = self.runner.spec.fingerprint
        return Dataset(
            inputs=context.data,
            targets=context.targets,
            metadata=metadata,
        )
