from pathlib import Path

from storm import ModelOutput, VisualizationManager, VisualizationRegistry, VisualizationRequest, VisualizationSpec
from storm_studio_visual import TemporalSample, TemporalTrace


trace = TemporalTrace(
    source_id="demo-session",
    alignment="frame",
    samples=tuple(
        TemporalSample(index=index, timestamp=index / 10, raw=value)
        for index, value in enumerate((1.0, 2.5, 2.0, 4.0, 3.0, 1.5))
    ),
)
registry = VisualizationRegistry()
registry.discover("storm_studio_visual")
result = VisualizationManager(registry).render(
    VisualizationSpec("temporal_classification_overlay"),
    VisualizationRequest(
        data=trace,
        output=ModelOutput(
            predictions=["explore", "explore", "interact", "interact", "groom", "groom"],
            metadata={"confidence": [0.7, 0.72, 0.81, 0.84, 0.76, 0.79]},
        ),
        metadata={"selected_index": 2},
    ),
)
Path("temporal-classification.svg").write_text(result.content, encoding="utf-8")
print("wrote temporal-classification.svg")
