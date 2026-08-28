# STORM: System for Traceable Orchestration, Reuse and Modeling

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

Build the documentation locally with:

```bash
uv run --extra docs mkdocs serve
```

`FileArtifactStore` uses pickle for model payloads. Only load artifacts from a
trusted store.

License: GNU LGPL-3.0-or-later.
