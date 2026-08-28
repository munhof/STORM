from storm.pipeline import PipelineContext, PipelineStep


class AddValue(PipelineStep):
    step_type = "add_value"
    version = "1"

    def __init__(self, *, amount: int) -> None:
        self.amount = amount

    def process(self, context: PipelineContext) -> PipelineContext:
        context.data += self.amount
        return context


class MultiplyValue(PipelineStep):
    step_type = "multiply_value"
    version = "1"

    def __init__(self, *, factor: int) -> None:
        self.factor = factor

    def process(self, context: PipelineContext) -> PipelineContext:
        context.data *= self.factor
        return context
