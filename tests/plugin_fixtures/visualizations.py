from storm.visualization import (
    Visualization,
    VisualizationRequest,
    VisualizationResult,
)


class MetricText(Visualization):
    visualization_type = "metric_text"

    def __init__(self, *, precision: int = 3) -> None:
        self.precision = precision

    def render(self, request: VisualizationRequest) -> VisualizationResult:
        content = ", ".join(
            f"{name}={value:.{self.precision}f}"
            for name, value in sorted(request.metrics.items())
        )
        return VisualizationResult(content=content, media_type="text/plain")
