# Interfaces y contratos

STORM combina protocolos estructurales con interfaces abstractas. Modelos y
métricas no necesitan herencia. Pasos y visualizaciones sí heredan de una clase
base porque el descubrimiento por reflexión necesita una relación nominal que
pueda enumerarse sin instanciar objetos.

## `Model`

Contrato equivalente:

```python
from typing import Any, Protocol, Self

from storm import ModelOutput


class Model(Protocol):
    def fit(self, inputs: Any, targets: Any = None) -> Self:
        ...

    def predict(self, inputs: Any) -> ModelOutput:
        ...
```

Invariantes:

- `fit` debe dejar la instancia lista para `predict`;
- debe aceptar `targets=None`, aunque el algoritmo no use targets;
- `predict` debe devolver `ModelOutput`;
- la instancia persistida debe contener el estado aprendido o delegar su
  recuperación a una estrategia de persistencia compatible;
- el modelo no debe depender del `Study` que lo ejecuta.

STORM no inspecciona `is_fitted`, losses, embeddings ni parámetros internos.

## `ModelBuilder`

```python
def build_model(config: Mapping[str, JsonValue]) -> Model:
    ...
```

El builder constituye el composition root de una integración. Debe:

- validar o convertir el diccionario a la configuración nativa;
- importar dependencias pesadas de forma local cuando resulte conveniente;
- construir una instancia nueva por corrida;
- no recibir datos, stores ni el `StudySpec` completo;
- no aplicar semillas globales: esa responsabilidad corresponde a los seeders.

## `Metric`

```python
class Metric(Protocol):
    def evaluate(
        self,
        *,
        dataset: Dataset,
        output: ModelOutput,
        model: Model,
    ) -> int | float:
        ...
```

La métrica puede usar inputs, targets, output o estado público del modelo. Debe
devolver un único escalar convertible a `float`. Si se necesitan tres valores,
se registran tres métricas o se define una capa externa de reporting.

La dirección de optimización no forma parte del protocolo actual. El consumidor
elige `maximize=True` o `False` al seleccionar.

## `ArtifactStore`

```python
class ArtifactStore(Protocol):
    def save(
        self,
        *,
        kind: str,
        artifact_id: str,
        value: Any,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        ...

    def load(self, reference: ArtifactRef) -> Any:
        ...

    def resolve(self, *, kind: str, artifact_id: str) -> ArtifactRef:
        ...
```

Un backend debe garantizar:

- que la referencia devuelta identifica el payload guardado;
- que `load(save(...))` recupera un valor equivalente;
- que `resolve` no confunde kind e ID;
- que los errores de ausencia e integridad son explícitos;
- que metadata y payload mantienen la relación de lineage;
- que documenta atomicidad, concurrencia, seguridad y consistencia.

El engine usa actualmente los kinds `models`, `outputs` y `runs`.

## `DataLoader`

```python
DataLoader = Callable[[DataRef], Dataset]
```

El loader es la frontera entre una identidad serializable y datos de runtime.
Puede ser una función, instancia callable o método ligado.

Responsabilidades recomendadas:

1. localizar el recurso indicado por `DataRef.identifier`;
2. comprobar el fingerprint cuando la aplicación pueda hacerlo;
3. leer y validar datos;
4. ejecutar preparación determinista;
5. devolver `Dataset(inputs, targets, metadata)`.

El loader se invoca una vez por `Study.run()`, no una vez por corrida.

## `Seeder`

```python
Seeder = Callable[[int], None]
```

Debe aplicar la semilla a una biblioteca o subsistema concreto. Los seeders
deben ser pequeños, idempotentes y estar ordenados explícitamente.

```python
def seed_external_library(seed: int) -> None:
    external_library.set_seed(seed)
```

## `ModelOutput`

Aunque es una dataclass concreta y no un protocolo, constituye la frontera de
salida:

```python
ModelOutput(predictions=..., metadata={...})
```

Convenciones recomendadas para metadata:

- claves `str` estables;
- documentar tipos y shapes en la integración;
- evitar almacenar el mismo objeto pesado en predictions y metadata;
- colocar artefactos grandes separados en el store cuando se agregue soporte
  explícito;
- no asumir que otras métricas conocen claves privadas.

## `PipelineStep`

```python
class PipelineStep(ABC):
    step_type: ClassVar[str]
    version: ClassVar[str]

    @abstractmethod
    def process(self, context: PipelineContext) -> PipelineContext:
        ...
```

Invariantes:

- el constructor debe aceptar exactamente la configuración declarada;
- `process` siempre devuelve `PipelineContext`;
- `step_type` es la identidad estable usada por `StepSpec`;
- `version` identifica el código ejecutado en `StepExecution`;
- no debe importar RAINSTORM desde un paso distribuido como parte de STORM;
- I/O, estado externo y no determinismo deben documentarse en el plugin.

`PipelineContext` no es serializable por contrato: puede transportar arrays,
tensores y recursos de runtime. `PipelineSpec` sí debe serlo.

## Descubrimiento por reflexión

`StepRegistry.discover(package)` y
`VisualizationRegistry.discover(package)` usan el mismo mecanismo:

1. el consumidor elige un paquete;
2. STORM importa sus módulos recursivamente;
3. enumera subclases concretas de la interfaz;
4. registra cada tipo por su nombre estable.

No es un registro global ni un escaneo automático al importar `storm`. Dos
runtimes pueden tener registros distintos. Los nombres duplicados fallan salvo
que el consumidor use el registro explícito con `replace=True`.

Los protocolos estructurales `Model` y `Metric` no son descubribles de esta
forma. Se registran explícitamente porque cualquier clase puede satisfacerlos
sin herencia y no existe una lista confiable de implementaciones que reflejar.

## `Visualization`

```python
class Visualization(ABC):
    visualization_type: ClassVar[str]

    @abstractmethod
    def render(
        self,
        request: VisualizationRequest,
    ) -> VisualizationResult:
        ...
```

El plugin decide si `content` es una figura, bytes, HTML, SVG, texto o una tabla.
Debe declarar un `media_type` no vacío y no debe guardar archivos como efecto
implícito, salvo que su contrato concreto lo documente.

## Contratos que todavía no existen

No están disponibles para importar:

- `SearchStrategy`;
- `PretrainedModelLoader`;
- `Observer` o `EventBus`;
- `Evaluator` separado.
- `PipelineCachePolicy` y exporters de visualización.

La [guía de pipelines y pasos](../guides/pipelines-and-steps.md) y la
[guía de visualizaciones](../guides/visualizations.md) describen las interfaces
implementadas y los límites de sus extensiones.
# Configuración declarada de componentes

Los componentes registrados en `storm.suite` pueden declarar un esquema JSON
pequeño y serializable. `Catalog.normalize(name, config)` es la operación común
que usan el motor y los formularios de Studio; aplica defaults y devuelve una
configuración nueva sin mutar la recibida.

Se admiten `object`, `number`, `integer`, `string`, `boolean` y `array`, además
de `required`, `enum`, `minimum` y `maximum`. Una propiedad también puede incluir
`description`, un texto que Studio muestra como ayuda y que no cambia la
validación. Los campos no declarados se rechazan. Un descriptor debe tener
configuración de objeto; no se aceptan palabras clave o tipos desconocidos.
`Catalog.validate` se conserva como atajo compatible y devuelve el descriptor
después de validar.

```python
from storm.suite import Catalog, Component

catalog = Catalog()
catalog.register(Component(
    "threshold",
    ThresholdModel,
    ("train", "infer"),
    {
        "type": "object",
        "required": ["threshold"],
        "properties": {
            "threshold": {"type": "number", "minimum": 0, "default": 0.5},
            "mode": {"type": "string", "enum": ["strict", "soft"]},
        },
    },
))

config = catalog.normalize("threshold", {"mode": "strict"})
# {"mode": "strict", "threshold": 0.5}
model = catalog.build("threshold", config)
```

La validación no sustituye las invariantes científicas del adaptador: por
ejemplo, el modelo sigue siendo responsable de verificar que sus targets sean
compatibles. La extensión del esquema a pasos, métricas y conectores está
planificada en [E4](../planning/suite-completion.md).

Los resultados de `storm.suite.execute` incluyen `alignment`, con los IDs de
observación originales y los índices que corresponden a las predicciones
devueltas. Si el dataset no declara `observation_ids`, el motor genera IDs
basados en su posición. Los IDs declarados deben ser strings no vacíos y únicos.

## Preflight de planes `suite` (contrato v1, implementado)

`storm.contracts` no importa Django ni librerías científicas. Exporta:

- `ModelInputContract(version, input_type, preparation, required_steps, granularity, shape)`:
  preparación `external`, `internal` o `unknown`; granularidad `observation` o `session`.
- `ValidationProblem(code, severity, branch, component, field, message)`, con `to_dict()`.
- `PreparationResolver`: callable `(steps, feature_names) -> resolved_steps`, sin mutar entradas.
- `ProgressReporter`: `set_progress_callback(callback | None)`, compatible con `ExecutionObserver`.
- `validate_plan(spec, catalog, data_summary=None)` y `require_valid_plan(...)`;
  este último lanza `PlanValidationError` con `.problems` cuando hay errores.

`Component.input_contract` es opcional y está al final del constructor. `Catalog.describe()`
serializa el contrato sin construir modelos. Componentes antiguos declaran requisitos
científicos desconocidos: advertencia `input.unknown`; preflight no inspecciona builders.
El worker conserva comprobaciones legacy de ejecución como segunda barrera.

```python
from storm.contracts import ModelInputContract, validate_plan
from storm.suite import Component, default_catalog

catalog = default_catalog()
catalog.register(Component("temporal", lambda config: None, (), {},
    input_contract=ModelInputContract(input_type="temporal", preparation="external",
                                     required_steps=("scale",))))
problems = validate_plan({"model": "temporal", "steps": []}, catalog)
assert problems[0].code == "preparation.required_step"
```

El spec de `suite` usa `model`, `config`, `steps`, `branch_models` y `branch_configs`.
Cada rama recibe diagnóstico propio (`root` para la principal, nombre del modelo para
las adicionales). Los errores impiden crear jobs; las advertencias se muestran en
formularios/ejecuciones y permanecen en `result.validation_problems`.
PlanForm, submit, endpoint de ejecución y suite.execute usan la misma validación.
Ante un plan inválido, el envío desde el navegador vuelve a Configurar y muestra
el diagnóstico y la indicación de guardar un plan nuevo; conserva el plan original
y crea cero jobs. Los clientes Python reciben `PlanValidationError`.

`data_summary` es metadata proporcionada por un host confiable: `feature_names`,
`shape` por observación, `input_type`, `full_sessions` y `preparation` opcionales.
No carga datasets ni instancia modelos; puede construir pasos con configuración
resuelta para comprobar sus parámetros. Con schema ausente, advierte y verifica
el registro de pasos. Con transformaciones arbitrarias no infiere shape de salida.
Campos científicos desconocidos requieren comprobación del worker.

Para pasos materializados, `preparation` debe contener `validated=True`, fingerprint
SHA-256 completo y `resolved_steps`. El host verifica la identidad; esos campos no son
una prueba criptográfica por sí solos. Studio recalcula fingerprint de fuente,
configuración resuelta y versiones y lo compara con config e inventario. Una lista
`preapplied_steps` aislada no satisface requisitos en preflight. Planes anteriores se
leen sin migración; para ejecutar inputs ya preparados requieren metadata verificada.

Códigos estables principales: `config.invalid`, `component.unknown`,
`preparation.external_forbidden`, `preparation.required_step`, `preparation.invalid`,
`preparation.resolve`, `input.shape`, `input.type`, `input.full_sessions`; las
advertencias incluyen `input.unknown`, `input.shape_unknown`,
`input.sessions_unknown` y `preparation.schema_unknown`.

Este contrato no implementa datasets por rama, puertos gráficos, controles científicos
ni equivalencia con Tesis_Facu; esas extensiones permanecen propuestas en el
[plan vigente](../planning/suite-completion.md).
## Descriptores reflectables y grafos (2026-10-03)

`storm.descriptors.reflect_schema` describe firmas anotadas y dataclasses sin
instanciar modelos. Los parámetros variádicos, anotaciones no soportadas y
restricciones científicas requieren un esquema declarado. Un esquema Pydantic
exportado puede aportar evidencia; no se convierte automáticamente en un
contrato compatible con el subconjunto JSON Schema de STORM.

`Component` agrega al final `descriptor` y `config_validator`. El validador debe
ser puro y liviano: recibe configuración normalizada, devuelve `None` o levanta
`ValueError`. `Catalog.normalize` valida objetos anidados, elementos de arrays,
defaults independientes y restricciones cruzadas declaradas por el plugin.
`Catalog.describe` exporta descriptores y fingerprints sin construir modelos.

`storm.graphs.validate_graph(graph, descriptors)` verifica nodos registrados,
configuración, puertos tipados, conexiones únicas y ausencia de ciclos.
`ordered_nodes` devuelve un orden topológico. La ejecución y las dimensiones
dinámicas pertenecen al compilador del plugin; un grafo válido estructuralmente
no demuestra equivalencia científica ni habilita una topología arbitraria.

`storm.plans.branch_specs` devuelve copias independientes del plan principal y
sus ramas. `branch_overrides[modelo]` admite `data`, `connector`,
`dataset_revision_id`, `preparation_revision_id` y `steps`. Cada variante elimina
las listas de ramas heredadas. Studio resuelve las referencias de preparación
contra revisiones del mismo estudio y crea variantes marcadas
`execution_variant: true`; las revisiones editables quedan fuera de esa selección.
Las revisiones históricas siguen siendo legibles y no se reescriben.

Las corridas `suite.execute` guardan `configuration.requested`,
`configuration.normalized` y `component_snapshot` con descriptor, contrato,
identidad del builder, SHA256 de su módulo y versiones de dependencias declaradas.
Estas capacidades corresponden al runtime `suite`, no a `Study/RunEngine`.

Los nuevos checkpoints incluyen `component_identity`: descriptor, esquema,
builder y hashes de módulos declarados. Cambiar esa identidad impide continuar
el checkpoint aunque el número de versión sea igual. Las versiones del runtime
siguen registradas separadamente; la compatibilidad de dispositivos la valida
el adapter. Los checkpoints antiguos conservan sus comprobaciones anteriores y
no reciben garantías nuevas de continuidad por esta ampliación.

El método optativo `model.configuration_snapshot()` devuelve un objeto JSON con
la configuración resuelta por el adapter. `suite.execute` lo guarda como
`configuration.resolved`; los plugins antiguos guardan `None`. STORM no extrae
atributos internos por reflexión para inventar esa configuración.
