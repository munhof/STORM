# API pública

La superficie pública del corte actual se importa desde `storm`. Esta página
describe únicamente comportamiento implementado.

## Configuración y datos

### `DataRef`

```python
DataRef(
    identifier: str,
    fingerprint: str,
    metadata: Mapping[str, JsonValue] = {},
)
```

Identidad serializable de un dataset. `identifier` es el nombre lógico;
`fingerprint` debe ser calculado por la aplicación a partir de los datos o su
manifiesto. STORM no lee el dataset para verificarlo.

Métodos:

- `to_dict() -> dict`: representación compatible con JSON;
- `from_dict(data) -> DataRef`: reconstrucción desde datos.

### `Dataset`

```python
Dataset(
    inputs: Any,
    targets: Any = None,
    metadata: Mapping[str, Any] = {},
)
```

Objeto de runtime devuelto por el loader. Puede contener arrays, dataframes,
tensores u objetos propios. No forma parte del spec serializable.

### `RunSpec`

```python
RunSpec(
    run_id: str,
    model_type: str,
    model_config: Mapping[str, JsonValue],
    seed: int = 0,
    tags: Mapping[str, JsonValue] = {},
)
```

Describe una corrida. `model_type` se resuelve en `ModelRegistry`. La propiedad
`fingerprint` calcula un SHA-256 sobre el JSON canónico del spec.

Métodos:

- `to_dict() -> dict`;
- `from_dict(data) -> RunSpec`;
- `fingerprint -> str`.

### `StudySpec`

```python
StudySpec(
    study_id: str,
    data: DataRef,
    runs: tuple[RunSpec, ...],
    metrics: tuple[str, ...] = (),
)
```

Solicitud serializable de un estudio. Requiere al menos una corrida y rechaza
`run_id` duplicados dentro del mismo estudio.

Métodos:

- `to_dict() -> dict`;
- `from_dict(data) -> StudySpec`.

## Modelos

### `Model`

Protocolo estructural con `fit(inputs, targets=None)` y
`predict(inputs) -> ModelOutput`. No debe instanciarse directamente.

### `ModelOutput`

```python
ModelOutput(
    predictions: Any,
    metadata: Mapping[str, Any] = {},
)
```

Salida neutral del modelo. Toda salida externa debe envolverse en este objeto.
Embeddings, probabilidades o referencias adicionales pueden viajar en metadata
mientras la integración documente su schema.

### `ModelBuilder`

Alias de tipo:

```python
Callable[[Mapping[str, JsonValue]], Model]
```

Un builder recibe exclusivamente la configuración serializable de la corrida y
devuelve un modelo compatible.

### `ModelRegistry`

```python
registry = ModelRegistry()
registry.register(model_type, builder, replace=False)
model = registry.build(model_type, config)
```

El registro es local a cada runtime. Registrar dos veces el mismo nombre sin
`replace=True` produce `ValueError`.

## Métricas

### `Metric`

Protocolo estructural con:

```python
evaluate(*, dataset: Dataset, output: ModelOutput, model: Model) -> int | float
```

Cada métrica registrada produce un escalar.

### `MetricRegistry`

```python
registry = MetricRegistry()
registry.register(name, metric, replace=False)
metric = registry.get(name)
```

Los nombres solicitados en `StudySpec.metrics` se resuelven en este registro.

### `Accuracy`, `MeanAbsoluteError` y `MeanSquaredError`

Implementaciones clásicas sin dependencias científicas. Las tres requieren
targets no vacíos y de la misma longitud que las predicciones. Se registran con
`register_classic_metrics()` desde `storm.metrics.classic` bajo los nombres
`accuracy`, `mae` y `mse`.

## Artefactos

### `ArtifactRef`

```python
ArtifactRef(
    artifact_id: str,
    kind: str,
    digest: str,
    uri: str,
)
```

Referencia estable a un artefacto. Implementa `to_dict()` y `from_dict()`.

### `ArtifactStore`

Protocolo de persistencia con `save()`, `load()` y `resolve()`. Permite sustituir
el filesystem por object storage, una base de datos u otro backend.

### `FileArtifactStore`

```python
store = FileArtifactStore(root)
reference = store.save(
    kind="models",
    artifact_id="execution-...",
    value=model,
    metadata={"study_id": "demo"},
)
value = store.load(reference)
reference = store.resolve(kind="models", artifact_id="execution-...")
```

Backend local basado en manifiestos JSON y payloads pickle. Solo debe cargar
artefactos confiables.

## Ejecución

### `Seeder`

Alias `Callable[[int], None]`. Los seeders se ejecutan después de
`random.seed()` y antes de construir el modelo.

### `RunEngine`

```python
engine = RunEngine(
    models=model_registry,
    metrics=metric_registry,
    artifacts=artifact_store,
    seeders=(),
)
result = engine.train(
    study_id=...,
    data_ref=...,
    dataset=...,
    spec=...,
    metric_names=...,
)
```

Ejecuta una sola corrida. La API actual solo implementa entrenamiento seguido
opcionalmente por métricas sobre la predicción del mismo `Dataset`.

### `RunRecord`

Registro persistible de una ejecución exitosa. Contiene:

- `study_id`, `run_id`, `execution_id` y `model_type`;
- `data_ref`, `spec_fingerprint` y `seed`;
- `metrics`;
- `model_artifact` y `output_artifact`;
- timestamps, duración y tags.

### `RunResult`

Handle de runtime que expone los campos de `RunRecord` y mantiene el store
necesario para recuperar payloads.

```python
model = result.load_model()
output = result.load_output()
recovered = RunResult.recover(store, artifact_id=execution_id)
```

## Estudios

### `DataLoader`

Alias `Callable[[DataRef], Dataset]`. La aplicación implementa resolución,
validación y preparación de datos.

### `Study`

```python
Study(
    spec: StudySpec,
    *,
    data_loader: DataLoader,
    models: ModelRegistry,
    artifacts: ArtifactStore,
    metrics: MetricRegistry | None = None,
    seeders: Sequence[Seeder] = (),
)
```

`run() -> StudyResults` resuelve el dataset una vez y ejecuta los `RunSpec` en
orden, de forma secuencial.

### `StudyResults`

Secuencia inmutable de `RunResult`.

```python
best = results.select("score", maximize=True)
lowest = results.select("loss", maximize=False)
```

La selección ignora resultados que no contienen la métrica y produce `KeyError`
si ninguno la contiene.

## Pipelines y preparación de datos

### `PipelineContext`

Estado mutable y neutral compartido por pasos. Expone `data`, `targets`,
`dataset_id`, `metadata`, `artifacts`, `state` y `executions`. Los métodos
`get_info()` y `summarize()` permiten inspección sin dependencias científicas.

### `PipelineStep`

Clase abstracta para extensiones descubribles. Una implementación declara
`step_type`, opcionalmente `version`, e implementa:

```python
process(context: PipelineContext) -> PipelineContext
```

### `StepSpec`

```python
StepSpec(
    step_type: str,
    config: Mapping[str, JsonValue] = {},
)
```

Implementa `to_dict()` y `from_dict()`.

### `PipelineSpec`

```python
PipelineSpec(
    pipeline_id: str,
    steps: tuple[StepSpec, ...],
    metadata: Mapping[str, JsonValue] = {},
)
```

Implementa `to_dict()`, `from_dict()` y la propiedad `fingerprint`.

### `StepRegistry`

```python
registry = StepRegistry()
registry.register(MyStep)
registry.discover("my_package.steps")
step = registry.build("my_step", {"option": 1})
available = registry.available
```

`discover()` importa recursivamente el paquete indicado y registra sus
subclases concretas de `PipelineStep`.

### `PipelineRunner`

```python
runner = PipelineRunner.from_spec(spec, registry=registry)
context = runner.run(PipelineContext(data=raw_data))
```

Ejecuta en orden, encadena el contexto devuelto y agrega trazas.

### `StepExecution`

Registro inmutable de un intento de paso: índice, tipo, versión, estado,
timestamps, duración y error opcional. `to_dict()` produce una representación
data-only.

### `PipelineDataLoader`

```python
loader = PipelineDataLoader(loader=base_loader, runner=runner)
dataset = loader(data_ref)
```

Compone un `DataLoader` existente con la pipeline y añade identidad,
fingerprint y trazas de preparación a la metadata del `Dataset`.

## Visualización

### `Visualization`

Clase abstracta para plugins descubribles. Declara `visualization_type` e
implementa `render(request) -> VisualizationResult`.

### `VisualizationRequest`

```python
VisualizationRequest(
    data=None,
    model=None,
    output=None,
    metrics={},
    metadata={},
)
```

Contenedor neutral para vistas de datos, modelos, outputs y métricas.

### `VisualizationResult`

```python
VisualizationResult(content=figure, media_type="image/svg+xml", metadata={})
```

El contenido depende del backend. `media_type` nunca puede estar vacío.

### `VisualizationSpec`

Selección serializable con `visualization_type` y `config`. Implementa
`to_dict()` y `from_dict()`.

### `VisualizationRegistry`

```python
registry = VisualizationRegistry()
registry.register(MyVisualization)
registry.discover("my_package.visualizations")
visualization = registry.build("metric_bar", {"title": "Score"})
```

### `VisualizationManager`

```python
result = VisualizationManager(registry).render(spec, request)
```

Resuelve la implementación configurada, la ejecuta y valida el tipo de salida.

## Versión

La versión del paquete se expone como `storm.__version__`. El corte actual es
pre-alpha y todavía puede introducir cambios incompatibles.
