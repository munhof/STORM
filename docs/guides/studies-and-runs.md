# Estudios y corridas

La API de experimentos versionados usa **adaptadores → contexto → pipeline →
entradas de modelo → estudio/corrida → métricas**. Ver el
[contrato compartido, tutoriales y límites](../guides/context-experiments.md).
`Study.from_experiment` y el editor del grafo comparten esa especificación.
Las secciones siguientes que usan `StudySpec`, `RunSpec` o `suite.steps`
describen las APIs legacy preservadas; no implican conversión automática al DAG.


## Conceptos

- Un `DataRef` identifica la versión lógica de los datos.
- Un `RunSpec` describe una configuración de modelo y semilla.
- Un `StudySpec` reúne corridas comparables sobre el mismo `DataRef`.
- Una ejecución produce un `RunRecord` y tres artefactos: modelo, output y run.

## Crear la referencia de datos

```python
from storm import DataRef

data = DataRef(
    identifier="customer-churn/train",
    fingerprint="sha256:4ad0...",
    metadata={"schema": "churn-v2", "split": "train"},
)
```

STORM no calcula este fingerprint automáticamente porque desconoce si los datos
provienen de un archivo, una tabla, un object store o una API. La aplicación
debe definir una política reproducible, por ejemplo el hash de un manifiesto de
archivos ordenado.

## Crear corridas

```python
from storm import RunSpec

runs = (
    RunSpec(
        run_id="small",
        model_type="my_model",
        model_config={"width": 16, "dropout": 0.1},
        seed=10,
        tags={"family": "baseline"},
    ),
    RunSpec(
        run_id="large",
        model_type="my_model",
        model_config={"width": 64, "dropout": 0.1},
        seed=10,
        tags={"family": "candidate"},
    ),
)
```

`model_config` y `tags` deben ser compatibles con JSON estricto. Objetos como
factories, arrays o handles de archivos producen `TypeError` al serializarse.

## Crear y serializar el estudio

```python
import json

from storm import StudySpec

spec = StudySpec(
    study_id="churn-width-study",
    data=data,
    runs=runs,
    metrics=("validation_accuracy", "validation_loss"),
)

payload = spec.to_dict()
text = json.dumps(payload, indent=2)
recovered_spec = StudySpec.from_dict(json.loads(text))
```

Los nombres de métricas son referencias de runtime; la implementación concreta
no se serializa dentro del spec.

## Resolver datos

```python
from storm import DataRef, Dataset


def load_churn_data(reference: DataRef) -> Dataset:
    inputs, targets = application_dataset_reader(reference.identifier)
    return Dataset(
        inputs=inputs,
        targets=targets,
        metadata={"data_fingerprint": reference.fingerprint},
    )
```

La preparación que deba ser idéntica para todas las corridas pertenece a este
loader o a una composición de pasos de la aplicación.

## Ejecutar

```python
from storm import FileArtifactStore, Study

study = Study(
    spec,
    data_loader=load_churn_data,
    models=model_registry,
    metrics=metric_registry,
    artifacts=FileArtifactStore("artifacts/churn-study"),
    seeders=(seed_numpy,),
)

results = study.run()
```

La ejecución actual es secuencial. Si una corrida falla, la excepción se
propaga y no se ejecutan las corridas restantes.

## Inspeccionar y seleccionar

```python
for result in results:
    print(result.run_id, result.execution_id, result.metrics)

best_accuracy = results.select("validation_accuracy", maximize=True)
lowest_loss = results.select("validation_loss", maximize=False)
```

STORM no combina métricas ni decide qué criterio es científicamente válido.

## Reutilizar el modelo

```python
model = best_accuracy.load_model()
prediction = model.predict(new_inputs)
```

El modelo conserva el contrato STORM y devuelve `ModelOutput`.

## Recuperar una ejecución en otro momento

```python
from storm import FileArtifactStore, RunResult

store = FileArtifactStore("artifacts/churn-study")
result = RunResult.recover(
    store,
    artifact_id="execution-0123456789abcdef...",
)

print(result.record)
model = result.load_model()
output = result.load_output()
```

La aplicación debe conservar o indexar el `execution_id`. El corte actual no
implementa todavía un catálogo de estudios ni una operación `list_runs()`.

## Limitaciones actuales

- `Study.run()` solo ejecuta entrenamiento y predicción sobre el dataset cargado.
- No hay split de train/evaluate administrado por STORM.
- No hay reanudación ni estado de fallo persistido.
- No hay paralelismo ni scheduler.
- No hay estrategias grid/random incorporadas.
- `StudySpec` no tiene aún versión de schema explícita.


## Planes del runtime `suite`

`StudySpec`/`RunSpec` pertenecen a `Study`/`RunEngine`; el plan de Studio pertenece
a `suite`. `storm.contracts.validate_plan` valida configuración y preparación por
rama antes de encolar. La preparación/dataset propios por rama y la separación del
plan activo de variantes son trabajo pendiente del [plan vigente](../planning/suite-completion.md).
La primera entrega no modifica la revisión 71 ni corridas 72–75 del estudio diagnosticado.
