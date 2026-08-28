# Diseño

## Principios

### Especificaciones como datos

`DataRef`, `RunSpec`, `StudySpec`, `StepSpec`, `PipelineSpec` y
`VisualizationSpec` contienen únicamente valores compatibles con JSON. No
contienen lambdas, clases, conexiones, modelos instanciados ni arrays. Los
componentes ejecutables viven en el runtime y se resuelven por nombre.

Esta decisión permite:

- versionar specs;
- compararlos y calcular fingerprints estables;
- producirlos desde CLI, JSON, YAML o una API;
- reconstruir el runtime en otro proceso;
- validar que una configuración no esconda estado no serializable.

### Inversión de dependencias

`Study` depende de `DataLoader`, `ModelRegistry`, `MetricRegistry` y
`ArtifactStore`. No crea implementaciones concretas salvo un registro vacío de
métricas cuando el estudio no solicita ninguna.

El consumidor decide:

- de dónde salen los datos;
- qué significa cada `model_type`;
- qué métricas existen;
- dónde persiste los artefactos;
- qué librerías reciben la semilla.

### Protocolos estructurales e interfaces descubribles

Los modelos y métricas no necesitan heredar de una clase base. Un objeto es
compatible si implementa la firma requerida. Esto reduce acoplamiento y hace
que envolver bibliotecas externas sea local.

Los pasos y visualizaciones sí heredan de `PipelineStep` y `Visualization`.
Esa relación nominal permite encontrarlos mediante reflexión. El consumidor
selecciona un paquete con `registry.discover(...)`; importar STORM no escanea el
entorno ni crea un registro global.

### Composición sobre herencia

Las capacidades se combinan mediante objetos pequeños:

```text
Study = spec + data_loader + registries + artifact_store + seeders

PipelineDataLoader = data_loader + PipelineRunner

PipelineRunner = PipelineSpec + StepRegistry

VisualizationManager = VisualizationSpec + VisualizationRegistry
```

Una aplicación puede reemplazar un solo componente sin crear una jerarquía de
subclases de `StudyEngine`.

### Núcleo liviano

El runtime no importa NumPy, pandas, scikit-learn, PyTorch ni herramientas del
dominio. Esas dependencias pertenecen al paquete que implementa el modelo,
loader, métrica o seeder.

## Separación entre identidad lógica y física

- `study_id` identifica el estudio para humanos y sistemas externos.
- `run_id` identifica una alternativa planificada dentro del estudio.
- `execution_id` identifica un intento concreto y es seguro para el backend
  local.
- `ArtifactRef.digest` identifica el contenido serializado.
- `ArtifactRef.uri` localiza el contenido dentro del store.

Los IDs lógicos pueden contener espacios o separadores; no se utilizan como
rutas del filesystem.

## Persistencia

`ArtifactStore` separa el engine del backend. `FileArtifactStore` implementa el
primer backend mediante un directorio por artefacto:

```text
artifact-root/
├── models/<execution-id>/
│   ├── manifest.json
│   └── payload.pkl
├── outputs/<execution-id>/
│   ├── manifest.json
│   └── payload.pkl
└── runs/<execution-id>/
    ├── manifest.json
    └── payload.pkl
```

La escritura usa archivos temporales y `os.replace`. La carga valida schema,
serializer, identidad, URI y digest SHA-256 antes de deserializar.

El digest verifica integridad, no confianza. Pickle puede ejecutar código al
cargar; el store local solo debe utilizarse con artefactos confiables.

## Reproducibilidad

Cada `RunSpec` declara una semilla. `RunEngine` aplica `random.seed()` y luego
invoca los `Seeder` entregados por la aplicación. Por ejemplo:

```python
def seed_numpy(seed: int) -> None:
    import numpy as np

    np.random.seed(seed)


def seed_torch(seed: int) -> None:
    import torch

    torch.manual_seed(seed)
```

```python
study = Study(..., seeders=(seed_numpy, seed_torch))
```

STORM no importa esas bibliotecas ni promete determinismo que la integración no
pueda garantizar. El adaptador debe documentar opciones adicionales del backend.

## Política de errores

El corte actual falla de forma explícita:

- nombres duplicados en registros: `ValueError`;
- modelo o métrica desconocidos: `KeyError`;
- builder que no produce `fit`/`predict`: `TypeError`;
- `DataLoader` que no devuelve `Dataset`: `TypeError`;
- paso que no devuelve `PipelineContext`: `TypeError`;
- paquete de plugins que no puede importarse: `PluginDiscoveryError`;
- visualización que no devuelve `VisualizationResult`: `TypeError`;
- `predict()` que no devuelve `ModelOutput`: `TypeError`;
- artefacto ausente: `FileNotFoundError`;
- manifiesto o digest inválido: `ValueError`.

Todavía no existe un `RunRecord` de fallo. Agregar estados
`running/succeeded/failed/cancelled` requiere un corte específico para que la
persistencia de errores sea consistente.

## Compatibilidad y versionado futuro

Las estructuras persistidas deberán adquirir una versión de schema antes de
garantizar compatibilidad entre releases. El manifiesto local ya declara
`schema_version = 1`, pero `RunRecord` todavía se persiste como objeto Python.

Antes de estabilizar la API se deberá:

1. definir serialización data-only de `RunRecord`;
2. versionar formatos y migradores;
3. separar serializers del backend;
4. agregar tests de compatibilidad entre versiones;
5. documentar una política de deprecación.

## Decisiones deliberadas y trabajo postergado

- No hay registro global: evita estado oculto y contaminación entre tests.
- El discovery es opt-in y limitado a un paquete: las clases habilitadas se
  registran por reflexión, sin cargar plugins pesados al importar STORM.
- No hay dirección embebida en `Metric`: `maximize` se decide al seleccionar.
- No hay caché implícita en `PipelineRunner`: se agregará como policy inyectable.
- No hay dependencia gráfica: los visualizadores clásicos generan SVG y los
  backends externos viven en plugins opcionales.
- No hay loaders preentrenados por defecto: pertenecen a cada integración.
- No hay dependencia de dataframe para resumir resultados.

La motivación y clasificación original de componentes se encuentra en el
[análisis de extracción](initial-extraction.md).
