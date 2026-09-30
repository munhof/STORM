# Pipelines, contexto y preparación de datos

## Alineación de observaciones

Cuando un paso preserva cantidad y orden de observaciones no necesita código
adicional. Si filtra o reordena, debe actualizar el mapeo de fuentes del
contexto. STORM usa ese mapeo para asociar outputs, targets y evidencia con la
observación correcta; cambiar filas sin declararlo detiene la ejecución.

```python
class KeepLast(PipelineStep):
    step_type = "keep_last"

    def process(self, context):
        context.data = context.data[1:]
        context.metadata["observation_indices"] = (
            context.metadata["observation_indices"][1:]
        )
        return context
```

El mapeo contiene índices de observación originales, debe ser único y debe tener
la misma longitud que `context.data`. Una transformación que agregue, fusione o
reemplace observaciones no está soportada todavía: debe exponerse como un
adaptador con una semántica de alineación específica, no simularse como un paso
ordinario.

> **Estado actual: implementado.** `storm.pipeline` contiene specs
> serializables, contexto neutral, interfaz de pasos, registro por reflexión,
> runner secuencial y composición con `DataLoader`.

## Frontera

STORM aporta la plataforma de manipulación:

- `PipelineContext` transporta datos, targets, metadata, estado y artefactos;
- `PipelineStep` define la interfaz de una transformación;
- `StepRegistry` descubre o registra implementaciones;
- `StepSpec` y `PipelineSpec` describen el flujo como JSON;
- `PipelineRunner` construye y ejecuta los pasos en orden;
- `PipelineDataLoader` inserta la preparación antes de un `Study`;
- `StepExecution` registra versión, estado, tiempos y errores de cada paso.

El consumidor aporta las operaciones. Un lector de DLC, una alineación de pose,
un cálculo de ROI o la creación de ventanas VAME no pertenecen a STORM. Pueden
implementarse como pasos de RAINSTORM usando la interfaz pública.

## 1. Implementar un paso

```python
from storm.pipeline import PipelineContext, PipelineStep


class SelectColumns(PipelineStep):
    step_type = "select_columns"
    version = "1"

    def __init__(self, *, names):
        self.names = tuple(names)

    def process(self, context: PipelineContext) -> PipelineContext:
        context.data = context.data[list(self.names)]
        context.metadata["columns"] = list(self.names)
        return context
```

Reglas del contrato:

- la clase debe heredar de `PipelineStep` para que la reflexión pueda hallarla;
- `step_type` debe ser estable porque aparece en archivos de configuración;
- `version` identifica la implementación ejecutada;
- el constructor recibe solo la configuración del `StepSpec`;
- `process()` recibe y devuelve `PipelineContext`;
- el paso puede mutar el contexto o devolver otro contexto;
- las dependencias opcionales deben importarse en el plugin que las utiliza.

Si no se declara `step_type`, se usa el nombre de clase. Esto facilita adaptar
pasos existentes, aunque para configuraciones duraderas se recomienda un nombre
explícito.

## 2. Organizar y descubrir pasos

```text
my_project/
└── preparation/
    ├── __init__.py
    ├── cleaning.py
    └── features.py
```

```python
from storm.pipeline import StepRegistry

registry = StepRegistry()
found = registry.discover("my_project.preparation")
print(found)
print(registry.available)
```

`registry.discover()` realiza tres operaciones:

1. importa recursivamente los módulos del paquete indicado;
2. encuentra por reflexión las subclases concretas de `PipelineStep`;
3. las registra por `step_type`.

STORM no escanea el entorno al importarse ni conoce el nombre del paquete
consumidor. El opt-in explícito evita cargar plugins pesados accidentalmente,
mientras que el registro interno automático elimina listas y decoradores
duplicados. Un error de importación del plugin se propaga como
`PluginDiscoveryError` con el módulo que falló.

También se puede registrar una clase de forma explícita:

```python
registry.register(SelectColumns)
```

Esto resulta útil para tests o plugins construidos dinámicamente.

## 3. Declarar una pipeline serializable

```python
from storm.pipeline import PipelineSpec, StepSpec

spec = PipelineSpec(
    pipeline_id="tabular-v1",
    steps=(
        StepSpec("select_columns", {"names": ["x", "y"]}),
        StepSpec("fill_missing", {"value": 0}),
    ),
    metadata={"output_schema": "features-v1"},
)
```

`spec.to_dict()` genera datos JSON-compatible y
`PipelineSpec.from_dict(data)` los reconstruye. `spec.fingerprint` es un SHA-256
del JSON canónico e incluye orden, tipos, configuración y metadata.

Ejemplo JSON equivalente:

```json
{
  "pipeline_id": "tabular-v1",
  "steps": [
    {"type": "select_columns", "config": {"names": ["x", "y"]}},
    {"type": "fill_missing", "config": {"value": 0}}
  ],
  "metadata": {"output_schema": "features-v1"}
}
```

## 4. Construir y ejecutar

```python
from storm.pipeline import PipelineContext, PipelineRunner

runner = PipelineRunner.from_spec(spec, registry=registry)
context = runner.run(
    PipelineContext(
        data=raw_table,
        dataset_id="session-01",
        metadata={"source_fingerprint": "sha256:..."},
    )
)

prepared = context.data
trace = context.executions
```

El runner conserva el orden del spec. Por cada intento agrega un
`StepExecution` con índice, tipo, versión, timestamps, duración y estado. Si un
paso falla, registra `failed` y vuelve a lanzar la excepción original; no la
oculta ni continúa con datos parciales.

## 5. Usar la pipeline en un estudio

El lector sigue perteneciendo a la aplicación:

```python
from storm import DataRef, Dataset, PipelineDataLoader


def read_application_data(reference: DataRef) -> Dataset:
    raw = repository.load(reference.identifier)
    return Dataset(
        inputs=raw.values,
        targets=raw.labels,
        metadata={"source": reference.identifier},
    )


data_loader = PipelineDataLoader(
    loader=read_application_data,
    runner=runner,
)

study = Study(..., data_loader=data_loader)
```

El wrapper:

- resuelve el `Dataset` una vez;
- crea un contexto con inputs, targets y metadata;
- ejecuta la pipeline;
- devuelve otro `Dataset`;
- añade `pipeline_id`, `pipeline_fingerprint` y `pipeline_executions` a su
  metadata.

Los pasos pueden transformar tanto `context.data` como `context.targets`. No
necesitan conocer `Study`, modelos ni métricas.

## 6. Usar correctamente el contexto

Campos neutrales:

| Campo | Uso |
|---|---|
| `data` | dato principal que se transforma |
| `targets` | objetivo opcional que puede acompañar o transformarse |
| `dataset_id` | identidad lógica de la fuente |
| `metadata` | descripciones y schema del dato |
| `state` | estado intermedio entre pasos |
| `artifacts` | valores o referencias producidos durante el flujo |
| `executions` | traza añadida por el runner |

`get_info(name)` busca en `state`, `artifacts` y `metadata`.
`summarize()` describe tipos, shapes o longitudes sin importar NumPy o pandas.

No agregue a este contexto del framework campos como `tracking_data`,
`video_reader`, `rois`, `bodyparts` o `syllable_labels`. RAINSTORM puede guardar
estructuras propias en `data`, `state` o crear un adapter de contexto fuera de
STORM.

## Diferencias con `Tesis_Facu`

| Implementación de tesis | API de STORM | Motivo |
|---|---|---|
| `StepRegistry` importa `rainstorm.application.steps` | `discover(package)` recibe el paquete | inversión de dependencias |
| config `{"step": ..., "params": ...}` | `{"type": ..., "config": ...}` | vocabulario uniforme con modelos y visualizaciones |
| runner ignora el contexto retornado | runner encadena el contexto retornado | composición explícita y testeable |
| comprobación de `tracking_data` | validación de `PipelineContext` | independencia de dominio |
| `ArtifactManager()` dentro del runner | sin backend implícito | persistencia y ejecución son responsabilidades separadas |
| caché exige `feature_tensor` | caché aún no incluida | evitar un contrato de pose disfrazado de core |

Adapter temporal para configs antiguas:

```python
def migrate_step_config(old):
    return StepSpec(
        step_type=old["step"],
        config=old.get("params", {}),
    )
```

Los pasos concretos de RAINSTORM pueden conservar su nombre de clase y adaptar
el acceso al nuevo contexto. La lectura DLC y los algoritmos de centrado,
filtrado, suavizado o alineación no se copian a STORM.

## Caché y artefactos

La primera implementación no incluye caché de pipeline. Cuando se agregue,
será una policy inyectada que use `ArtifactStore` y calcule su clave con:

```text
fingerprint de entrada
 + fingerprint de PipelineSpec
 + versiones de pasos
 + schema de salida
```

El runner nunca debe construir un store ni asumir nombres de features.

## Preparación codeless en RAINSTORM Studio

En un estudio, **Preparar** guarda una revisión de receta ligada a una revisión
de datos. El editor agrega, reordena y quita pasos; permite previsualizar las
observaciones de entrenamiento y evaluación con su ID original. El centrado
calcula una media por coordenada usando solo entrenamiento. Si el plugin de
RAINSTORM está activo, también aparecen selección de coordenadas, recentrado por
punto corporal, alineación canónica a 45°, filtro de confianza y ventanas
temporales. La orientación aplica la misma fórmula documentada por la receta
supervisada histórica; los puntos se eligen por nombre y se resuelven contra las
coordenadas que queden disponibles en ese punto de la pipeline.

Una receta se puede ejecutar como trabajo independiente para crear otra revisión
de dataset con su propio artefacto, fingerprint y enlace a la fuente. En
**Configurar**, el investigador puede seleccionar ese dataset procesado, o
aplicar una receta guardada durante la ejecución del modelo. La revisión del
plan conserva el ID de la receta y los pasos resueltos que ejecutó el worker.
