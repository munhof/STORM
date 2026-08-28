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
