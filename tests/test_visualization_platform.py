from storm.visualization import (
    VisualizationManager,
    VisualizationRegistry,
    VisualizationRequest,
    VisualizationSpec,
)


def test_visualization_is_discovered_and_rendered_through_its_interface() -> None:
    registry = VisualizationRegistry()

    discovered = registry.discover("tests.plugin_fixtures")
    manager = VisualizationManager(registry=registry)
    result = manager.render(
        VisualizationSpec(
            visualization_type="metric_text",
            config={"precision": 2},
        ),
        VisualizationRequest(metrics={"accuracy": 0.916}),
    )

    assert discovered == ("metric_text",)
    assert result.content == "accuracy=0.92"
    assert result.media_type == "text/plain"
