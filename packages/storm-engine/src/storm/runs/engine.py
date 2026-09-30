from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
import random
from time import monotonic
from uuid import uuid4

from storm.artifacts import ArtifactStore
from storm.metrics import MetricRegistry
from storm.models import ModelOutput, ModelRegistry
from storm.runs.data import Dataset
from storm.runs.result import RunRecord, RunResult
from storm.runs.spec import DataRef, RunSpec


Seeder = Callable[[int], None]


class RunEngine:
    """Execute one domain-neutral training run and persist its traceable outputs."""

    def __init__(
        self,
        *,
        models: ModelRegistry,
        metrics: MetricRegistry,
        artifacts: ArtifactStore,
        seeders: Sequence[Seeder] = (),
    ) -> None:
        self.models = models
        self.metrics = metrics
        self.artifacts = artifacts
        self.seeders = tuple(seeders)

    def train(
        self,
        *,
        study_id: str,
        data_ref: DataRef,
        dataset: Dataset,
        spec: RunSpec,
        metric_names: Sequence[str],
    ) -> RunResult:
        started = datetime.now(timezone.utc)
        timer = monotonic()
        self._seed(spec.seed)
        operation = spec.tags.get("operation", "train")
        if operation not in ("train", "infer"):
            raise ValueError("Run operation must be train or infer")
        model = self.models.build(spec.model_type, spec.model_config)
        if operation == "train":
            model.fit(dataset.inputs, dataset.targets)
        output = model.predict(dataset.inputs)
        if not isinstance(output, ModelOutput):
            raise TypeError(
                f"Model '{spec.model_type}' must return ModelOutput from predict()."
            )
        n_observations = len(dataset.inputs)
        mask = dataset.metadata.get("evaluation_mask", [True] * n_observations)
        if (not isinstance(mask, (list, tuple)) or len(mask) != n_observations
                or any(type(value) is not bool for value in mask)):
            raise ValueError("evaluation_mask must contain one boolean per observation")
        metric_indices = [index for index, valid in enumerate(mask) if valid]
        metric_values = {}
        if dataset.targets is not None and metric_indices:
            metric_dataset = Dataset(
                inputs=[dataset.inputs[i] for i in metric_indices],
                targets=[dataset.targets[i] for i in metric_indices],
            )
            metric_output = ModelOutput(
                predictions=[output.predictions[i] for i in metric_indices],
                metadata=output.metadata,
            )
            metric_values = {
                name: float(self.metrics.get(name).evaluate(
                    dataset=metric_dataset, output=metric_output, model=model))
                for name in metric_names
            }
        elif dataset.targets is None:
            # Preserve output-only metrics used by unsupervised models and older studies.
            metric_values = {
                name: float(self.metrics.get(name).evaluate(
                    dataset=dataset, output=output, model=model))
                for name in metric_names
            }
        tags = {
            **dict(spec.tags),
            "metric_indices": metric_indices,
            "evaluation_mask": list(mask),
            "evaluation_status": (
                "rankable" if metric_indices and dataset.targets is not None and metric_values
                else "metrics_not_requested" if metric_indices and dataset.targets is not None
                else "inspection_only_no_valid_labels" if dataset.targets is not None
                else "inspection_only_no_reference"
            ),
        }
        execution_id = f"execution-{uuid4().hex}"
        lineage = {
            "study_id": study_id,
            "run_id": spec.run_id,
            "execution_id": execution_id,
            "data": data_ref.to_dict(),
            "run_spec_fingerprint": spec.fingerprint,
        }
        model_ref = self.artifacts.save(
            kind="models",
            artifact_id=execution_id,
            value=model,
            metadata=lineage,
        )
        output_ref = self.artifacts.save(
            kind="outputs",
            artifact_id=execution_id,
            value=output,
            metadata=lineage,
        )
        finished = datetime.now(timezone.utc)
        record = RunRecord(
            study_id=study_id,
            run_id=spec.run_id,
            execution_id=execution_id,
            model_type=spec.model_type,
            data_ref=data_ref,
            spec_fingerprint=spec.fingerprint,
            seed=spec.seed,
            metrics=metric_values,
            model_artifact=model_ref,
            output_artifact=output_ref,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            duration_seconds=monotonic() - timer,
            tags=tags,
        )
        run_ref = self.artifacts.save(
            kind="runs",
            artifact_id=execution_id,
            value=record,
            metadata=lineage,
        )
        return RunResult(record, artifacts=self.artifacts, run_artifact=run_ref)

    def _seed(self, seed: int) -> None:
        random.seed(seed)
        for seeder in self.seeders:
            seeder(seed)
