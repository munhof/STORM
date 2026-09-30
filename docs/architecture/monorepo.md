# Arquitectura vigente del monorepo

La suite usa Python y Django. No implementa aplicaciones de dominio. Esta
decisión reemplaza la propuesta inicial React/FastAPI.

| Paquete | Distribución / imports | Responsabilidad |
|---|---|---|
| `packages/storm-engine` | `storm-traceable` / `storm` | contratos, pipeline, ejecución, registros y artefactos |
| `packages/storm-studio` | `storm-studio` / `storm_studio` | Django, formularios, revisiones SQLite y worker local |
| `packages/storm-visualization` | `storm-studio-visual` / `storm_studio_visual` | renderer temporal opcional |

El root es un workspace de uv sin paquete distribuible propio. Cada miembro se
puede construir con `uv build --package <nombre>`. Los imports existentes se
mantienen. El contenido previo de `src/storm` se trasladó mecánicamente al
paquete del motor; `storm.visualization` sigue disponible para compatibilidad.

## Flujo de aplicación

```text
Template Django / formulario
    → Revision(kind=plan) inmutable
    → aprobación → Job pendiente
    → worker → subproceso → storm.suite.execute
    → PipelineRunner + modelo + métricas + ArtifactStore
    → Job completado → evidencia / inferencia / evaluación / reporte
    → lote de revisión → decisión → nuevo plan → nueva aprobación
```

El engine no importa Django. La web y los comandos invocan servicios Python.
La API `Study` anterior se conserva; el recorrido nuevo usa `storm.suite` para
particiones separadas. Su servicio `evaluate` recupera un modelo sin entrenarlo.

## Versiones y aislamiento

`Revision` identifica planes, inferencias, evaluaciones, propuestas, decisiones
y snapshots. Las modificaciones se representan creando sucesores. `Job`
identifica cada intento antes de iniciarse y enlaza reintentos mediante
`previous`. Los resultados incluyen especificación, datos resueltos, semillas,
fingerprints, referencias verificables y trazas de preparación.

SQLite guarda metadatos. `FileArtifactStore` guarda los objetos científicos en
el workspace local. Los procesos científicos se ejecutan fuera de HTTP. No hay
dependencia de Redis ni de un servicio cloud.

## Plugins

Una aplicación externa instala su paquete y registra componentes en el catálogo
desde su módulo de composición. La suite no busca ni importa paquetes de
dominio implícitamente. Los contratos existentes `PipelineStep`, `Visualization`
y métricas siguen disponibles; el catálogo de modelos agrega capacidades y
descriptores sin obligar a un adapter de inferencia a implementar `fit`.

La guía [Django Suite](../guides/django-suite.md) detalla comandos, contratos
implementados y capacidades del plan aún pendientes. La validación actual
comprueba Linux, componentes sintéticos y un plugin externo de ejemplo.
