# Visualizaciones

> **Estado actual: plataforma y visualizadores SVG clásicos implementados;
> Matplotlib, Seaborn y Plotly son extensiones opcionales.**

STORM puede seleccionar, configurar y ejecutar visualizaciones de datos,
modelos, outputs o métricas sin depender de Matplotlib, Seaborn, Plotly o
pandas. La biblioteca gráfica y la semántica de cada figura pertenecen al
plugin que implementa `Visualization`.

## Contratos

```python
from storm.visualization import (
    Visualization,
    VisualizationRequest,
    VisualizationResult,
)


class MetricText(Visualization):
    visualization_type = "metric_text"

    def __init__(self, *, precision=3):
        self.precision = precision

    def render(self, request):
        text = ", ".join(
            f"{name}={value:.{self.precision}f}"
            for name, value in sorted(request.metrics.items())
        )
        return VisualizationResult(content=text, media_type="text/plain")
```

`VisualizationRequest` ofrece cinco entradas neutrales:

- `data`: dataset o vista de datos;
- `model`: modelo entrenado o adapter;
- `output`: `ModelOutput` u otra salida documentada por la integración;
- `metrics`: escalares por nombre;
- `metadata`: opciones de presentación que no forman parte de la configuración
  estable del plugin.

`VisualizationResult.content` puede ser una figura, HTML, SVG, bytes, una tabla
estructurada o texto. `media_type` declara cómo tratar ese contenido. Guardar o
publicar el resultado es responsabilidad de un exporter o del consumidor; el
manager no escribe archivos implícitamente.

## Descubrimiento automático

Las clases viven en un paquete de la aplicación y heredan de la interfaz:

```text
my_project/
└── visualizations/
    ├── __init__.py
    ├── data.py
    ├── models.py
    └── metrics.py
```

```python
from storm import VisualizationRegistry

registry = VisualizationRegistry()
registry.discover("my_project.visualizations")
print(registry.available)
```

`discover()` importa recursivamente solo el paquete indicado, encuentra las
subclases concretas y usa `visualization_type` como nombre estable. No necesita
decoradores ni una lista central. Los errores de importación no se ocultan.

## Configurar y renderizar

```python
from storm import (
    VisualizationManager,
    VisualizationRequest,
    VisualizationSpec,
)

manager = VisualizationManager(registry)
result = manager.render(
    VisualizationSpec("metric_text", {"precision": 2}),
    VisualizationRequest(metrics={"accuracy": 0.916}),
)
```

`VisualizationSpec` es JSON-compatible. Las figuras pueden, por lo tanto,
reconstruirse desde una configuración sin serializar objetos de Matplotlib o
Plotly.

## Visualizadores clásicos incluidos

El paquete `storm.visualization.classic` usa solo la biblioteca estándar y
devuelve SVG:

| `visualization_type` | Entrada | Resultado |
|---|---|---|
| `metric_bar` | `request.metrics` | barras de métricas escalares |
| `data_line` | secuencia numérica en `request.data` | serie lineal |
| `data_scatter` | pares `(x, y)` en `request.data` | dispersión bidimensional |
| `prediction_histogram` | `request.output.predictions` | histograma numérico |

```python
from pathlib import Path

registry = VisualizationRegistry()
registry.discover("storm.visualization.classic")

result = VisualizationManager(registry).render(
    VisualizationSpec("metric_bar", {"title": "Validation"}),
    VisualizationRequest(metrics={"mae": 1.2, "mse": 2.4}),
)

Path("validation.svg").write_text(result.content, encoding="utf-8")
```

Parámetros comunes: `width`, `height` y `title`. El histograma acepta `bins`.
Los visualizadores validan entradas vacías, valores no numéricos y valores no
finitos.

## Evidencia temporal para modelos de comportamiento

Un plugin de dominio puede usar el mismo contrato para mostrar el dato original
junto con la salida del modelo. STORM no sabe si `request.data` es un video,
una trayectoria o una señal; el plugin debe entregar una vista cruda y declarar
su índice temporal en `request.metadata`. La salida debe conservar la relación
entre observación y predicción.

RAINSTORM puede registrar un visualizador como:

```python
class TemporalClassificationVisualization(Visualization):
    visualization_type = "temporal_classification_overlay"

    def render(self, request):
        return VisualizationResult(
            content=render_raw_with_labels(
                request.data,
                request.output,
                request.metadata,
            ),
            media_type="image/svg+xml",
            metadata={"alignment": "frame"},
        )
```

La vista debe poder mostrar datos crudos, bandas de clasificación, etiquetas
humanas, desacuerdos y un cursor sincronizado. Si no existe una alineación
verificable por frame, timestamp o índice, debe devolver una tabla o advertencia,
no superponer etiquetas de forma aproximada. Lectura de DLC, video, pose, ROI y
la semántica de clases siguen siendo responsabilidad del adaptador RAINSTORM.

## Qué se rescata de `Tesis_Facu`

El análisis encontró lógica reusable en barplots de métricas, heatmaps,
series temporales, embeddings y radares. No se copiaron directamente porque
mezclan una o más de estas decisiones:

- tipos `pandas.DataFrame` obligatorios;
- imports de Matplotlib/Seaborn en el mismo módulo;
- aplicación automática del tema RAINSTORM;
- nombres como cluster, ROI, variante o syllable;
- escritura directa de PNG/PDF.

Las cuatro vistas incluidas cubren el núcleo neutral sin copiar esos módulos.
La migración de las variantes más ricas consiste en crear plugins opcionales,
por ejemplo
`storm-visualization-matplotlib`, que implementen `Visualization`. Los gráficos
de ROI, pose, trayectorias animales y reportes de la tesis permanecen en
RAINSTORM.

## Dependencias opcionales

Una integración puede declarar:

```toml
[project.optional-dependencies]
plots = ["matplotlib>=3.8", "seaborn>=0.13", "pandas>=2.2"]
```

STORM conserva así un core liviano. Instalar el framework no importa ni exige
ningún backend gráfico.
