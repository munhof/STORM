from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

import storm


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

REQUIRED_PAGES = (
    "index.md",
    "getting-started.md",
    "architecture/overview.md",
    "architecture/design.md",
    "architecture/initial-extraction.md",
    "api/public-api.md",
    "api/interfaces.md",
    "guides/studies-and-runs.md",
    "guides/models.md",
    "guides/metrics.md",
    "guides/artifacts.md",
    "guides/pipelines-and-steps.md",
    "guides/visualizations.md",
    "guides/studio-components.md",
    "guides/integrations.md",
)


class DocumentationTest(unittest.TestCase):
    def test_required_documentation_pages_are_present_and_navigable(self):
        navigation = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")

        for relative_path in REQUIRED_PAGES:
            page = DOCS / relative_path
            self.assertTrue(page.is_file(), relative_path)
            self.assertIn(relative_path, navigation)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/index.md", readme)

    def test_internal_markdown_page_links_resolve(self):
        link_pattern = re.compile(r"\[[^]]+\]\(([^)]+)\)")
        missing = []

        for page in DOCS.rglob("*.md"):
            for target in link_pattern.findall(page.read_text(encoding="utf-8")):
                clean_target = target.split("#", 1)[0]
                if not clean_target or "://" in clean_target or clean_target.startswith("mailto:"):
                    continue
                if not clean_target.endswith(".md"):
                    continue
                resolved = (page.parent / clean_target).resolve()
                if not resolved.is_file():
                    missing.append(f"{page.relative_to(ROOT)} -> {target}")

        self.assertEqual(missing, [])

    def test_public_api_reference_covers_every_root_export(self):
        reference = (DOCS / "api/public-api.md").read_text(encoding="utf-8")
        missing = [name for name in storm.__all__ if f"`{name}`" not in reference]

        self.assertEqual(missing, [])

    def test_pipeline_guide_documents_available_and_external_interfaces(self):
        guide = (DOCS / "guides/pipelines-and-steps.md").read_text(encoding="utf-8")

        self.assertIn("Estado actual: implementado", guide)
        self.assertIn("from storm.pipeline import", guide)
        self.assertIn("registry.discover", guide)
        self.assertIn("DLC", guide)

    def test_basic_example_executes_without_external_dependencies(self):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "packages/storm-engine/src")

        completed = subprocess.run(
            [sys.executable, str(ROOT / "examples/basic_study.py")],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("best_run=shifted", completed.stdout)
        self.assertIn("prediction=[5.0, 5.0]", completed.stdout)

    def test_pipeline_and_visualization_example_executes_without_external_dependencies(self):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "packages/storm-engine/src")

        completed = subprocess.run(
            [sys.executable, str(ROOT / "examples/pipeline_and_visualization.py")],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("prepared=[2, 4, 6]", completed.stdout)
        self.assertIn("rows=3", completed.stdout)

    def test_full_core_example_executes_without_external_dependencies(self):
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "packages/storm-engine/src")

        completed = subprocess.run(
            [sys.executable, str(ROOT / "examples/full_core_example.py")],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("best_run=mean", completed.stdout)
        self.assertIn("visualization=image/svg+xml", completed.stdout)

    def test_studio_components_guide_describes_current_storm_runtime(self):
        guide = (DOCS / "guides/studio-components.md").read_text(encoding="utf-8")

        for required in (
            "ModelRegistry",
            "MetricRegistry",
            "DataLoader",
            "ArtifactStore",
            "PipelineDataLoader",
            "ModelOutput",
            "no inventa un modelo",
            "conector",
            "RAINSTORM",
        ):
            self.assertIn(required, guide)

    def test_visual_study_describes_raw_data_classification_inspection(self):
        guide = (DOCS / "design/visual-study.md").read_text(encoding="utf-8")

        for required in (
            "datos crudos",
            "clasificación",
            "alineación temporal",
            "overlay",
            "TemporalClassificationVisualization",
        ):
            self.assertIn(required, guide)


if __name__ == "__main__":
    unittest.main()
