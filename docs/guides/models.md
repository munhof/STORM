# Modelos compatibles

La API de experimentos versionados usa **adaptadores → contexto → pipeline →
entradas de modelo → estudio/corrida → métricas**. Ver el
[contrato compartido, tutoriales y límites](../guides/context-experiments.md).
`Study.from_experiment` y el editor del grafo comparten esa especificación.
Las secciones siguientes que usan `StudySpec`, `RunSpec` o `suite.steps`
describen las APIs legacy preservadas; no implican conversión automática al DAG.


## Contrato mínimo

Un modelo compatible implementa dos métodos:

```python
class MyModel:
    def fit(self, inputs, targets=None):
        ...
        return self

    def predict(self, inputs):
        return ModelOutput(predictions=...)
```

No necesita heredar de `Model`: el contrato es estructural.

## Modelo nativo

```python
from storm import ModelOutput, ModelRegistry


class ThresholdModel:
    def __init__(self, config):
        self.threshold = float(config["threshold"])

    def fit(self, inputs, targets=None):
        return self

    def predict(self, inputs):
        labels = [int(value >= self.threshold) for value in inputs]
        return ModelOutput(
            predictions=labels,
            metadata={"threshold": self.threshold},
        )


models = ModelRegistry()
models.register("threshold", ThresholdModel)
```

Una clase cuyo constructor acepta el diccionario de configuración puede
registrarse directamente.

## Modelos dummy incluidos

`storm.testing` contiene modelos pequeños para smoke tests, ejemplos y tests de
integración. No son algoritmos de producción:

| Nombre de registro | Clase | Comportamiento |
|---|---|---|
| `constant` | `ConstantModel` | repite `config["value"]` |
| `identity` | `IdentityModel` | devuelve los inputs |
| `mean_regressor` | `MeanRegressor` | aprende la media de los targets |

```python
from storm import ModelRegistry
from storm.testing import register_dummy_models

models = ModelRegistry()
register_dummy_models(models)
```

Estos modelos hacen posible probar todo el ciclo de estudios, métricas,
persistencia y recuperación sin instalar una biblioteca científica.

## Builder con configuración tipada

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ExternalConfig:
    width: int
    learning_rate: float


def build_external(config):
    typed = ExternalConfig(
        width=int(config["width"]),
        learning_rate=float(config["learning_rate"]),
    )
    return ExternalModelAdapter(typed)


models.register("external", build_external)
```

La conversión y validación específicas pertenecen al builder, no a STORM.

## Adaptar una biblioteca externa

Supongamos que una biblioteca devuelve un array directamente:

```python
from storm import ModelOutput


class EstimatorAdapter:
    def __init__(self, estimator):
        self.estimator = estimator

    def fit(self, inputs, targets=None):
        if targets is None:
            self.estimator.fit(inputs)
        else:
            self.estimator.fit(inputs, targets)
        return self

    def predict(self, inputs):
        raw = self.estimator.predict(inputs)
        return ModelOutput(
            predictions=raw,
            metadata={"adapter": type(self).__name__},
        )
```

El builder instancia el estimador y devuelve el adapter:

```python
def build_estimator(config):
    estimator = ExternalEstimator(**config)
    return EstimatorAdapter(estimator)
```

## Outputs ricos

El campo `predictions` contiene la salida principal. El corte actual ofrece
`metadata` para información complementaria:

```python
return ModelOutput(
    predictions=labels,
    metadata={
        "probabilities": probabilities,
        "embedding": embedding,
        "class_names": class_names,
    },
)
```

Esta flexibilidad es transitoria. Una integración debe documentar las claves,
tipos y shapes. No se debe asumir que una métrica genérica entiende metadata de
otro plugin.

## Modelos no supervisados

El engine siempre llama:

```python
model.fit(dataset.inputs, dataset.targets)
```

Por eso un modelo no supervisado debe aceptar `targets=None` e ignorarlo:

```python
def fit(self, inputs, targets=None):
    self.backend.fit(inputs)
    return self
```

STORM no clasifica el paradigma del modelo ni impone labels discretos.

## Semillas

No aplique semillas globales dentro de `ModelRegistry`. Registre seeders en el
runtime:

```python
study = Study(
    ...,
    seeders=(seed_numpy, seed_torch, seed_external_backend),
)
```

El builder se ejecuta después de los seeders.

## Persistencia

`FileArtifactStore` serializa la instancia completa con pickle. Para que la
recarga funcione:

- la clase debe estar disponible en el entorno consumidor;
- sus dependencias deben tener versiones compatibles;
- no debe contener handles no serializables;
- el estado aprendido debe formar parte del objeto.

Si la biblioteca usa checkpoints, proyectos o archivos propios, existen dos
opciones:

1. crear un wrapper pickleable que conserve la ruta y recargue bajo demanda;
2. implementar un store/serializer especializado en un corte posterior.

No existe aún una interfaz pública de loader preentrenado. No debe simularse
mediante un `fit()` vacío sin documentar esa decisión en la aplicación.

## Checklist

- [ ] `fit(inputs, targets=None)` funciona con el tipo real de `Dataset`.
- [ ] `predict(inputs)` devuelve `ModelOutput`.
- [ ] el builder solo recibe configuración JSON-compatible.
- [ ] dos builds producen instancias independientes.
- [ ] las semillas se aplican antes del build.
- [ ] modelo y output se pueden persistir y recargar.
- [ ] la integración declara dependencias y licencias opcionales.
- [ ] hay tests de paridad con la API nativa de la biblioteca.

## Declaración de entradas en `suite`

La interfaz mínima anterior corresponde a `Study`/`RunEngine`. En `suite`, las
capacidades registradas determinan métodos como `fit_predict`, `partial_fit` o
`fit_with_checkpoints`; un adapter de inferencia puede declarar sólo `infer`.
`Component.input_contract` declara preparación, pasos, granularidad y shape.
Véase [contratos v1](../api/interfaces.md). Desconocido no significa científicamente compatible.
