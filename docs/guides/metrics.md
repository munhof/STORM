# Métricas

## Contrato

Una métrica compatible implementa:

```python
class MyMetric:
    def evaluate(self, *, dataset, output, model):
        return 0.0
```

Debe devolver un `int` o `float`; `RunEngine` normaliza el resultado a `float`.

## Métricas clásicas incluidas

STORM incluye tres métricas generales sin dependencias externas:

| Nombre de registro | Clase | Requisito |
|---|---|---|
| `accuracy` | `Accuracy` | targets y predicciones comparables |
| `mae` | `MeanAbsoluteError` | targets y predicciones numéricos |
| `mse` | `MeanSquaredError` | targets y predicciones numéricos |

```python
from storm import MetricRegistry
from storm.metrics.classic import register_classic_metrics

metrics = MetricRegistry()
register_classic_metrics(metrics)
```

Las tres validan que existan targets, que las secuencias no estén vacías y que
sus longitudes coincidan. `mae` y `mse` rechazan valores no numéricos.

## Métrica supervisada

```python
class Accuracy:
    def evaluate(self, *, dataset, output, model):
        if dataset.targets is None:
            raise ValueError("Accuracy requires targets.")
        matches = sum(
            predicted == expected
            for predicted, expected in zip(output.predictions, dataset.targets)
        )
        return matches / len(dataset.targets)
```

## Métrica que usa metadata

```python
class MeanConfidence:
    def evaluate(self, *, dataset, output, model):
        probabilities = output.metadata["probabilities"]
        return sum(max(row) for row in probabilities) / len(probabilities)
```

La clave `probabilities` forma parte del contrato de esa integración, no de la
API universal de STORM.

## Registro

```python
from storm import MetricRegistry

metrics = MetricRegistry()
metrics.register("accuracy", Accuracy())
metrics.register("mean_confidence", MeanConfidence())
```

Los nombres deben coincidir con `StudySpec.metrics`:

```python
spec = StudySpec(
    ...,
    metrics=("accuracy", "mean_confidence"),
)
```

## Selección

```python
best = results.select("accuracy", maximize=True)
lowest = results.select("validation_loss", maximize=False)
```

La dirección se declara en el punto de selección. El registro actual no guarda
`higher_is_better`, unidades ni descripción. Una capa de catálogo de métricas
es una extensión futura.

## Varias salidas

Una métrica debe producir un escalar. Para reportar precisión, recall y F1:

```python
metrics.register("precision", Precision())
metrics.register("recall", Recall())
metrics.register("f1", F1())
```

Esto mantiene simple el resultado y hace explícito qué nombres pueden usarse
para seleccionar.

## Dependencias opcionales

Las métricas que necesitan NumPy o scikit-learn deben vivir en el paquete de la
integración o en un extra opcional futuro:

```python
class AdjustedRandIndex:
    def evaluate(self, *, dataset, output, model):
        from sklearn.metrics import adjusted_rand_score

        return adjusted_rand_score(dataset.targets, output.predictions)
```

El import local evita que importar STORM cargue esa biblioteca. La distribución
consumidora debe declarar la dependencia.

## Manejo de casos no computables

El contrato actual no define un valor faltante universal. La métrica debe elegir
una política explícita:

- lanzar `ValueError` si el estudio es inválido;
- devolver `float("nan")` si el reporting de la aplicación lo soporta;
- no solicitar esa métrica para datasets incompatibles.

No se recomienda devolver `{}` ni ocultar errores, porque `RunEngine` espera un
escalar.

## Checklist

- [ ] nombre estable y documentado;
- [ ] resultado escalar;
- [ ] requirements de targets y metadata explícitos;
- [ ] dirección de selección conocida por el consumidor;
- [ ] casos vacíos y degenerados testeados;
- [ ] dependencias científicas fuera del core;
- [ ] tests contra valores conocidos.
