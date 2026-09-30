# STORM: System for Traceable Orchestration, Reuse and Modeling

## Local Django suite

The monorepo now contains `packages/storm-engine`, `packages/storm-studio` and
`packages/storm-visualization`. Python imports remain `storm` and
`storm_studio_visual`. The old `studio-visual/web-demo` is an archived mockup.

```bash
uv sync --extra test --extra docs
uv run storm-studio migrate
uv run storm-studio demo
uv run storm-studio runserver 127.0.0.1:8000
```

Open **http://127.0.0.1:8000**. In a second terminal, from the same directory:

```bash
uv run storm-studio worker
```

Workspace data is stored in `.storm/`; set `STORM_WORKSPACE` to an absolute path
in both terminals to use another directory. Start with the
[Django suite guide](docs/guides/django-suite.md), including current limitations.

```bash
uv run pytest
uv run mkdocs build --strict
```

Browser tests require Chromium: `uv run playwright install chromium`.
To run without browser infrastructure: `uv run pytest --ignore=tests/test_studio_browser.py`.

A domain-neutral Python framework for traceable orchestration, reproducible
model studies, and reusable experimental artifacts.

STORM is in pre-alpha development. The first implemented vertical slice covers:

- JSON-serializable study and run specifications;
- explicit model and metric registries;
- generic model training and prediction contracts;
- deterministic standard-library seeding with extension hooks;
- local artifact persistence with manifests and SHA-256 verification;
- result selection and persisted-model recovery;
- serializable data-preparation pipelines with execution traces;
- opt-in reflection discovery for steps and visualizations;
- classic dependency-free SVG visualizers.

The runtime core has no scientific-computing dependency and contains no
RAINSTORM, pose, ROI, VAME, or Keypoint-MoSeq logic. Domain applications supply
data loaders, processing steps, model adapters, metrics, visualizers, and
optional seeders.

```python
study = Study(
    spec,
    data_loader=load_data,
    models=model_registry,
    metrics=metric_registry,
    artifacts=FileArtifactStore("artifacts"),
)

results = study.run()
best = results.select("validation_score", maximize=True)
model = best.load_model()
prediction = model.predict(new_data)
```

Start with the [documentation home](docs/index.md) for installation, concepts,
architecture, public API, extension interfaces, models, metrics, artifacts,
pipelines, and integrations. The
[initial extraction analysis](docs/architecture/initial-extraction.md) records
the boundary found in `Tesis_Facu` and the incremental migration plan.

The proposed browser-based [STORM Studio](docs/architecture/visual-studio.md)
is documented against the current runtime. The
[components and integrations guide](docs/guides/studio-components.md) shows how
to register existing models, metrics, steps, visualizers and data connectors;
it does not invent a second execution engine.

Build the documentation locally with:

```bash
uv run --extra docs mkdocs serve
```

`FileArtifactStore` uses pickle for model payloads. Only load artifacts from a
trusted store.

License: GNU LGPL-3.0-or-later.
