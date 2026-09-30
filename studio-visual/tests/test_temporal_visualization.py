from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/storm-engine/src"))
sys.path.insert(0, str(ROOT / "packages/storm-visualization/src"))

from storm import ModelOutput, VisualizationRegistry, VisualizationRequest  # noqa: E402
from storm_studio_visual import (  # noqa: E402
    TemporalClassificationVisualization,
    TemporalSample,
    TemporalTrace,
)


def test_render_includes_raw_series_labels_and_cursor() -> None:
    trace = TemporalTrace(
        source_id="demo-session",
        alignment="frame",
        samples=(
            TemporalSample(index=0, timestamp=0.0, raw=1.0),
            TemporalSample(index=1, timestamp=0.1, raw=2.0, human_label="explore"),
            TemporalSample(index=2, timestamp=0.2, raw=3.0),
        ),
    )

    result = TemporalClassificationVisualization().render(
        VisualizationRequest(
            data=trace,
            output=ModelOutput(
                predictions=["explore", "interact", "interact"],
                metadata={"confidence": [0.7, 0.81, 0.9]},
            ),
            metadata={"selected_index": 1},
        )
    )

    assert result.media_type == "image/svg+xml"
    assert "demo-session" in result.content
    assert "explore" in result.content
    assert "interact" in result.content
    assert 'data-index="1"' in result.content
    assert result.metadata["alignment"] == "frame"


def test_render_rejects_unaligned_predictions() -> None:
    trace = TemporalTrace(
        source_id="demo",
        alignment="timestamp",
        samples=(TemporalSample(index=0, timestamp=0.0, raw=1.0),),
    )

    with pytest.raises(ValueError, match="same length"):
        TemporalClassificationVisualization().render(
            VisualizationRequest(
                data=trace,
                output=ModelOutput(predictions=[]),
            )
        )


def test_visualization_is_discoverable_through_storm_registry() -> None:
    registry = VisualizationRegistry()
    discovered = registry.discover("storm_studio_visual")

    assert discovered == ("temporal_classification_overlay",)
    assert registry.build("temporal_classification_overlay", {}).visualization_type == (
        "temporal_classification_overlay"
    )


def test_web_demo_is_self_contained_and_exposes_temporal_controls() -> None:
    demo = Path(__file__).parents[1] / "web-demo" / "index.html"
    source = demo.read_text(encoding="utf-8")

    for required in (
        "Datos crudos",
        "Clasificación",
        "timeline",
        "model-select",
        "play-button",
        "raw-chart",
        "classification-track",
    ):
        assert required in source
    assert "https://" not in source


def test_web_demo_contains_all_visual_study_tabs() -> None:
    source = (Path(__file__).parents[1] / "web-demo" / "index.html").read_text(encoding="utf-8")

    for required in (
        'data-tab="flow"',
        'data-tab="compare"',
        'data-tab="labels"',
        'data-tab="evidence"',
        'data-tab="history"',
        'id="flow-panel"',
        'id="compare-panel"',
        'id="labels-panel"',
        'id="evidence-panel"',
        'id="history-panel"',
        "Asistencia LLM",
    ):
        assert required in source
