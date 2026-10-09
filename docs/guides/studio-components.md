# Componentes reales en STORM Studio

La API de experimentos versionados usa **adaptadores → contexto → pipeline →
entradas de modelo → estudio/corrida → métricas**. Ver el
[contrato compartido, tutoriales y límites](../guides/context-experiments.md).
`Study.from_experiment` y el editor del grafo comparten esa especificación.
Las secciones siguientes que usan `StudySpec`, `RunSpec` o `suite.steps`
describen las APIs legacy preservadas; no implican conversión automática al DAG.


Esta guía corrige la relación entre el estudio visual y el motor actual de
STORM. El editor no inventa una plataforma paralela: debe configurar y conectar
los contratos que ya existen en `storm`, y mostrar claramente qué capacidades
son propuestas futuras.

## Navegación de Studio

La navegación principal reúne cinco vistas:

- **Experimento:** catálogo agrupado con búsqueda, canvas e inspector. «Ordenar
  grafo» organiza los nodos por dependencias y modifica solamente el estado visual.
  «Archivo» reúne el ejemplo tabular, la importación y las exportaciones.
- **Preparación:** acceso directo al grafo del contexto y a las recetas existentes.
  La previsualización se ejecuta en el worker sobre una muestra acotada y compara
  entrada/salida, unidades, máscaras y procedencia. En pose, el frame mostrado se
  identifica mediante los padres de la ventana, no por su posición central.
- **Datos:** fuentes, asociaciones, particiones y previsualizaciones. Las tablas
  extensas se desplazan dentro de su panel; registrar fuentes y cargar benchmarks
  son acciones desplegables.
- **Ejecuciones:** veinte corridas por página; cada detalle conserva avisos, logs,
  errores y acciones. La paginación no elimina historial.
- **Resultados:** selección de corrida, métricas declaradas y estados por nodo.
  Los resultados del grafo incluyen distribución de etiquetas, geometría con ejes
  seleccionables, pose por sesión/frame y video cuando la fuente está vinculada.
  Se leen artefactos JSON sin deserializar modelos en Studio. Desde aquí se congela una selección y se solicita,
  por separado, la evaluación del test reservado.

Las herramientas de análisis de planes legacy aparecen al seleccionar una
corrida legacy. Sus recetas, configuración y modelos permanecen en **Planes
anteriores**. **Herramientas** reúne historial, trazabilidad y extensiones.
No se modifican particiones, recetas ni resultados al cambiar la presentación.
La interfaz se verifica en escritorio a 1920 × 1080 y 2560 × 1440.

## Qué existe hoy

El runtime actual tiene este recorrido:

```text
DataRef
  ↓ DataLoader o PipelineDataLoader
Dataset(inputs, targets, metadata)
  ↓ Study
RunEngine.train()
  ├─ ModelRegistry.build(model_type, model_config)
  ├─ model.fit(inputs, targets)
  ├─ model.predict(inputs) → ModelOutput
  ├─ MetricRegistry.get(name).evaluate(...)
  └─ ArtifactStore.save(model, output, RunRecord)
```

El estudio visual debe representar ese recorrido. `StudySpec`, `RunSpec`,
`DataRef`, `StepSpec` y `PipelineSpec` se guardan como configuración. Los
modelos, loaders, métricas y stores son objetos de runtime que se inyectan al
crear `Study`.

El `RunEngine` implementado ejecuta entrenamiento seguido de predicción y
métricas sobre el `Dataset` recibido. Todavía no hay métodos separados
`evaluate`, `infer`, `SearchStrategy`, `PretrainedModelLoader`, recuperación de
checkpoints ni persistencia de ejecuciones fallidas. Studio debe mostrar estas
capacidades como no disponibles hasta que se agreguen sus contratos.

## Catálogo de componentes y registros

### Pasos y visualizaciones

Son clases abstractas descubribles por reflexión:

```python
steps = StepRegistry()
steps.discover("rainstorm.integration.steps")

views = VisualizationRegistry()
views.discover("rainstorm.integration.visualizations")
```

El discovery importa recursivamente solamente el paquete habilitado y encuentra
subclases concretas de `PipelineStep` o `Visualization`. No se ejecuta al
importar STORM ni acepta paquetes arbitrarios enviados desde el navegador.

### Modelos

`ModelRegistry` usa builders explícitos. Un modelo existente se registra así:

```python
from storm import ModelOutput, ModelRegistry


class ExistingModelAdapter:
    def __init__(self, backend):
        self.backend = backend

    def fit(self, inputs, targets=None):
        self.backend.fit(inputs, targets)
        return self

    def predict(self, inputs):
        native = self.backend.predict(inputs)
        return ModelOutput(
            predictions=native,
            metadata={"adapter": type(self).__name__},
        )


def build_existing(config):
    from external_library import Estimator

    return ExistingModelAdapter(Estimator(**config))


models = ModelRegistry()
models.register("external.estimator", build_existing)
```

El builder recibe únicamente `model_config`, crea una instancia nueva y deja los
imports pesados dentro del plugin. Studio debe listar `external.estimator` a
partir del catálogo que el plugin entrega junto al registro. En el código actual
`ModelRegistry` no expone todavía un `available` público ni un descriptor de
formulario; esa es una extensión pendiente del adaptador de catálogo, no un
motivo para leer `_builders` desde la UI.

Un modelo ya implementado en RAINSTORM sigue el mismo patrón:

```python
def register_rainstorm_models(models):
    models.register("rainstorm.vame", build_vame_adapter)
    models.register("rainstorm.kpms", build_kpms_adapter)
```

El adapter traduce la salida nativa a `ModelOutput`. STORM no importa VAME,
Keypoint-MoSeq, pose ni ROI. El plugin RAINSTORM conserva sus configuraciones,
loaders y dependencias.

### Métricas

Una métrica implementa `evaluate(dataset, output, model)` y devuelve un escalar:

```python
class DomainAgreement:
    def evaluate(self, *, dataset, output, model):
        return agreement(dataset.targets, output.predictions)


metrics = MetricRegistry()
metrics.register("rainstorm.domain_agreement", DomainAgreement())
```

No asumir que cada modelo produce labels, embeddings o estados. Las métricas
conductuales se registran desde RAINSTORM. `accuracy`, `mae` y `mse` son las
métricas clásicas incluidas en STORM.

### Artefactos y recuperación

`FileArtifactStore` persiste referencias de modelos, outputs y `RunRecord` con
manifiestos y SHA-256. `RunResult` permite recuperar el modelo y la salida:

```python
model = result.load_model()
output = result.load_output()
```

Eso es recuperación de artefactos y resultados. No equivale a reanudar una
ejecución desde un checkpoint: esa capacidad todavía requiere un contrato de
checkpoint y soporte del adapter. Studio debe diferenciar las acciones «Cargar
modelo», «Abrir output», «Restaurar estado visual» y «Reanudar entrenamiento».

## Cómo cargar inputs sin ocultar el dominio

La frontera de entrada es `DataRef` → `Dataset`. El lector conoce el formato y
el plugin conoce la preparación:

```python
from storm import DataRef, Dataset, PipelineDataLoader


def read_application_data(reference: DataRef) -> Dataset:
    raw = repository.load(reference.identifier)
    return Dataset(
        inputs=raw.inputs,
        targets=raw.targets,
        metadata={"source": reference.identifier},
    )


loader = PipelineDataLoader(
    loader=read_application_data,
    runner=pipeline_runner,
)
```

Para RAINSTORM, `read_application_data` puede usar DLC, video, archivos de pose
o ROI, pero vive en RAINSTORM. Studio configura el `DataRef`, muestra metadata y
una previsualización limitada cuando el conector la ofrece. El navegador no
lee rutas arbitrarias ni ejecuta imports indicados por el documento.

El asistente de conectores guía la creación de este código mediante un formulario:

1. seleccionar formato o API;
2. indicar ubicación como referencia de workspace;
3. mapear inputs, targets y metadata;
4. declarar fingerprint y requisitos;
5. probar con una muestra;
6. revisar un diff de código y tests;
7. registrar el loader con aprobación explícita.

La asistencia puede generar un esqueleto, pero **no inventa un modelo**, no
ejecuta código nuevo, no instala dependencias ni registra un conector sin una
revisión humana. Si el formato es desconocido, debe marcar el conector como
incompleto y explicar qué contrato falta.

## Cómo incluir código existente

Hay tres formas, con diferente grado de integración:

| Caso | Adaptación | Registro |
|---|---|---|
| clase Python que ya cumple `fit`/`predict` | builder que la instancia y normaliza output | `ModelRegistry.register` |
| biblioteca externa | adapter + dependencia opcional + tests | builder con import local |
| código que necesita pipeline | `PipelineStep` que transforma `PipelineContext` | `StepRegistry.discover` o `register` |

El asistente de inclusión debe pedir el módulo, clase, constructor, configuración,
entrada/salida, versión y dependencia. Genera un adapter inicial como archivo
revisable. La integración se considera disponible solo después de que pasen los
tests de contrato: `fit`, `predict`, `ModelOutput`, serialización del modelo y
paridad con una salida nativa conocida.

Código ilustrativo de una integración RAINSTORM:

```python
def build_rainstorm_model(config):
    from rainstorm_adapter import RainstormBackend

    return RainstormModelAdapter(RainstormBackend(**config))


def register_rainstorm_components(models, metrics, steps, visualizations):
    models.register("rainstorm.behavior", build_rainstorm_model)
    metrics.register("rainstorm.behavior_score", BehaviorScore())
    steps.discover("rainstorm.integration.steps")
    visualizations.discover("rainstorm.integration.visualizations")
```

La composición se ejecuta al iniciar el runtime, no dentro del core de STORM.

## Comparar modelos en el estudio visual

La comparación parte de `StudyResults` y sus `RunResult`. Para cada corrida se
puede mostrar `RunRecord`: `run_id`, `execution_id`, `model_type`, `data_ref`,
`spec_fingerprint`, seed, métricas, timestamps y referencias de artefactos.

Una comparación debe verificar antes de ordenar: fingerprint de datos, versión
de pipeline, partición, versión de etiquetas y definición/dirección de métricas.
Si difieren, Studio permite inspección lado a lado, pero no presenta un ranking
como si fuera equivalente. Las visualizaciones se generan con
`VisualizationRequest`; no modifican la corrida ni el plan.

## Asistencia LLM dentro del estudio visual

El LLM es un proveedor opcional de propuestas de interfaz. Puede:

- explicar un contrato o error de validación;
- proponer un adapter de modelo o conector;
- sugerir configuración a partir de un schema;
- resumir diferencias entre corridas;
- proponer etiquetas para revisión humana.

No puede ejecutar un plan, aceptar etiquetas, cambiar un `RunSpec`, instalar
paquetes o acceder a datos fuera del contexto autorizado. El usuario debe ver
qué contexto se envía, proveedor, modelo, modo local/remoto y costo/estado.
Desactivarlo deja disponibles edición manual, validación, comparación y
recuperación. Una propuesta aceptada que cambia datos, labels o configuración
crea una nueva revisión con diff y fingerprint.

## Qué debe implementar Studio después

| Necesidad visual | Base STORM disponible | Extensión pendiente |
|---|---|---|
| listar modelos existentes | `ModelRegistry.register` | catálogo público con descriptors |
| configurar modelos | `RunSpec.model_type/model_config` | JSON Schema de formularios |
| cargar inputs | `DataRef`, `Dataset`, `DataLoader` | wizard y previsualización segura |
| preparar datos | `PipelineSpec`, `PipelineRunner`, `PipelineDataLoader` | puertos, schemas y validación visual |
| ejecutar estudio | `Study`, `RunEngine` | API web, worker y eventos |
| comparar modelos | `StudyResults`, `RunRecord` | vista comparativa y reglas de comparabilidad |
| recuperar modelo/output | `RunResult`, `ArtifactStore` | explorador de artefactos |
| reanudar checkpoint | no disponible | contrato y adapter de checkpoints |
| ayuda para código | contratos públicos | generador de adapter + diff + tests |
| etiquetas asistidas | no hay contrato universal | `AnnotationRevision` en Studio/plugin |
| LLM opt-in | no hay proveedor en core | `AssistantProvider` aislado y auditable |

Esta tabla es la frontera de implementación. La UI puede anticipar controles
para capacidades futuras, pero debe deshabilitarlos con una explicación concreta
hasta que exista el contrato correspondiente.

## Contratos de entrada antes de crear jobs

`Catalog.describe()` incluye `input_contract`, serializable y opcional.
PlanForm y submit usan [validate_plan](../api/interfaces.md); los errores bloquean jobs
por rama y las advertencias quedan visibles. Consultar el catálogo no construye modelos.
Hosts genéricos para controles/renderers y editor gráfico siguen propuestos en el
[plan vigente](../planning/suite-completion.md).


## Extensiones instaladas y reflexión (2026-10-03)

Instalar un paquete Python de plugin y habilitar su módulo `register(catalog)`
en `STORM_PLUGINS` en Studio y en el worker. Importar únicamente esos módulos
explícitos; no usar el descubrimiento recursivo sobre un paquete con runtimes
PyTorch/JAX. El builder debe importar las dependencias científicas al ejecutarse:

```python
from storm.suite import Component
from storm.contracts import ModelInputContract

def build(config):
    from my_plugin.runtime import MyModel
    return MyModel(config)

def register(catalog):
    catalog.register(Component('my_model', build, ('group', 'infer'),
        {'type': 'object', 'properties': {'states': {'type': 'integer', 'minimum': 2}}},
        input_contract=ModelInputContract(input_type='my_declared_input', preparation='external'),
        descriptor={'version': '1', 'backend': 'my_backend',
                    'source_modules': ['my_plugin.runtime'], 'dependencies': ['my-backend']}))
```

`reflect_schema` puede generar parámetros a partir de firmas/dataclasses. El
plugin declara por separado la preparación, granularidad, puertos, dimensiones y
restricciones cruzadas. Instalar una arquitectura nueva como componente distinto
preserva su identidad y evita atribuir equivalencia científica al backend original.
Studio no acepta código fuente para ejecutarlo en el servidor.

El host gráfico consume `Component.descriptor.graph`: `field`, `default`, `nodes`
y `slots`. El plugin aporta el compilador y `config_validator`; la UI sólo edita
la configuración declarada. El primer caso ejecutable es el VAE de RAINSTORM con
encoder GRU/LSTM. El editor general de pipeline, topologías arbitrarias y hosts
científicos adicionales siguen abiertos en ST-08/09/10 y RS-10/11.

## Evidencia visual de experimentos con contexto

El worker guarda `evidence_ref` junto al manifiesto de cada corrida. El artefacto
JSON conserva predicciones, máscaras, identidades, frames, entradas de referencia,
embeddings y pérdidas de entrenamiento cuando el modelo los declara. El checksum
se verifica al leerlo. «Preparar visualizaciones» recupera corridas anteriores
mediante un trabajo que lee sus artefactos; no entrena ni evalúa test.

Las distribuciones usan todas las predicciones válidas. La geometría muestra hasta
2.000 observaciones deterministas y permite elegir dimensiones; no aplica PCA o
UMAP implícitamente. La pose se consulta en bloques de hasta 500 observaciones de
una misma sesión. Las coordenadas de ventanas necesitan una identidad de referencia
preservada. Si falta, se informa la limitación y no se inventa una pose.

Las pérdidas de entrenamiento se distinguen de las métricas de validación. Las
métricas exploratorias requieren referencias válidas y una tarea compatible;
los estados de agrupamiento no se interpretan como clases de conducta.

Para video se necesitan archivos registrados, correspondencias de sesión/frame y
fuentes verificables. La pose preparada en centímetros se dibuja por separado de
la pose original en píxeles. Sin video compatible, las vistas de pose y geometría
siguen disponibles y Studio explica la fuente faltante.

Los trabajos `experiment_preview` ejecutan sólo antecesores de preparación con
muestreo declarado; rechazan modelos y evaluación. Necesitan un worker con las
bibliotecas del plugin y acceso a las rutas referenciadas por el estudio. Las
muestras completadas se reabren desde Ejecuciones y no cambian el grafo guardado.
