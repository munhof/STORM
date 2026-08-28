from storm import Visualization, VisualizationRequest, VisualizationResult


class RowCount(Visualization):
    visualization_type = "row_count"

    def render(self, request: VisualizationRequest) -> VisualizationResult:
        return VisualizationResult(
            content=f"rows={len(request.data)}",
            media_type="text/plain",
        )
