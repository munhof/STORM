from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from math import isfinite
from typing import Any, Mapping, Sequence

from storm import ModelOutput, Visualization, VisualizationRequest, VisualizationResult


@dataclass(frozen=True)
class TemporalSample:
    """One raw observation with its stable temporal identity."""

    index: int
    timestamp: float
    raw: float
    human_label: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("sample index must be non-negative")
        if not isfinite(self.timestamp):
            raise ValueError("sample timestamp must be finite")
        if not isfinite(self.raw):
            raise ValueError("sample raw value must be finite")


@dataclass(frozen=True)
class TemporalTrace:
    """Domain-neutral raw series prepared for temporal inspection."""

    source_id: str
    alignment: str
    samples: tuple[TemporalSample, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id must not be empty")
        if self.alignment not in {"index", "timestamp", "frame"}:
            raise ValueError("alignment must be index, timestamp or frame")
        if not self.samples:
            raise ValueError("a temporal trace needs at least one sample")
        indices = [sample.index for sample in self.samples]
        if indices != sorted(set(indices)):
            raise ValueError("sample indices must be unique and ordered")


class TemporalClassificationVisualization(Visualization):
    """Render raw values and aligned classification bands as portable SVG."""

    visualization_type = "temporal_classification_overlay"

    def render(self, request: VisualizationRequest) -> VisualizationResult:
        if not isinstance(request.data, TemporalTrace):
            raise TypeError("request.data must be a TemporalTrace")
        if not isinstance(request.output, ModelOutput):
            raise TypeError("request.output must be a ModelOutput")

        trace = request.data
        predictions = _as_sequence(request.output.predictions)
        if len(predictions) != len(trace.samples):
            raise ValueError("predictions and temporal samples must have the same length")

        labels = [str(value) for value in predictions]
        confidence = request.output.metadata.get("confidence")
        if confidence is not None:
            confidence = _as_sequence(confidence)
            if len(confidence) != len(labels):
                raise ValueError("confidence and temporal samples must have the same length")

        selected = request.metadata.get("selected_index")
        svg = _render_svg(trace, labels, confidence, selected)
        return VisualizationResult(
            content=svg,
            media_type="image/svg+xml",
            metadata={
                "alignment": trace.alignment,
                "source_id": trace.source_id,
                "sample_count": len(trace.samples),
            },
        )


def _as_sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, (str, bytes)):
        return [value]
    try:
        return list(value)
    except TypeError as error:
        raise TypeError("predictions must be a sequence") from error


def _render_svg(
    trace: TemporalTrace,
    labels: Sequence[str],
    confidence: Sequence[Any] | None,
    selected: Any,
) -> str:
    width, height = 1100, 600
    left, chart_width = 70, 980
    chart_top, chart_height = 110, 210
    band_top, band_height = 370, 60
    raw_values = [sample.raw for sample in trace.samples]
    low, high = min(raw_values), max(raw_values)
    span = high - low or 1.0
    points = []
    for position, value in enumerate(raw_values):
        x = left + chart_width * position / max(len(raw_values) - 1, 1)
        y = chart_top + chart_height - (value - low) / span * chart_height
        points.append(f"{x:.2f},{y:.2f}")
    path = " ".join(points)
    palette = ("#DCFCE7", "#FEF3C7", "#DBEAFE", "#FCE7F3", "#E2E8F0")
    unique = {label: palette[index % len(palette)] for index, label in enumerate(dict.fromkeys(labels))}
    bands = []
    for position, label in enumerate(labels):
        x = left + chart_width * position / len(labels)
        band_width = chart_width / len(labels) + 0.5
        confidence_text = ""
        if confidence is not None:
            confidence_text = f" · p={float(confidence[position]):.2f}"
        bands.append(
            f'<rect x="{x:.2f}" y="{band_top}" width="{band_width:.2f}" height="{band_height}" '
            f'fill="{unique[label]}" data-index="{trace.samples[position].index}"/>'
            f'<text x="{x + 5:.2f}" y="{band_top + 36}" font-size="12" fill="#0F172A">'
            f'{escape(label)}{escape(confidence_text)}</text>'
        )
    cursor = ""
    if selected is not None:
        try:
            selected_position = next(i for i, sample in enumerate(trace.samples) if sample.index == int(selected))
        except (StopIteration, TypeError, ValueError):
            selected_position = None
        if selected_position is not None:
            x = left + chart_width * selected_position / max(len(labels) - 1, 1)
            cursor = (
                f'<line x1="{x:.2f}" y1="80" x2="{x:.2f}" y2="470" '
                'stroke="#DC2626" stroke-width="3"/>'
                f'<text x="{min(x + 8, width - 180):.2f}" y="500" font-size="13" fill="#DC2626">'
                f'index {trace.samples[selected_position].index} · {trace.samples[selected_position].timestamp:.2f}s'
                '</text>'
            )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="Temporal classification overlay">'
        '<rect width="100%" height="100%" fill="#F8FAFC"/>'
        f'<text x="24" y="35" font-size="22" font-weight="700" fill="#0F172A">'
        f'Raw data + classification · {escape(trace.source_id)}</text>'
        f'<text x="24" y="60" font-size="14" fill="#475569">alignment: {escape(trace.alignment)} · samples: {len(labels)}</text>'
        '<rect x="24" y="80" width="1052" height="420" rx="10" fill="#FFFFFF"/>'
        f'<polyline points="{path}" fill="none" stroke="#0369A1" stroke-width="3"/>'
        f'{bands}{cursor}'
        f'<text x="{left}" y="345" font-size="13" fill="#475569">raw value</text>'
        f'<text x="{left}" y="455" font-size="13" fill="#475569">predicted label</text>'
        f'<text x="24" y="560" font-size="13" fill="#475569">Temporal identity is preserved; no labels are shifted or overwritten.</text>'
        '</svg>'
    )
