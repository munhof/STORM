from __future__ import annotations

from html import escape
import math
from typing import Any

from storm.visualization import Visualization, VisualizationRequest, VisualizationResult
from storm.visualization.classic.svg import finite_numbers, points, project, svg_document


class MetricBarVisualization(Visualization):
    """Render scalar metrics as a dependency-free SVG bar chart."""

    visualization_type = "metric_bar"

    def __init__(self, *, width: int = 640, height: int = 400, title: str = "Metrics") -> None:
        self.width, self.height, self.title = _canvas(width, height, title)

    def render(self, request: VisualizationRequest) -> VisualizationResult:
        labels = sorted(request.metrics)
        values = finite_numbers((request.metrics[label] for label in labels), label="metrics")
        chart_left, chart_top = 55.0, 45.0
        chart_width = self.width - 75.0
        chart_height = self.height - 105.0
        maximum = max(max(values), 0.0) or 1.0
        slot = chart_width / len(values)
        body = [_heading(self.title, self.width), _axes(chart_left, chart_top, chart_width, chart_height)]
        for index, (label, value) in enumerate(zip(labels, values, strict=True)):
            bar_height = max(0.0, value) / maximum * chart_height
            x = chart_left + index * slot + slot * 0.15
            y = chart_top + chart_height - bar_height
            body.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{slot * 0.7:.2f}" '
                f'height="{bar_height:.2f}" fill="#2563eb"/>'
            )
            body.append(_text(x + slot * 0.35, chart_top + chart_height + 20, label, anchor="middle"))
            body.append(_text(x + slot * 0.35, max(chart_top + 14, y - 6), f"{value:.3g}", anchor="middle"))
        return _result(body, self.width, self.height, self.title, "metric_bar")


class DataLineVisualization(Visualization):
    """Render a one-dimensional data sequence as an SVG line chart."""

    visualization_type = "data_line"

    def __init__(self, *, width: int = 640, height: int = 400, title: str = "Data") -> None:
        self.width, self.height, self.title = _canvas(width, height, title)

    def render(self, request: VisualizationRequest) -> VisualizationResult:
        values = finite_numbers(request.data, label="data")
        coordinates = _series_coordinates(values, self.width, self.height)
        body = [
            _heading(self.title, self.width),
            _axes(55, 45, self.width - 75, self.height - 90),
            f'<polyline points="{_point_string(coordinates)}" fill="none" '
            'stroke="#2563eb" stroke-width="2.5"/>',
        ]
        body.extend(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3" fill="#1d4ed8"/>' for x, y in coordinates)
        return _result(body, self.width, self.height, self.title, "data_line")


class DataScatterVisualization(Visualization):
    """Render two-dimensional data points as an SVG scatter plot."""

    visualization_type = "data_scatter"

    def __init__(self, *, width: int = 640, height: int = 400, title: str = "Scatter") -> None:
        self.width, self.height, self.title = _canvas(width, height, title)

    def render(self, request: VisualizationRequest) -> VisualizationResult:
        values = points(request.data, label="data")
        coordinates = _scatter_coordinates(values, self.width, self.height)
        body = [_heading(self.title, self.width), _axes(55, 45, self.width - 75, self.height - 90)]
        body.extend(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#7c3aed"/>' for x, y in coordinates)
        return _result(body, self.width, self.height, self.title, "data_scatter")


class PredictionHistogramVisualization(Visualization):
    """Render numeric model predictions as an SVG histogram."""

    visualization_type = "prediction_histogram"

    def __init__(self, *, bins: int = 10, width: int = 640, height: int = 400, title: str = "Predictions") -> None:
        if bins < 1:
            raise ValueError("bins must be positive.")
        self.bins = int(bins)
        self.width, self.height, self.title = _canvas(width, height, title)

    def render(self, request: VisualizationRequest) -> VisualizationResult:
        predictions = getattr(request.output, "predictions", None)
        if predictions is None:
            raise ValueError("prediction_histogram requires output.predictions.")
        values = finite_numbers(predictions, label="predictions")
        counts = _histogram(values, self.bins)
        request_with_counts = VisualizationRequest(metrics={str(index): count for index, count in enumerate(counts)})
        chart = MetricBarVisualization(width=self.width, height=self.height, title=self.title).render(request_with_counts)
        return VisualizationResult(
            content=chart.content,
            media_type=chart.media_type,
            metadata={"visualization_type": self.visualization_type, "bins": len(counts)},
        )


def _canvas(width: int, height: int, title: str) -> tuple[int, int, str]:
    if width < 200 or height < 160:
        raise ValueError("SVG width and height are too small.")
    return int(width), int(height), str(title)


def _heading(title: str, width: int) -> str:
    return _text(width / 2, 25, title, anchor="middle", size=16)


def _axes(x: float, y: float, width: float, height: float) -> str:
    return (
        f'<path d="M {x:.2f} {y:.2f} V {y + height:.2f} H {x + width:.2f}" '
        'fill="none" stroke="#475569" stroke-width="1"/>'
    )


def _text(x: float, y: float, value: Any, *, anchor: str = "start", size: int = 11) -> str:
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{anchor}" '
        f'font-family="sans-serif" font-size="{size}" fill="#0f172a">'
        f"{escape(str(value))}</text>"
    )


def _series_coordinates(values: list[float], width: int, height: int) -> list[tuple[float, float]]:
    low, high = min(values), max(values)
    return [
        (
            project(index, 0, max(len(values) - 1, 1), 55, width - 20),
            project(value, low, high, height - 45, 45),
        )
        for index, value in enumerate(values)
    ]


def _scatter_coordinates(values: list[tuple[float, float]], width: int, height: int) -> list[tuple[float, float]]:
    xs, ys = zip(*values, strict=True)
    return [
        (
            project(x, min(xs), max(xs), 55, width - 20),
            project(y, min(ys), max(ys), height - 45, 45),
        )
        for x, y in values
    ]


def _point_string(values: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in values)


def _histogram(values: list[float], bins: int) -> list[int]:
    low, high = min(values), max(values)
    count = min(bins, max(1, len(values)))
    if low == high:
        return [len(values)]
    width = (high - low) / count
    result = [0] * count
    for value in values:
        index = min(int(math.floor((value - low) / width)), count - 1)
        result[index] += 1
    return result


def _result(body: list[str], width: int, height: int, title: str, kind: str) -> VisualizationResult:
    return VisualizationResult(
        content=svg_document(body, width=width, height=height, title=title),
        media_type="image/svg+xml",
        metadata={"visualization_type": kind, "width": width, "height": height},
    )
