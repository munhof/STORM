# Extracción inicial de STORM desde `Tesis_Facu`

Fecha del análisis: 2026-08-26.

## Alcance y conclusión

Se inspeccionaron los dos repositorios, los imports del paquete `rainstorm`, los
tests de arquitectura, persistencia, estudios y métricas, el historial Git de
los componentes candidatos, las licencias y el template inicial de STORM.

La conclusión principal es que `Tesis_Facu` ya contiene varias abstracciones
útiles, pero no existe un subárbol que pueda copiarse completo y llamarse
STORM. La infraestructura general está distribuida entre `rainstorm/core`,
`rainstorm/experiments` y `src/experiment`, y sus límites todavía permiten
dependencias desde el supuesto núcleo hacia RAINSTORM, las variantes de pose,
VAME, Keypoint-MoSeq, pandas, tqdm, NumPy, scikit-learn y PyTorch.

La extracción debe preservar las ideas y los contratos comprobados, pero
reconstruir el núcleo con inversión de dependencias. En particular, STORM no
debe conocer loaders de datos, familias de modelos ni convenciones de métricas
de RAINSTORM.

## 1. Arquitectura encontrada

El flujo principal actual puede resumirse así:

```text
scripts/notebooks de src/experiment
        |
        +-- TrainingStudySpec / PretrainedStudySpec
        |          |
        |          +-- StudyDataSpec (pose, ROI, output_kind, variantes)
        |          +-- StudyOutputSpec (estructura de corridas de la tesis)
        |
        +-- StudyEngine
                   |
                   +-- StudyInputBuilder
                   |      +-- datasets
                   |      +-- PipelineRunner
                   |             +-- StepRegistry
                   |             +-- rainstorm.application.steps
                   |             +-- domains pose/ROI + connectors
                   |
                   +-- Strategy -> ExperimentSetup
                   +-- ModelRegistry -> builders -> modelos/adaptadores
                   +-- RunEngine -> ExperimentContext
                   |      +-- BehaviorModel -> ModelOutput
                   |      +-- Metric -> métricas
                   |
                   +-- ArtifactManager / StudyOutputManager
                          +-- pickle y JSON
                          +-- CSV/pandas y carpetas específicas
```

### Organización por capas reales

- `rainstorm/core` contiene contextos, pasos, runner, registro y artefactos. El
  nombre sugiere independencia, pero `StepRegistry` importa
  `rainstorm.application.steps`, `PipelineRunner` crea directamente un
  `ArtifactManager` y su caché exige `feature_tensor` y `feature_names`.
- `rainstorm/application/steps`, `domains`, `connectors` y `video` implementan
  RAINSTORM: tracking, pose, ROI, video y análisis conductual. `visualization`
  es mixto: contiene mecánicas generales de gráficos, pero también el tema,
  vocabulario y figuras específicas de RAINSTORM.
- `rainstorm/experiments` combina infraestructura reutilizable con dominio. Los
  contratos base, registros y estrategias son generalizables; los datasets,
  inputs, métricas, modelos y loaders preentrenados contienen supuestos de
  VAME, Keypoint-MoSeq, estados y comportamiento.
- `src/experiment` funciona como capa de composición de la tesis. Allí viven
  builders, defaults, scripts, comparaciones y decisiones operativas. Debe
  seguir siendo consumidor de STORM, no parte del framework.

### Acoplamientos que rompen la frontera deseada

1. `core/context/experiment_context.py` importa `LearningTask` desde
   `rainstorm.experiments` y expone `human_labels`.
2. `core/registry.py` autodetecta concretamente
   `rainstorm.application.steps`; el núcleo conoce su aplicación.
3. `core/pipeline/runner.py` contiene política de caché, crea el backend y exige
   nombres de artefacto propios del procesamiento de pose.
4. `study/specs.py` importa `OutputKind`, cuyas alternativas son
   `vame_temporal`, `kpms_keypoints` y `contextual_features`.
5. `StudyInputBuilder` contiene variantes `pose_roi`/`ego_roi`, bodyparts y la
   secuencia concreta de pasos de RAINSTORM.
6. `StudyEngine` importa ese builder, loaders preentrenados concretos,
   generación de checkpoints del dominio, métricas de entrenamiento VAME,
   pandas y tqdm.
7. `RunEngine` depende de `BehaviorModel`, interpreta labels y construye
   metadata con `syllable_labels`.
8. `Metric` usa `CLUSTERING` como dominio por defecto y las implementaciones
   consumen convenciones de syllables/embeddings.
9. `PretrainedModelRegistry.with_default_loaders()` registra VAME y
   Keypoint-MoSeq dentro del supuesto nivel general.
10. La recuperación de estudios persiste objetos con pickle en rutas conocidas,
    pero no ofrece una identidad de contenido ni un manifiesto uniforme para
    datos, modelo, salida y corrida.

### Comparación con el template de STORM

El template contenía solamente `.gitignore`, un README de una línea y una
licencia LGPL 2.1. No había paquete Python, metadata de distribución, tests ni
decisiones arquitectónicas que conservar. Además, la licencia no coincidía con
la decisión solicitada de usar `LGPL-3.0-or-later`; se corrigió en este corte.

## 2. Mapa de componentes

| Componente actual | Responsabilidad actual | Dependencias relevantes | Destino propuesto | Clasificación | Refactorización necesaria |
|---|---|---|---|---|---|
| `BaseContext` | Identidad, artefactos y metadata mutables | biblioteca estándar | contexto de ejecución opcional | Core de STORM | Reemplazar diccionarios implícitos por estado genérico documentado; no imponer datos de pose. |
| `PipelineContext` | Estado compartido del pipeline | tracking, video, model input, features | `PipelineContext` neutral + datos propios en plugins | Core de STORM / RAINSTORM | Implementado con `data`, `targets`, `state`, `artifacts`, metadata y trazas; los campos de pose permanecen en RAINSTORM. |
| `PipelineStep` | Unidad encadenable que muta contexto | `PipelineContext` concreto | interfaz abstracta descubrible | Extensión o interfaz de STORM | Implementado como interfaz nominal para reflection; la transformación concreta permanece fuera. |
| `PipelineRunner` | Ejecución secuencial y caché | `PipelineContext`, `StepRegistry`, `ArtifactManager`, `feature_tensor` | executor de pipeline | Core de STORM | Implementada la ejecución y observabilidad; caché/store quedan como policy posterior; se eliminaron controles de tracking y nombres de features. |
| `DataPipeline` | Fachada `fit/transform` | ninguna real; implementación placeholder | no migrar en su estado actual | Código experimental o legado | Rediseñar solo cuando haya un caso real; hoy no delega en `PipelineRunner`. |
| `StepRegistry` | Descubrimiento y construcción de pasos | reflection, `rainstorm.application.steps` | registro local con discovery acotado | Extensión o interfaz de STORM | Implementado mediante `discover(package)`: no conoce RAINSTORM y no oculta imports fallidos. |
| plots de barras, heatmaps, series, embeddings y radar | Visualización de datos, outputs y métricas | pandas, NumPy, Matplotlib, Seaborn y tema RAINSTORM | interfaz neutral + plugins y SVG clásicos | Extensión de STORM / RAINSTORM | Se extrajo request/result/registry/manager y cuatro SVG neutrales; estilos, ROI, pose y reportes de tesis permanecen fuera. |
| `ArtifactManager` | Memoria global, pickle, metadata y caché MD5 | filesystem, pickle, JSON | `ArtifactStore` + backends | Core de STORM | Separar referencia, manifiesto, serializer y backend; SHA-256; inyección; evitar singleton global. |
| `ExperimentContext` | Convenciones X/y y tarea de aprendizaje | `LearningTask`, human labels | `Dataset`/entrada de corrida neutral | Core de STORM / RAINSTORM | Mantener inputs/targets/metadata en STORM; inferencia de tarea y etiquetas humanas fuera del core. |
| `RunEngine` | Fit, predict, evaluación y resultado | `BehaviorModel`, métricas, syllables, contexto | motor de una corrida | Core de STORM | Depender de protocolos `Model`/`Metric`; separar fases; no interpretar outputs ni labels de dominio. |
| `TrainingStudySpec` | Solicitud de búsqueda y ejecución | `StudyDataSpec`, grids, variantes | `StudySpec` + estrategia | Core de STORM | Separar spec de datos del dominio, plan de corridas y decisión operativa `execute`. |
| `StudyDataSpec` | Split, pose, ROI, ventanas y output de modelos de tesis | `OutputKind`, bodyparts, variantes | spec de entrada RAINSTORM que resuelve un `DataRef` | RAINSTORM | No migrar. Implementar un `RainstormDataLoader`/builder sobre STORM. |
| `StudyOutputSpec` | Convenciones de carpetas y runs previas | rutas y nombres de la tesis | configuración de store/reporting | Core de STORM + RAINSTORM | Extraer root/namespace; dejar variants, tablas y figuras en la aplicación. |
| `ExperimentSetup` | Configuración inmutable de una corrida | serialización común | `RunSpec` | Core de STORM | Casi directo: renombrar, validar JSON estrictamente y usar identidad estable. |
| `ExperimentResult` | Resultado, métricas y lineage parcial | `LearningTask`, `ModelOutput` | `RunRecord` + referencias de artefactos | Core de STORM | Quitar campos específicos; persistir modelo/output por referencia; timestamps y fingerprints uniformes. |
| `StudyEngine` | Orquestación, inputs, búsqueda, checkpoints, outputs | casi todo `experiments`, pandas, tqdm, RAINSTORM | `Study` fino + servicios inyectados | Core de STORM / RAINSTORM | Dividir en planificador, ejecutor, loader y reporting; mover callbacks VAME/KPMS a extensiones. |
| `GridSearchStrategy` | Producto cartesiano de configs | stdlib, `ExperimentSetup` | estrategia genérica | Extensión o interfaz de STORM | Migrable con validación JSON y generación de IDs centralizada. |
| `RandomSearchStrategy` | Muestreo discreto | stdlib | estrategia genérica | Extensión o interfaz de STORM | Corregir semántica de `n_trials` cuando hay duplicados y separar semilla de búsqueda de semilla de modelo. |
| `ModelRegistry` | Construcción y seeding | NumPy y PyTorch opcional | registro explícito + seeders | Core de STORM / extensión | El registro es casi directo; seeding científico debe inyectarse desde adaptadores para no cargar dependencias. |
| `BaseModel`/`BehaviorModel` | Contrato fit/predict | `ModelOutput`; semántica conductual | protocolo `Model`; adaptador RAINSTORM | Extensión o interfaz de STORM / RAINSTORM | Eliminar nombre y docstring conductual; no exigir herencia; normalizar salida en el adaptador. |
| `ModelOutput` | Results, embeddings, probabilidades y reconstrucciones | ninguna pesada | salida mínima + metadata/extensiones tipadas | Core de STORM / extensión | `predictions` neutral en core; embeddings, reconstrucción y transiciones como outputs de extensión, no campos obligatorios universales. |
| `Metric`/`MetricDomain` | Contrato de métricas con dominio clustering por defecto | `ModelOutput` | protocolo escalar o resultado de métrica | Extensión o interfaz de STORM | Sin dominio por defecto; inputs explícitos; registro por nombre. |
| ARI/NMI/silhouette/CH/DB | Métricas matemáticas | NumPy, scikit-learn, labels/embeddings | paquete opcional de métricas | Extensión o interfaz de STORM | Separar cálculos genéricos de vocabulario `behavioral`/`syllable`; declarar extra `sklearn`. |
| Entropía/duración/dead states | Diagnóstico de secuencias de estados | NumPy, `syllable_labels` | extensión RAINSTORM o plugin temporal | RAINSTORM / extensión | Renombrar contratos y no asumir que todo modelo produce estados discretos. |
| `training_metrics.py` | Extrae losses VAME | `VAME TrainingResult` | adaptador VAME | Modelo/adaptador externo | No migrar al core. |
| `StudyInputBuilder` | Construye pipeline de pose/ROI | datasets, pasos, variantes | builder RAINSTORM | RAINSTORM | Mantener completo fuera de STORM; hacer que entregue `Dataset`/`DataRef`. |
| `datasets/*` y model inputs | Splits y estructuras VAME/KPMS/contextuales | pipeline, NumPy, modelos concretos | loaders y DTOs de RAINSTORM/adaptadores | RAINSTORM / Modelo externo | Extraer únicamente la idea de `DataRef`/loader; no las clases concretas. |
| `PretrainedModelRegistry` | Carga modelos persistidos | loaders VAME/KPMS oficiales y nativos | contrato de loader en STORM; registros en plugins | Extensión o interfaz / Modelo externo | Quitar `with_default_loaders()` del core; cada distribución registra sus loaders. |
| `StudyOutputManager`/reporting | Rutas, tablas, figuras y runs previas | pandas, pickle, tema RAINSTORM | store genérico + reporting externo | RAINSTORM / Código experimental | No migrar entero; rescatar lineage y recuperación mediante contratos del store. |
| `src/experiment/builders` y `registry_setup` | Composition root de modelos | VAME/KPMS/RAINSTORM | consumidor de STORM | Modelo/adaptador externo / RAINSTORM | Adaptar builders a `ModelRegistry`; nunca importarlos desde STORM. |
| notebooks, comparadores y defaults | Experimentos concretos de tesis | paths, variantes, visualización | permanecer en aplicación | Código experimental o legado | Reutilizar como pruebas de aceptación, no como módulos del framework. |

## 3. Arquitectura propuesta para STORM

```text
src/storm/
├── artifacts/       # ArtifactRef, ArtifactStore y backends livianos
├── config/          # serialización estricta, JSON canónico y fingerprints
├── metrics/         # protocolo y registro; implementaciones opcionales aparte
├── models/          # protocolo Model, ModelOutput y registro de builders
├── pipeline/        # contexto, specs, steps, reflection, runner y loader
├── plugins/         # descubrimiento recursivo acotado por paquete
├── runs/            # DataRef/Dataset, RunSpec, RunEngine y RunRecord
├── studies/         # StudySpec, Study, StudyResults y selección
├── testing/         # pasos y modelos dummy, no producción
├── visualization/   # contratos, registro, manager y SVG clásicos
├── strategies/      # grid/random y futuros planificadores (corte posterior)
└── reproducibility/ # política de seed y captura de entorno (corte posterior)
```

No se propone un paquete `core`: suele convertirse en un contenedor ambiguo y
facilita ciclos. Cada paquete representa una capacidad pública concreta.

### Dependencias permitidas

```text
config
  ^
  +-- artifacts
  +-- models
  +-- runs <--- metrics (solo contratos de tipos)
        ^
        +-- studies

pipeline ---> config + plugins + runs
visualization ---> config + plugins

RAINSTORM / plugins ---> API pública de STORM
STORM -X-> RAINSTORM / VAME / KPMS / librerías científicas
```

Reglas adicionales:

- los specs contienen datos serializables, nunca factories, modelos o arrays;
- los objetos de runtime se suministran por registros/loaders explícitos;
- el backend de artefactos es una dependencia, no un singleton creado por el
  runner;
- un adaptador traduce la API externa a `ModelOutput`; STORM no inspecciona
  atributos internos del modelo;
- progreso, logging remoto y reporting son observers/plugins, no lógica del
  motor;
- NumPy, pandas, scikit-learn, PyTorch, VAME y Keypoint-MoSeq son extras de las
  extensiones que los necesiten.

## 4. API pública inicial

El primer corte implementa y exporta:

- configuración y datos: `DataRef`, `Dataset`, `RunSpec`, `StudySpec`;
- modelos: `Model`, `ModelOutput`, `ModelRegistry`;
- métricas: `Metric`, `MetricRegistry`;
- preparación: `PipelineContext`, `PipelineStep`, `StepSpec`, `PipelineSpec`,
  `StepRegistry`, `PipelineRunner`, `PipelineDataLoader`, `StepExecution`;
- visualización: `Visualization`, `VisualizationSpec`,
  `VisualizationRequest`, `VisualizationResult`, `VisualizationRegistry` y
  `VisualizationManager`;
- artefactos: `ArtifactRef`, `ArtifactStore`, `FileArtifactStore`;
- ejecución: `RunEngine`, `RunRecord`, `RunResult`;
- estudios: `Study`, `StudyResults`.

La forma concreta inicial es:

```python
study = Study(
    spec,
    data_loader=application_data_loader,
    models=model_registry,
    metrics=metric_registry,
    artifacts=artifact_store,
    seeders=(numpy_seeder, torch_seeder),  # solo si la aplicación los usa
)

results = study.run()
best = results.select("validation_score", maximize=True)
model = best.load_model()
prediction = model.predict(new_data)
```

La inyección explícita evita registros globales y mantiene serializable el
spec. Una futura fachada `StormRuntime` puede reducir argumentos sin ocultar
dependencias ni introducir singletons.

### Rupturas de API justificadas

- `BehaviorModel` pasa a un protocolo estructural `Model`: RAINSTORM puede
  conservar sus clases y registrarlas mediante adaptadores; no necesita heredar
  del framework.
- `ModelOutput.results` pasa conceptualmente a `ModelOutput.predictions`. Un
  adaptador RAINSTORM debe envolver la salida existente y colocar embeddings,
  probabilidades o transiciones en tipos/extensiones o metadata documentada.
- `ExperimentContext` no se migra como bolsa universal. Un loader externo
  resuelve `DataRef` a `Dataset(inputs, targets, metadata)`.
- `ArtifactManager` no es singleton y no se construye dentro de engines. El
  consumidor inyecta un `ArtifactStore`.
- los loaders preentrenados por defecto dejan de existir en el núcleo. El
  paquete que integra cada modelo es responsable de registrarlos.

## 5. Plan incremental de migración

Cada etapa debe comenzar con un test de caracterización en `Tesis_Facu` y un
test de contrato en STORM.

1. **Corte vertical autónomo (implementado).** Specs serializables, registros,
   entrenamiento, métrica, manifiestos SHA-256, selección, recarga de modelo y
   predicción. No cambia RAINSTORM.
2. **Plataforma de datos y visualización (implementada).** Pipeline neutral,
   contexto, specs, trazas, discovery acotado, visualización neutral, SVG
   clásicos y componentes dummy. No incluye lógica de RAINSTORM.
3. **Adaptador de artefactos en RAINSTORM.** Implementar una fachada que escriba
   los resultados actuales mediante `ArtifactStore`, conservando temporalmente
   nombres/rutas existentes. Probar carga de artefactos históricos y nuevos.
4. **Adaptadores de modelo nativos.** Adaptar primero un modelo dummy y luego una
   única familia RAINSTORM a `Model`/`ModelOutput`; no mover la implementación.
   Comparar predicciones y configuración con el flujo actual.
5. **Paridad de fases de corrida.** Agregar specs y métodos explícitos para
   train/evaluate/infer, estado de fallo/cancelación y observers. Reemplazar
   gradualmente `RunEngine` en RAINSTORM con tests de paridad.
6. **Adaptar pipelines RAINSTORM.** Conservar pasos DLC/pose/ROI fuera de STORM,
   migrar sus configs con un adapter y comprobar paridad paso a paso. Diseñar la
   caché como policy inyectable solo después de esa prueba.
7. **Separar specs de estudio.** `StudyDataSpec` se convierte en
   `RainstormStudyDataSpec`; su builder resuelve `DataRef`/`Dataset`. STORM suma
   estrategias grid/random sin conocer variantes o modelos.
8. **Métricas y reporting.** Crear extras opcionales para métricas matemáticas
   generales; conservar métricas conductuales, tablas, plots y selección de la
   tesis en RAINSTORM. Definir direcciones de optimización en metadata de la
   métrica, no en scripts.
9. **Reproducibilidad ampliada.** Capturar versión de STORM/plugin, Python,
   código fuente, entorno y fingerprints de datos/config. Añadir seeders de
   NumPy/PyTorch solo en extensiones.
10. **Recuperación y compatibilidad.** Índice de estudios, reanudación, migradores
   versionados de manifiestos y adaptadores temporales para IDs/rutas históricas.
11. **Retiro de infraestructura duplicada.** Deprecar componentes de
    `rainstorm.experiments` únicamente después de que sus consumidores y tests
    usen STORM. No mantener aliases artificiales sin fecha de retiro.

## 6. Primera implementación

El corte implementado es deliberadamente pequeño pero vertical:

```text
StudySpec + RunSpec + DataRef
        |
        v
Study -> PipelineDataLoader -> PipelineRunner -> Dataset
                    |                 |
                    |                 +-> PipelineStep + StepExecution
        |
        v
RunEngine -> ModelRegistry -> Model.fit/predict
        |               |
        |               +-> ModelOutput
        +-> MetricRegistry
        +-> FileArtifactStore
                +-> modelo + manifiesto
                +-> output + manifiesto
                +-> RunRecord + manifiesto
        |
        v
StudyResults.select -> RunResult.load_model -> predict

VisualizationManager -> VisualizationRegistry -> SVG / backend externo
```

Propiedades verificadas por tests:

- round-trip JSON de specs;
- dos configuraciones comparables y selección por métrica;
- persistencia y recarga del modelo y de la salida;
- IDs lógicos libres de restricciones del filesystem y un `execution_id` único
  por ejecución, sin sobrescribir corridas repetidas;
- lineage con estudio, corrida, datos y fingerprint del spec;
- digest SHA-256 verificado al cargar y rechazo de manifiestos que redirijan el
  payload o contradigan su identidad;
- errores explícitos para registros desconocidos;
- importación de `storm` sin dependencias científicas ni RAINSTORM;
- metadata y texto de licencia `LGPL-3.0-or-later`;
- compatibilidad con `pytest` y `unittest`.
- discovery recursivo sin imports de RAINSTORM codificados;
- round-trip y fingerprint de `PipelineSpec`;
- preparación de `Dataset` conservando targets y lineage;
- visualizaciones de datos, predicciones y métricas como SVG;
- modelos y pasos dummy, más accuracy, MAE y MSE.

No se implementaron búsqueda automática, caché de pipeline, evaluación
separada, inferencia preentrenada, observers ni backends remotos.

## Revisión preliminar de licencia y procedencia

- `Tesis_Facu` declara licencia MIT y el archivo de licencia atribuye copyright
  a Santiago D'hers. El historial de los módulos de experimentación inspeccionados
  muestra principalmente commits de Facundo Munhó. Antes de copiar código
  sustancial, debe confirmarse la autoridad de relicenciamiento y conservarse el
  aviso MIT correspondiente. MIT es compatible con distribuir una obra derivada
  bajo LGPL, pero su aviso no debe eliminarse.
- El rastreo de headers, URLs y avisos en los componentes candidatos no encontró
  fragmentos marcados como copiados de VAME, Keypoint-MoSeq, scikit-learn u otra
  biblioteca. Sí hay integraciones mediante imports y una referencia bibliográfica
  al algoritmo de random search; eso no prueba por sí solo copia de código.
- Los loaders y adaptadores oficiales dependen de bibliotecas externas y deben
  permanecer en paquetes opcionales. Antes de distribuir cada integración hay
  que revisar la licencia de su versión concreta y cualquier código efectivamente
  incorporado, no solo importado.
- Este primer corte se escribió como implementación nueva a partir de contratos
  observados, no copió módulos de `Tesis_Facu`, no incorpora código de VAME/KPMS
  y usa únicamente la biblioteca estándar en runtime.
- `FileArtifactStore` usa pickle para permitir modelos arbitrarios; los
  manifiestos verifican integridad, no seguridad. Solo deben cargarse stores de
  confianza.

Esta revisión es técnica y preliminar, no asesoramiento legal.
