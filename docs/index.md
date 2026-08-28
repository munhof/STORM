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

Todavía no implementa búsqueda automática de hiperparámetros, caché de
pipeline, evaluación separada, inferencia preentrenada, ejecución remota ni
backends distribuidos.

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

## Flujo mínimo

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
