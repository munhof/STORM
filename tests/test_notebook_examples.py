from __future__ import annotations

import json
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = ROOT / "examples" / "notebooks"


def _notebook_source(name: str) -> str:
    notebook = json.loads((NOTEBOOKS / name).read_text(encoding="utf-8"))
    assert notebook["cells"]
    return "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )


def test_full_core_dummy_notebook_is_an_editable_tutorial():
    source = _notebook_source("01_full_core_dummy_study.ipynb")

    assert "Study(" in source
    assert "PipelineRunner.from_spec" in source
    assert "register_dummy_models" in source
    assert "register_classic_metrics" in source
    assert "VisualizationManager" in source


def test_knn_notebook_uses_sklearn_dataset_and_storm_contracts():
    source = _notebook_source("02_sklearn_knn_study.ipynb")

    assert "KNeighborsClassifier" in source
    assert "load_iris" in source
    assert "sns.load_dataset(\"penguins\")" in source
    assert "ModelOutput" in source
    assert "StudySpec" in source
    assert "MetricRegistry" in source
    assert "PipelineStep" in source
    assert "PipelineDataLoader" in source
    assert "FiniteFeatureFilter" in source
    assert "MorphologyRatioFeatures" in source
    assert "pico_cuerpo_index" in source
    assert "aleta_cuerpo_index" in source
    assert "Visualization" in source
    assert "RatioSummaryTable" in source
    assert "ConfusionMatrixPlot" in source
    assert "RatioScatterPlot" in source
    assert "AccuracyBarPlot" in source
    assert "confusion_matrix" in source
    assert "image/png" in source
    assert "display(Image(data=" in source


def test_examples_extra_installs_notebook_knn_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    examples = project["project"]["optional-dependencies"]["examples"]

    assert "scikit-learn>=1.5" in examples
    assert "matplotlib>=3.8" in examples
    assert "seaborn>=0.13" in examples
    assert "ipykernel>=6" in examples
