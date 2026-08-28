from __future__ import annotations

from collections.abc import Callable, Sequence

from storm.artifacts import ArtifactStore
from storm.metrics import MetricRegistry
from storm.models import ModelRegistry
from storm.runs import DataRef, Dataset, RunEngine, Seeder
from storm.studies.results import StudyResults
from storm.studies.spec import StudySpec


DataLoader = Callable[[DataRef], Dataset]


class Study:
    """Run a serializable study using application-provided runtime adapters."""

    def __init__(
        self,
        spec: StudySpec,
        *,
        data_loader: DataLoader,
        models: ModelRegistry,
        artifacts: ArtifactStore,
        metrics: MetricRegistry | None = None,
        seeders: Sequence[Seeder] = (),
    ) -> None:
        self.spec = spec
        self.data_loader = data_loader
        self.engine = RunEngine(
            models=models,
            metrics=metrics or MetricRegistry(),
            artifacts=artifacts,
            seeders=seeders,
        )

    def run(self) -> StudyResults:
        dataset = self.data_loader(self.spec.data)
        if not isinstance(dataset, Dataset):
            raise TypeError("data_loader must return Dataset.")
        results = [
            self.engine.train(
                study_id=self.spec.study_id,
                data_ref=self.spec.data,
                dataset=dataset,
                spec=run_spec,
                metric_names=self.spec.metrics,
            )
            for run_spec in self.spec.runs
        ]
        return StudyResults(results)

