from storm import PipelineContext, PipelineStep


class ScaleValues(PipelineStep):
    step_type = "scale_values"
    version = "1"

    def __init__(self, *, factor: float) -> None:
        self.factor = factor

    def process(self, context: PipelineContext) -> PipelineContext:
        context.data = [value * self.factor for value in context.data]
        return context
