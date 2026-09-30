# STORM

**System for Traceable Orchestration, Reuse and Modeling** es un framework
Python independiente del dominio para definir, ejecutar y recuperar estudios de
modelado trazables.

STORM separa dos tipos de objetos:

- **especificaciones serializables**, que describen datos, corridas, modelos,
  configuraciones, semillas y métricas;
- **componentes de runtime**, que cargan datos, construyen modelos, calculan
  métricas y persisten artefactos.

Esta separación permite guardar una solicitud experimental sin serializar
factories, arrays, conexiones o implementaciones externas dentro de ella.

## Estado del proyecto

STORM está en estado **pre-alpha**. El primer corte vertical implementa:

- `StudySpec`, `RunSpec` y `DataRef` serializables;
- ejecución de entrenamiento mediante `Study` y `RunEngine`;
- interfaces estructurales para modelos, métricas y stores de artefactos;
- registros explícitos de modelos y métricas;
- persistencia local de modelo, output y `RunRecord`;
- manifiestos, fingerprints y verificación SHA-256;
- selección de resultados y recuperación del modelo persistido;
- hooks de semillas sin dependencias científicas obligatorias;
- pipelines de preparación con contexto, specs y trazas;
- descubrimiento por reflexión limitado a paquetes habilitados;
- visualizadores SVG clásicos para datos, outputs y métricas.

El recorrido de `storm.suite`, utilizado por Studio, agrega evaluación separada,
inferencia preentrenada y recuperación de checkpoints; no cambia silenciosamente
las semánticas de la API `Study` anterior. Todavía no hay búsqueda automática de
hiperparámetros, caché de pipeline, ejecución remota ni backends distribuidos.
Consultar el [inventario de faltantes](architecture/suite-gaps.md) y el
[plan de cierre vigente](planning/suite-completion.md).

## Recorrido recomendado

1. [Instalar y ejecutar el ejemplo mínimo](getting-started.md).
2. Entender la [arquitectura](architecture/overview.md) y sus
   [decisiones de diseño](architecture/design.md).
3. Consultar la [API pública](api/public-api.md) y las
   [interfaces](api/interfaces.md).
4. Crear [estudios y corridas](guides/studies-and-runs.md).
5. Integrar [modelos](guides/models.md), [métricas](guides/metrics.md) y
   [artefactos](guides/artifacts.md).
6. Implementar [pipelines y pasos](guides/pipelines-and-steps.md).
7. Agregar [visualizaciones](guides/visualizations.md).
8. Implementar una [integración externa o de dominio](guides/integrations.md).

El documento [Análisis de extracción](architecture/initial-extraction.md)
explica cómo se obtuvo esta frontera a partir de `Tesis_Facu` y qué componentes
deben permanecer en RAINSTORM.

## Configuración visual: STORM Studio

La implementación actual está en la [guía de la suite Django](guides/django-suite.md).
Usa un monorepo Python con templates HTML. Los documentos de diseño siguientes
incluyen propuestas históricas que no representan el stack actual.

La aplicación Django permite registrar y preparar datasets, configurar modelos
del catálogo, ejecutar trabajos persistentes, inspeccionar evidencia y aplicar
modelos guardados a nuevas revisiones compatibles. RAINSTORM conecta sus adapters
científicos como plugin separado; la interfaz y los contratos siguen en evolución.
Las siguientes referencias conservan diseños y prototipos anteriores:

- [Viabilidad, arquitectura y contratos](architecture/visual-studio.md).
- [Guía de marca clara y experiencia de uso](design/studio-brand.md).
- [Plan histórico de Studio](planning/studio-roadmap.md).
- [Componentes actuales, modelos, conectores y asistencia](guides/studio-components.md).
- `studio-visual/` conserva ejemplos y una demo HTML histórica; no es la aplicación
  Django actual. El paquete de visualización reside en `packages/storm-visualization`.

## Flujo mínimo de ejecución

```text
StudySpec
   |
   v
Study --PipelineDataLoader--> PipelineRunner --> Dataset
                   |              |
                   |              +--> PipelineStep + StepExecution
                   |
                   v
               RunEngine --ModelRegistry--> Model.fit() -> Model.predict()
                   |                              |
                   +--MetricRegistry--------------+
                   |
                   +--ArtifactStore--> model + output + RunRecord
                   |
                   v
            StudyResults.select() -> RunResult.load_model() -> predict()
```

## Licencia

STORM se distribuye bajo **GNU LGPL-3.0-or-later**. Las integraciones externas
deben declarar y respetar sus propias dependencias y licencias.
