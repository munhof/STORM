"""Minimal executable STORM study using only the Python standard library."""

from tempfile import TemporaryDirectory

from storm import (
    DataRef,
    Dataset,
    FileArtifactStore,
    MetricRegistry,
    ModelOutput,
    ModelRegistry,
    RunSpec,
    Study,
    StudySpec,
)


class MeanModel:
    def __init__(self, config):
        self.offset = float(config["offset"])
        self.value = 0.0

    def fit(self, inputs, targets=None):
        self.value = sum(inputs) / len(inputs) + self.offset
        return self

    def predict(self, inputs):
        return ModelOutput(predictions=[self.value] * len(inputs))


class MeanPrediction:
    def evaluate(self, *, dataset, output, model):
        return sum(output.predictions) / len(output.predictions)


def main() -> None:
    model_registry = ModelRegistry()
    model_registry.register("mean", MeanModel)

    metric_registry = MetricRegistry()
    metric_registry.register("mean_prediction", MeanPrediction())

    spec = StudySpec(
        study_id="getting-started",
        data=DataRef(
            identifier="demo-values",
            fingerprint="sha256:demo-values-v1",
        ),
        runs=(
            RunSpec(
                run_id="baseline",
                model_type="mean",
                model_config={"offset": 0.0},
                seed=7,
            ),
            RunSpec(
                run_id="shifted",
                model_type="mean",
                model_config={"offset": 2.0},
                seed=7,
            ),
        ),
        metrics=("mean_prediction",),
    )

    with TemporaryDirectory() as artifact_root:
        study = Study(
            spec,
            data_loader=lambda data_ref: Dataset(inputs=[1.0, 3.0, 5.0]),
            models=model_registry,
            metrics=metric_registry,
            artifacts=FileArtifactStore(artifact_root),
        )
        results = study.run()
        best = results.select("mean_prediction", maximize=True)
        prediction = best.load_model().predict([10.0, 20.0])

        print(f"best_run={best.run_id}")
        print(f"prediction={prediction.predictions}")


if __name__ == "__main__":
    main()

