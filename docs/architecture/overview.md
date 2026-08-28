# Arquitectura

## Objetivo

STORM orquesta estudios y modelos sin conocer el dominio, la representación de
los datos ni la biblioteca que implementa cada algoritmo. El framework define
contratos de composición; las aplicaciones aportan las implementaciones.

## Límites del sistema

STORM es responsable de:

- representar solicitudes mediante datos serializables;
- resolver componentes de runtime mediante registros explícitos;
- preparar datos mediante pipelines configurables y trazables;
- descubrir pasos y visualizaciones dentro de paquetes habilitados;
- ejecutar el ciclo mínimo `fit`/`predict`;
- invocar métricas sin interpretar las predicciones;
- orquestar visualizaciones sin imponer un backend gráfico;
- identificar corridas y ejecuciones;
- persistir y recuperar modelos, outputs y registros;
- mantener lineage entre datos, spec, ejecución y artefactos.

STORM no es responsable de:

- leer formatos de dominio;
- implementar lectores o transformaciones concretas de un dominio;
- implementar algoritmos científicos;
- interpretar embeddings, estados, probabilidades o labels;
- elegir automáticamente la mejor métrica o su dirección;
- importar RAINSTORM, VAME, Keypoint-MoSeq u otra integración.

## Paquetes actuales

| Paquete | Responsabilidad | Dependencias internas permitidas |
|---|---|---|
| `storm.config` | JSON estricto, serialización canónica y fingerprints | ninguna |
| `storm.artifacts` | referencias, protocolo de store y backend local | `storm.config` |
| `storm.models` | contrato de modelo, output y builders | `storm.config` |
| `storm.metrics` | contrato y registro de métricas | tipos de `models`/`runs` |
| `storm.plugins` | descubrimiento recursivo de implementaciones | biblioteca estándar |
| `storm.pipeline` | contexto, specs, pasos, registro, runner y loader compuesto | config, plugins, runs |
| `storm.runs` | datos de runtime, specs, ejecución y resultados | config, artifacts, models, metrics |
| `storm.studies` | orquestación de múltiples corridas y selección | runs y contratos inyectados |
| `storm.visualization` | specs, requests, resultados, registro y manager | config, plugins |
| `storm.testing` | pasos y modelos dummy para ejemplos y smoke tests | pipeline, models |

No se utiliza un paquete genérico llamado `core`: cada módulo debe representar
una capacidad concreta y mantener una dirección de dependencias verificable.

## Dirección de dependencias

```text
artifacts --------> config
models -----------> config
pipeline ---------> config + plugins + runs
visualization ----> config + plugins
runs -------------> artifacts + models + metrics + config
studies ----------> runs

aplicaciones y plugins ------> API pública de storm
storm ----------------------X-> aplicaciones y plugins
```

La regla importante es la última: el framework nunca importa al consumidor.

## Ciclo de vida de un estudio

```text
1. Study recibe StudySpec y dependencias de runtime
2. DataLoader resuelve DataRef -> Dataset
   2.1 opcionalmente PipelineDataLoader ejecuta PipelineSpec
   2.2 cada PipelineStep deja un StepExecution
3. por cada RunSpec:
   3.1 se aplica la semilla estándar y los seeders externos
   3.2 ModelRegistry construye el modelo
   3.3 RunEngine ejecuta fit(inputs, targets)
   3.4 RunEngine ejecuta predict(inputs)
   3.5 MetricRegistry calcula las métricas solicitadas
   3.6 ArtifactStore guarda modelo y output
   3.7 ArtifactStore guarda RunRecord
4. Study devuelve StudyResults
5. el consumidor selecciona o recupera resultados
```

## Modelo de trazabilidad

```text
DataRef
  |- identifier
  |- fingerprint
  `- metadata serializable

RunSpec
  |- run_id lógico
  |- model_type
  |- model_config
  |- seed
  `- fingerprint SHA-256

RunRecord
  |- study_id + run_id
  |- execution_id único
  |- DataRef + spec_fingerprint
  |- timestamps + duración
  |- métricas
  |- model_artifact ----> ArtifactRef ----> manifest + payload
  `- output_artifact ---> ArtifactRef ----> manifest + payload
```

`run_id` identifica una configuración dentro de un estudio. `execution_id`
identifica una ejecución concreta. Ejecutar dos veces el mismo `RunSpec` produce
dos `execution_id`, evitando sobrescrituras.

## Componentes planificados

| Capacidad | Estado | Condición para incorporarla |
|---|---|---|
| pipeline, contexto y pasos | implementado | ampliar mediante policies comprobadas |
| visualización neutral y SVG clásicos | implementado | backends pesados como extras/plugins |
| caché de pipeline | diseño pendiente | policy inyectable y claves versionadas |
| grid/random search | diseño pendiente | separar planificación de ejecución |
| evaluate/infer | diseño pendiente | definir outputs y recuperación sin supuestos de modelo |
| observers/eventos | diseño pendiente | progreso y logging sin acoplar el engine |
| stores remotos | extensión posible hoy | implementar `ArtifactStore` |
| seeders NumPy/PyTorch | extensión posible hoy | pasarlos a `Study(seeders=...)` |

La [guía de pipelines](../guides/pipelines-and-steps.md) explica cómo preparar
datos y portar pasos de RAINSTORM. La
[guía de visualizaciones](../guides/visualizations.md) describe vistas de datos,
outputs y métricas.
