from pathlib import Path
from tempfile import TemporaryDirectory

from storm import (
    DataRef,
    Dataset,
    FileArtifactStore,
    MetricRegistry,
    ModelRegistry,
    PipelineDataLoader,
    PipelineRunner,
    PipelineSpec,
    RunSpec,
    StepRegistry,
    StepSpec,
    Study,
    StudySpec,
    VisualizationManager,
    VisualizationRegistry,
    VisualizationRequest,
    VisualizationSpec,
)
from storm.metrics.classic import register_classic_metrics
from storm.testing import register_dummy_models


def load_numbers(reference: DataRef) -> Dataset:
    return Dataset(
        inputs=[0, 1, 2],
        targets=[1, 3, 5],
        metadata={"source": reference.identifier},
    )


steps = StepRegistry()
steps.discover("storm.testing")
pipeline = PipelineRunner.from_spec(
    PipelineSpec(
        pipeline_id="numeric-demo",
        steps=(StepSpec("scale", {"factor": 2}),),
    ),
    registry=steps,
)

models = ModelRegistry()
register_dummy_models(models)
metrics = MetricRegistry()
register_classic_metrics(metrics)

spec = StudySpec(
    study_id="full-core-demo",
    data=DataRef(identifier="numbers", fingerprint="sha256:demo-numbers"),
    runs=(
        RunSpec("constant", "constant", {"value": 0}),
        RunSpec("mean", "mean_regressor", {}),
    ),
    metrics=("mae", "mse"),
)

with TemporaryDirectory(prefix="storm-example-") as directory:
    results = Study(
        spec,
        data_loader=PipelineDataLoader(loader=load_numbers, runner=pipeline),
        models=models,
        metrics=metrics,
        artifacts=FileArtifactStore(Path(directory) / "artifacts"),
    ).run()
    best = results.select("mse", maximize=False)

    visualizations = VisualizationRegistry()
    visualizations.discover("storm.visualization.classic")
    chart = VisualizationManager(visualizations).render(
        VisualizationSpec("metric_bar", {"title": "Best run metrics"}),
        VisualizationRequest(metrics=best.record.metrics),
    )

    svg_path = Path(directory) / "best-run-metrics.svg"
    svg_path.write_text(chart.content, encoding="utf-8")

    print(f"best_run={best.record.run_id}")
    print(f"metrics={dict(best.record.metrics)}")
    print(f"visualization={chart.media_type}")
