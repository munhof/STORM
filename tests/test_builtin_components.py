from __future__ import annotations

from storm import (
    Accuracy,
    DataRef,
    Dataset,
    FileArtifactStore,
    MeanAbsoluteError,
    MeanSquaredError,
    MetricRegistry,
    ModelOutput,
    ModelRegistry,
    PipelineContext,
    PipelineRunner,
    PipelineSpec,
    StepRegistry,
    StepSpec,
    Study,
    StudySpec,
    RunSpec,
    VisualizationManager,
    VisualizationRegistry,
    VisualizationRequest,
    VisualizationSpec,
)
from storm.metrics.classic import register_classic_metrics
from storm.testing import register_dummy_models


def test_dummy_steps_are_discovered_and_executed() -> None:
    registry = StepRegistry()

    discovered = registry.discover("storm.testing")
    runner = PipelineRunner.from_spec(
        PipelineSpec(
            pipeline_id="dummy-preparation",
            steps=(
                StepSpec("identity", {}),
                StepSpec("scale", {"factor": 2}),
            ),
        ),
        registry=registry,
    )

    result = runner.run(PipelineContext(data=[1, 2, 3]))

    assert {"identity", "scale"}.issubset(discovered)
    assert result.data == [2.0, 4.0, 6.0]


def test_dummy_models_and_classic_metrics_complete_a_study(tmp_path) -> None:
    models = ModelRegistry()
    register_dummy_models(models)
    metrics = MetricRegistry()
    register_classic_metrics(metrics)
    spec = StudySpec(
        study_id="dummy-study",
        data=DataRef(identifier="numbers", fingerprint="sha256:numbers"),
        runs=(
            RunSpec("constant", "constant", {"value": 0}),
            RunSpec("mean", "mean_regressor", {}),
        ),
        metrics=("mae", "mse"),
    )

    results = Study(
        spec,
        data_loader=lambda _: Dataset(inputs=[0, 1, 2], targets=[1, 3, 5]),
        models=models,
        metrics=metrics,
        artifacts=FileArtifactStore(tmp_path),
    ).run()

    assert results.select("mse", maximize=False).record.run_id == "mean"
    assert Accuracy().evaluate(
        dataset=Dataset(inputs=[], targets=[1, 0]),
        output=ModelOutput(predictions=[1, 1]),
        model=results[0].load_model(),
    ) == 0.5
    assert MeanAbsoluteError().evaluate(
        dataset=Dataset(inputs=[], targets=[1, 3]),
        output=ModelOutput(predictions=[2, 5]),
        model=results[0].load_model(),
    ) == 1.5
    assert MeanSquaredError().evaluate(
        dataset=Dataset(inputs=[], targets=[1, 3]),
        output=ModelOutput(predictions=[2, 5]),
        model=results[0].load_model(),
    ) == 2.5


def test_classic_visualizers_are_discovered_and_render_svg() -> None:
    registry = VisualizationRegistry()

    discovered = registry.discover("storm.visualization.classic")
    manager = VisualizationManager(registry)
    requests = {
        "metric_bar": VisualizationRequest(metrics={"mae": 1.5, "mse": 2.5}),
        "data_line": VisualizationRequest(data=[1, 4, 2, 5]),
        "prediction_histogram": VisualizationRequest(
            output=ModelOutput(predictions=[0, 0, 1, 2, 2, 2])
        ),
        "data_scatter": VisualizationRequest(data=[(0, 1), (1, 3), (2, 2)]),
    }

    assert set(requests).issubset(discovered)
    for visualization_type, request in requests.items():
        result = manager.render(VisualizationSpec(visualization_type), request)
        assert result.media_type == "image/svg+xml"
        assert result.content.startswith("<svg")
        assert result.content.endswith("</svg>")
