# Primeros pasos

Esta guía instala STORM, ejecuta sus tests y construye un estudio completo sin
dependencias científicas externas.

## Requisitos

- Python 3.11 o posterior.
- Git.
- Opcional: `uv` para resolución y entornos reproducibles.

El runtime de STORM usa únicamente la biblioteca estándar de Python. `pytest`
es una dependencia opcional de desarrollo.

El repositorio completo es ahora un monorepo. La aplicación web usa Django;
el paquete del motor continúa sin esas dependencias. Para abrir la aplicación,
seguir la [guía de Django Studio](guides/django-suite.md).

## Instalación con `uv`

Desde un checkout del repositorio:

```bash
uv sync --extra test --extra docs
uv run playwright install chromium
uv run pytest
```

Para ejecutar el ejemplo:

```bash
uv run python examples/basic_study.py
uv run python examples/pipeline_and_visualization.py
uv run python examples/full_core_example.py
```

## Instalación con `venv` y `pip`

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e packages/storm-engine -e packages/storm-visualization -e packages/storm-studio
python -m pip install pytest pytest-django playwright
python -m playwright install chromium
pytest
python examples/basic_study.py
python examples/pipeline_and_visualization.py
python examples/full_core_example.py
```

En PowerShell, la activación del entorno es:

```powershell
.venv\Scripts\Activate.ps1
```

## Resultado esperado

```text
best_run=shifted
prediction=[5.0, 5.0]
prepared=[2, 4, 6]
rows=3
best_run=mean
visualization=image/svg+xml
```

## Qué construye el ejemplo

El ejemplo realiza siete acciones:

1. implementa un modelo con `fit()` y `predict()`;
2. implementa una métrica escalar con `evaluate()`;
3. registra ambos componentes con nombres de runtime;
4. crea un `DataRef` y dos `RunSpec` serializables;
5. resuelve los datos mediante un `DataLoader`;
6. ejecuta y persiste las dos corridas;
7. selecciona el resultado con mayor métrica, recarga el modelo y predice.

La implementación completa está en `examples/basic_study.py`.

`examples/pipeline_and_visualization.py` muestra un paquete externo con un paso
y una vista descubribles. `examples/full_core_example.py` encadena los pasos
dummy incluidos, modelos dummy, métricas clásicas, estudios, artefactos y un
gráfico SVG.

Los mismos conceptos están disponibles como tutoriales editables en
`examples/notebooks/01_full_core_dummy_study.ipynb` y
`examples/notebooks/02_sklearn_knn_study.ipynb`. El segundo notebook usa
`scikit-learn` para entrenar un KNN con Penguins si `seaborn` está disponible,
o Iris como fallback, y muestra extensiones locales para filtrar datos, agregar
ratios morfológicos de pico/cuerpo/aleta, graficar esos índices, comparar
accuracy y renderizar una matriz de confusión.

Para preparar un entorno con soporte para esos notebooks:

```bash
uv sync --extra examples
```

## Estructura mínima de una aplicación

Una aplicación que consume STORM puede empezar con esta estructura:

```text
my_application/
├── pyproject.toml
├── src/my_application/
│   ├── data.py          # DataLoader y preparación de datos
│   ├── models.py        # modelos o adaptadores
│   ├── metrics.py       # métricas del dominio
│   ├── preparation/     # PipelineStep descubribles
│   ├── visualizations/  # Visualization descubribles
│   └── runtime.py       # registros y ArtifactStore
└── studies/
    └── baseline.json    # StudySpec serializado
```

STORM no exige este layout; su propósito es mostrar dónde debe vivir cada
responsabilidad.

## Próximos pasos

- Aprender a construir [estudios y corridas](guides/studies-and-runs.md).
- Crear un [modelo compatible](guides/models.md).
- Implementar una [métrica](guides/metrics.md).
- Comprender la [persistencia y recuperación](guides/artifacts.md).
- Preparar datos con [pipelines y pasos](guides/pipelines-and-steps.md).
- Integrar [visualizaciones](guides/visualizations.md).
- Integrar una biblioteca o aplicación mediante
  [adaptadores](guides/integrations.md).

## Errores frecuentes

### Modelo no registrado

`KeyError: No model builder registered...` significa que `RunSpec.model_type`
no coincide con ningún nombre registrado en `ModelRegistry`.

### Métrica no registrada

Cada nombre de `StudySpec.metrics` debe existir en el `MetricRegistry` entregado
a `Study`.

### Output incompatible

`Model.predict()` debe devolver una instancia de `ModelOutput`, no un array,
lista o dataframe directamente. Un adaptador debe envolver el resultado.

### Error de pickle

El backend local persiste el modelo completo. La clase debe poder serializarse
con pickle y estar disponible al recargarlo. Para bibliotecas que usan su propio
formato conviene implementar otro `ArtifactStore` o un wrapper persistible.
