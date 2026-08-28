from __future__ import annotations

from storm import DataRef, Dataset
from storm.pipeline import (
    PipelineContext,
    PipelineDataLoader,
    PipelineRunner,
    PipelineSpec,
    StepRegistry,
    StepSpec,
)


def test_pipeline_is_built_from_serializable_specs_and_discovered_steps() -> None:
    registry = StepRegistry()

    discovered = registry.discover("tests.plugin_fixtures")

    assert discovered == ("add_value", "multiply_value")
    spec = PipelineSpec(
        pipeline_id="numeric-preparation",
        steps=(
            StepSpec(step_type="add_value", config={"amount": 2}),
            StepSpec(step_type="multiply_value", config={"factor": 4}),
        ),
        metadata={"schema": "numeric-v1"},
    )
    restored = PipelineSpec.from_dict(spec.to_dict())

    result = PipelineRunner.from_spec(restored, registry=registry).run(
        PipelineContext(data=3, dataset_id="numbers")
    )

    assert restored.fingerprint == spec.fingerprint
    assert result.data == 20
    assert [event.step_type for event in result.executions] == [
        "add_value",
        "multiply_value",
    ]
    assert all(event.status == "completed" for event in result.executions)
    assert all(event.duration_seconds >= 0 for event in result.executions)


def test_pipeline_data_loader_preserves_targets_and_prepares_inputs() -> None:
    registry = StepRegistry()
    registry.discover("tests.plugin_fixtures")
    runner = PipelineRunner.from_spec(
        PipelineSpec(
            pipeline_id="loader-preparation",
            steps=(StepSpec("add_value", {"amount": 5}),),
        ),
        registry=registry,
    )

    def load_data(reference: DataRef) -> Dataset:
        return Dataset(
            inputs=10,
            targets=[1],
            metadata={"source": reference.identifier},
        )

    loader = PipelineDataLoader(loader=load_data, runner=runner)
    reference = DataRef(identifier="sample", fingerprint="sha256:source")

    dataset = loader(reference)

    assert dataset.inputs == 15
    assert dataset.targets == [1]
    assert dataset.metadata["source"] == "sample"
    assert dataset.metadata["pipeline_id"] == "loader-preparation"
    assert dataset.metadata["pipeline_fingerprint"] == runner.spec.fingerprint
