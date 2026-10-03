# Artefactos y recuperación

## Objetivos

La capa de artefactos separa cuatro conceptos:

- el valor de runtime;
- su identidad física en un backend;
- su digest de contenido;
- la metadata de lineage.

`RunEngine` persiste tres artefactos por corrida exitosa:

| Kind | Payload | Uso |
|---|---|---|
| `models` | modelo entrenado | reutilizar `predict()` |
| `outputs` | `ModelOutput` | métricas y análisis posteriores |
| `runs` | `RunRecord` | recuperar identidad, métricas y referencias |

## Backend local

```python
from storm import FileArtifactStore

store = FileArtifactStore("artifacts")
```

Estructura:

```text
artifacts/
└── models/
    └── execution-<uuid>/
        ├── manifest.json
        └── payload.pkl
```

El mismo patrón se aplica a `outputs` y `runs`.

## Manifiesto

Ejemplo simplificado:

```json
{
  "schema_version": 1,
  "artifact_id": "execution-0123...",
  "kind": "models",
  "digest": "sha256:...",
  "uri": "models/execution-0123...",
  "serializer": "pickle",
  "metadata": {
    "study_id": "demo",
    "run_id": "baseline",
    "execution_id": "execution-0123...",
    "data": {
      "identifier": "dataset-v1",
      "fingerprint": "sha256:..."
    },
    "run_spec_fingerprint": "sha256:..."
  }
}
```

## Guardar y cargar manualmente

```python
reference = store.save(
    kind="custom",
    artifact_id="artifact-001",
    value={"value": 10},
    metadata={"producer": "my-application"},
)

loaded = store.load(reference)
resolved = store.resolve(kind="custom", artifact_id="artifact-001")
```

`kind` y `artifact_id` físicos deben usar letras, números, punto, guion o guion
bajo. Los IDs lógicos de estudios y corridas no tienen esta restricción porque
el engine genera un `execution_id` físico.

## Verificaciones de carga

Antes de ejecutar pickle, el backend valida:

- versión de schema;
- serializer esperado;
- coincidencia de kind, ID y URI;
- formato del digest;
- SHA-256 del payload.

Si el `ArtifactRef` entregado no coincide con el manifiesto, la carga falla.

## Seguridad

SHA-256 detecta corrupción o reemplazo respecto del manifiesto; no convierte
pickle en un formato seguro. Una persona que pueda modificar el store puede
reemplazar manifiesto y payload de forma coordinada.

Reglas:

- no cargar artefactos descargados de orígenes no confiables;
- restringir permisos del directorio;
- no usar el backend local como frontera entre tenants;
- preferir serializers seguros para datos y formatos nativos verificados para
  modelos cuando se agregue soporte;
- registrar procedencia y firma criptográfica en backends que lo requieran.

## Recuperar una corrida

```python
from storm import RunResult

result = RunResult.recover(
    store,
    artifact_id="execution-0123...",
)

record = result.record
model = result.load_model()
output = result.load_output()
```

`RunResult` verifica que el payload del kind `runs` sea realmente un
`RunRecord`.

## Implementar otro store

Un backend alternativo implementa `ArtifactStore`:

```python
class ObjectArtifactStore:
    def save(self, *, kind, artifact_id, value, metadata=None):
        ...
        return ArtifactRef(...)

    def load(self, reference):
        ...

    def resolve(self, *, kind, artifact_id):
        ...
```

Debe documentar:

- formato de serialización;
- atomicidad y consistencia;
- comportamiento ante escrituras concurrentes;
- autenticación y autorización;
- validación de integridad;
- política de retención y borrado;
- compatibilidad entre versiones.

STORM no requiere que la URI sea una ruta local; su interpretación pertenece al
backend.

## Limitaciones actuales

- no existe listado o búsqueda de artefactos;
- no existe garbage collection;
- no existen serializers intercambiables;
- los manifests no están firmados;
- `RunRecord` se persiste como objeto Python;
- no existe migración automática de schema.


## Checkpoints y procedencia del runtime `suite`

`RunEngine` persiste corridas de `Study`; `suite.execute` ofrece checkpoints con
los contratos de `storm.learning` y recuperación de modelos guardados. Las APIs
son distintas. La primera entrega de contratos conserva los métodos de checkpoints
existentes: no implementa retención histórica ni continuidad científica completa.
El fallo de retención de `progress-run-epoch-1` está registrado en el
[plan vigente](../planning/suite-completion.md).

Preflight reconoce preparación materializada sólo cuando el host verifica fuente,
config resuelta, versiones y fingerprint contra el inventario. Integridad del artefacto
se vuelve a comprobar al cargarlo; esta metadata no sustituye equivalencia científica.
