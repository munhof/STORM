from storm import (
    DataRef,
    Dataset,
    PipelineDataLoader,
    PipelineRunner,
    PipelineSpec,
    StepRegistry,
    StepSpec,
    VisualizationManager,
    VisualizationRegistry,
    VisualizationRequest,
    VisualizationSpec,
)


def load_numbers(reference: DataRef) -> Dataset:
    return Dataset(inputs=[1, 2, 3], metadata={"source": reference.identifier})


steps = StepRegistry()
steps.discover("storm_example_extensions")
runner = PipelineRunner.from_spec(
    PipelineSpec(
        pipeline_id="scale-numbers",
        steps=(StepSpec("scale_values", {"factor": 2}),),
    ),
    registry=steps,
)
dataset = PipelineDataLoader(loader=load_numbers, runner=runner)(
    DataRef(identifier="numbers", fingerprint="sha256:example")
)

views = VisualizationRegistry()
views.discover("storm_example_extensions")
view = VisualizationManager(views).render(
    VisualizationSpec("row_count"),
    VisualizationRequest(data=dataset.inputs),
)

print(f"prepared={dataset.inputs}")
print(view.content)
