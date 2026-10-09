# Estudios construidos sobre contextos y pipelines

Un adaptador de datos carga fuentes y describe identidad, unidades y tiempo.
Un contexto transporta datos, objetivos, metadatos, estado y artefactos. Un
pipeline transforma esos contenidos. Un adaptador de modelo selecciona sus
entradas; el modelo entrena e infiere. Un estudio combina grafo, variantes,
parámetros, semillas y métricas. Una corrida congela una configuración y conserva
su evidencia. Una métrica declara tarea, versión, dirección y partición.

## Contrato Python, documento y Studio

```python
import json
from storm import Study
from storm.experiment_examples import tabular_graph
from storm.experiments import scientific_fingerprint

spec = {"graph": tabular_graph(), "seeds": [156]}
serialized = json.dumps(spec)
recovered = json.loads(serialized)
assert scientific_fingerprint(spec["graph"]) == scientific_fingerprint(recovered["graph"])
runs = Study.from_experiment(recovered).run()
assert runs[0]["result"].status == "complete"
```

En Studio, abrir **Grafo del experimento** y **Ejemplo tabular** produce ese
recorrido. Cada nodo tiene ID propio. Las conexiones del inspector enlazan
puertos explícitos; añadir un modelo deja sus entradas sin conectar. Seleccionar
salidas mediante `adapter.select`, incluyendo `targets` o `none`. Guardar crea
una revisión; exportar JSON conserva también la vista, mientras exportar Python
produce un archivo ejecutable que usa el mismo motor. Importar y guardar crea
una revisión sucesora. Los modelos oficiales conservan su preparación interna;
su arquitectura es configuración del modelo, no topología del experimento.

`PipelineContext` conserva todos sus campos anteriores. `schema` contiene
`ContentDescriptor(dtype, dimensions, units, granularity, identity, time)`.
Describe valores sin serializar arrays, archivos abiertos ni modelos en el plan.
Los datos grandes pertenecen a un `DataAdapter` o artefacto del worker; las listas
inline están destinadas a ejemplos pequeños. `DataAdapter` y `ModelAdapter`
tienen registros separados en `Catalog`. Un adaptador de datos con varias
particiones declara `outputs=("train", "validation")` y devuelve un contexto por
puerto; su callback opcional `preview` conserva ese mismo contrato. `ModelAdapter` ofrece
`prepare_training`, `fit`, `prepare_inference` y `predict`, con puentes para
`bind_context`, `bind_data` y `predict_with_context`.

Las operaciones registradas con `NodeOperation` declaran versión, configuración,
puertos, `reads` y `writes`. Las declaraciones científicas son responsabilidad
del plugin; STORM no las infiere a partir de una firma Python. Los puertos del
editor de arquitecturas y del experimento comparten `validate_graph`.

## Ejecución y límites del contrato

`ExperimentExecutor` valida antes de cargar fuentes y ejecuta un DAG en orden
topológico determinista. Copia cada salida antes de entregarla a un consumidor.
Recursos que no admitan copia requieren `NodeOperation.fork`; el plugin debe
proveer una copia aislada. Fallos bloquean descendientes; hojas independientes
pueden completar. El resultado distingue `complete`, `partial` y `failed`.

El fingerprint científico incluye configuración, conexiones y versiones
resueltas; ignora `visual` y el orden de enumeración de nodos y conexiones.
Los manifiestos guardan configuración solicitada/resuelta y procedencia por
nodo. Studio persiste salidas completas en `experiment_outputs`, con digest,
y guarda sólo referencias y manifiestos en la base. No se implementa aún una
caché automática de DAG: no confundir identidad de configuración con resultados
reutilizados ni con verificación de todos los archivos externos.

El mismo scheduler ejecuta `suite.execute` y `RunEngine.train` mediante nodos
atómicos `suite.partitioned.legacy` y `study.same_dataset.legacy`. Sus ciclos
internos y políticas de métricas siguen siendo distintos por compatibilidad.
Esto NO convierte automáticamente un plan antiguo en un grafo granular.
`Study.from_experiment` es la entrada nueva; `Study(StudySpec(...))` conserva la
evaluación histórica sobre el dataset cargado. La migración granular es explícita.

## Tutorial tabular: dos variantes del mismo modelo

Ejecutar `PYTHONPATH=packages/storm-engine/src .venv/bin/python examples/context_study.py`.
El ejemplo tiene 2 filas de entrenamiento, 1 de validación y 1 reservada de test.
`transform.center` ajusta la media sólo en train y aplica el estado a validación.
Los nodos de adaptación declaran `data` y `targets` antes del modelo.
Las variantes `low` y `high` ejecutan el mismo `constant`, con valores 2 y 9;
cada una conserva su `run_id` y evidencia. MSE evalúa sólo validación.

En Studio:

1. Cargar el ejemplo. Cambiar el tipo de modelo creando `model.constant` y conectar
   explícitamente `train_inputs.context` y `valid_inputs.context`; conectar su
   salida con la métrica y eliminar el modelo anterior.
2. Crear variantes con IDs propios y parámetros fijos `model_constant.value`.
   Para un barrido, elegir ese parámetro y escribir valores separados por comas.
3. Escribir semillas. **Ver corridas** muestra la lista exacta y su cantidad.
   Todo el lote debe ser válido antes de **Encolar lote**.
4. Guardar, reabrir y ejecutar mediante el worker existente. Recargar para ver
   métricas, estado por nodo y errores en **Ejecuciones y evidencia**.
5. Congelar un modelo evaluado. La acción **Evaluar test reservado** crea otro
   trabajo; no entrena ni modifica el modelo seleccionado.

El replay de test está implementado para fuentes inline y las operaciones
`scale`, `center`, `windows` y `adapter.select`. Otros plugins requieren un
adaptador de replay; se rechazan sin refit implícito. No hay ranking automático
entre métricas/tareas diferentes. Los grupos conservan tarea `clustering`.

## Dos fuentes con frecuencias distintas

Ejecutar `examples/multisource_temporal.py` con el mismo `PYTHONPATH`. La fuente
pose a 2 Hz es la referencia; el sensor a 1 Hz se interpola linealmente, con
tolerancia explícita de 1 segundo. `state.aligned` conserva los valores,
`state.alignment_mask` los aciertos y `state.alignment_parents` las identidades
originales. Los valores de referencia permanecen disponibles en `data`.

La unión por identidad usa `(sesión, partición, ID)`. La temporal exige mismo
reloj, unidad, origen y alcance. `exact` es el valor inicial; `previous`, `nearest`
y `linear` necesitan tolerancia. No extrapolan; empates eligen el anterior.
Duplicados ambiguos fallan. Relojes distintos necesitan una transformación
explícita externa: no hay estimación automática de offsets. Las ventanas exponen
`window_parents`, preservan objetivos y respetan sesión/partición.

## Previsualización y escritorio

El inspector muestra salidas declaradas, efectos y diagnósticos del nodo/campo.
La previsualización se solicita bajo demanda, compara entradas/salidas y expone
identidades, unidades, máscaras y correspondencias cuando están disponibles.
Las fuentes inline admiten hasta 2000 observaciones. Los adaptadores externos
declaran `NodeOperation.preview`: el executor lo usa en lugar del cargador completo.
RAINSTORM muestra una sesión por partición y hasta 256 frames; `preview_scope`
conserva la etiqueta `sample_only` después de preparar. Nunca ejecuta modelos.
Configuración válida no equivale a dataset validado.

El canvas admite conexiones desde selects accesibles, arrastre, flechas de teclado,
duplicado, borrado, deshacer/rehacer, zoom y desplazamiento interno. Las pruebas
nuevas cubren exclusivamente 1920×1080 y 2560×1440. No se declara aceptación móvil.

## Tutorial RAINSTORM: sesiones, pose temporal y VAME

Cargar el plugin con `STORM_PLUGINS=rainstorm_thesis.plugin`. El catálogo incorpora
`data.pose_sessions`, `pose.prepare_sessions` y `pose.sample_windows` sin importar
NumPy, pandas ni PyTorch. El worker necesita las dependencias científicas.

1. Crear un manifiesto versión `1`, con sesiones `{id, partition, path, sha256}`.
   Las identidades deben ser únicas entre particiones. La fuente comprueba hashes
   de train/validation; conserva test en el manifiesto sin abrir sus archivos.
2. Añadir `data.pose_sessions` y configurar ruta y hash del manifiesto.
3. Añadir dos `pose.prepare_sessions`, conectados respectivamente a `train` y
   `validation`. Declarar puntos corporales, escala X/Y y altura para invertir Y.
   Un `calibration_ref` con ruta/hash de ROI verifica además esos valores.
4. La preparación procesa cada sesión completa: confianza 0,6 → velocidad
   120 cm/s → filtro espacial 4/12 cm, máximo 3 conexiones lejanas → PCHIP →
   mediana 5 → gaussiano sigma 0,6 → centro body → orientación nose-body a 90° →
   ventanas -9…10. No cruza sesiones, particiones ni huecos de frames.
   Su reloj declara unidad `frame`, origen `source_frame_id` y alcance `session`;
   sincronizar con una fuente en segundos requiere una transformación explícita.
5. Para aceptación, conectar `pose.sample_windows` después de preparar train,
   con 256 ventanas y semilla 156. No muestrear antes de validar las sesiones.
6. Añadir dos `adapter.select`, con `data=data` y `targets=none`. Conectarlos a
   `model.vame_native`, manteniendo separados train y validation. El modelo
   comprueba su requisito `pose.temporal_windows` antes de entrenar.
7. Guardar y ejecutar. Las salidas retienen máscaras, auditoría por sesión,
   referencias a arrays preparados con hash, IDs de ventanas y sus padres.
   Las ventanas se cargan desde archivos de sólo lectura; no se serializan en el plan.

`pose.prepare_sessions` es una receta compuesta explícita, no un editor individual
para cada filtro. Las referencias de modelo oficial requieren otro contexto y su
preparación interna; no conectar automáticamente ventanas nativas a esa integración.
La arquitectura interna VAME se abre en **Arquitectura interna del modelo**
dentro del inspector. Su editor conserva el grafo interno en la configuración
del nodo; no modifica las conexiones del experimento.

Desde RAINSTORM, los scripts reproducen el caso real del estudio 8:

```bash
export PYTHONPATH=packages/rainstorm-thesis/src:packages/rainstorm-supervised/src:src:../STORM-System-for-Traceable-Orchestration-Reuse-and-Modeling/packages/storm-engine/src
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
RUNTIME=envs/vame_worker/.venv/bin/python
WORKSPACE=../STORM-System-for-Traceable-Orchestration-Reuse-and-Modeling/.storm
OUTPUT=$WORKSPACE/rainstorm/study8-context-v2
$RUNTIME scripts/prepare_study8_context.py --workspace "$WORKSPACE" --roi ../rainstorm_analysis_simon/ROIs.json --output "$OUTPUT"
$RUNTIME scripts/accept_study8_context.py --prepared-root "$OUTPUT" --artifact-root "$WORKSPACE/artifacts"
```

La auditoría verificó las 64 sesiones train/validation; la preparación completa
pasó para las 62 train y 2 validation. La corrida CPU de una época usó 256 ventanas
train con semilla 156 e infirió 37.743 ventanas de validación. Las 22 sesiones test
permanecen reservadas. Las referencias de artefactos y parámetros se guardan en
`preparation.json` y `acceptance.json` dentro del directorio de salida.

La escala 15,02 px/cm y altura 1052 provienen del ROI histórico. La rama nativa
histórica no cargaba explícitamente ese ROI: esta receta es una sucesora versionada,
no una afirmación de equivalencia. Se compararon las máscaras espaciales y coordenadas reconstruidas de las 64
sesiones con las funciones recuperadas: máscaras iguales y valores dentro de
atol=1e-6, rtol=1e-6 bajo esta misma calibración. Esa comparación de etapas no
demuestra equivalencia de la configuración histórica del experimento. La pérdida KL
nativa tampoco acredita la programación histórica de warmup. CPU y una época no
prueban estabilidad GPU ni calidad de un entrenamiento completo.

### Bloques de extracción de características

En **Preparación → Grafo del contexto**, el catálogo distingue las transformaciones
 de la **Extracción de características**. Añadí un bloque y conectá el círculo de
salida de una fuente o transformación con su entrada. El inspector permite la
misma operación mediante un selector. Escape cancela una conexión pendiente.
Las conexiones incompatibles y los ciclos se rechazan; las bifurcaciones conservan
el contexto original mediante el aislamiento del ejecutor.

- **Distancia entre puntos** (`features.distance`): recibe un vector por observación.
  Elegí los índices de columnas de cada punto, desde cero, en orden x/y o x/y/z.
  Por ejemplo, `[0, 1]` y `[2, 3]` convierten `[0, 0, 3, 4]` en `[5]`.
  Ambos puntos deben usar el mismo sistema y unidades de coordenadas.
- **Estadísticas por ventana** (`features.window_statistics`): recibe ventanas
  `[tiempo, características]`, por ejemplo de `transform.windows`, y produce
  media, desviación estándar poblacional, mínimo o máximo por característica.
  Cada ventana se calcula por separado; no ajusta estadísticas entre particiones.

Estos bloques sustituyen `context.data` por las características extraídas,
actualizan sus nombres y contrato y conservan objetivos, identidad, estado,
artefactos y procedencia. Podés bifurcar la fuente para conservar una rama de pose
y otra de características, y elegir una de ellas en `adapter.select` para el modelo.
Los valores ausentes o no finitos se rechazan: no se rellenan automáticamente.
Las unidades se heredan; no hay conversión ni calibración implícita.
Usá **Previsualizar nodo** para revisar una muestra antes de ejecutar.

### Recetas con piezas encastrables

El editor de recetas (`/studies/<id>/prepare/?editor=recipes`) permite construir
una cadena secuencial sin modificar el grafo del experimento. La biblioteca incluye
procesamiento y extracción de características. Arrastrá una pieza a un encastre,
o pulsá **Insertar aquí** y elegí una pieza. Seleccioná un bloque para editarlo en
el inspector; podés reordenarlo arrastrándolo o con **Subir/Bajar**, y quitarlo.

**Previsualizar sobre el dataset** ejecuta una muestra con el orden y parámetros
actuales. **Guardar receta** conserva una revisión nueva, reutilizable por los
planes anteriores. Los extractores de distancia y estadísticas por ventana usan
las mismas funciones que sus bloques del grafo. La forma de los encastres indica
el orden; la compatibilidad real con los datos se comprueba al previsualizar.

## Recuperar modelos y comparar ramas

En el catálogo del experimento, agregá **Recuperar modelo entrenado o bundle** (`model.saved`)
y seleccioná una corrida completada de cualquiera de los estudios locales.
El inspector permite:

- **Agregar rama de inferencia**: conecta el modelo recuperado con
  `model.infer_saved`. Cuando existe un grafo de origen también agrega
  `adapter.saved_preparation`. Conectá a este adaptador el contexto de validación
  anterior a la preparación original.
- **Copiar parámetros para entrenar**: agrega un modelo nuevo con los parámetros
  resueltos de la corrida elegida. Seleccioná explícitamente sus entradas de
  entrenamiento y validación. Esta acción no transfiere pesos.

La preparación recuperada reproduce selección de entradas, escala y ventanas;
el centrado reutiliza exclusivamente la estadística guardada del entrenamiento
original. Los pasos de plugins que no tienen un adaptador de recuperación se
rechazan, sin volver a ajustarlos. Las referencias de artefactos se verifican por
hash y una versión de modelo distinta requiere migración explícita.

Los plugins que solo declaran inferencia aparecen como nodos con entrada
`validation`; no reciben una llamada a `fit`. Los bundles supervisados cuya
población de entrenamiento figura como `unknown` producen evidencia exploratoria.
El test reservado sigue requiriendo la acción independiente de selección congelada.

En **Resultados → Comparar corridas**, seleccioná ejecuciones de cualquier estudio.
Una corrida puede contener varias salidas de modelo. Las predicciones se alinean
por sesión e identidad; no por número de fila. Una comparación de métricas exige
cohorte válida, tarea, partición, fuentes verificables, objetivos y semántica de
categorías compatibles, además de igual definición de métrica. Las ramas pueden
usar características diferentes. Los manifiestos antiguos sin evidencia portable
se muestran como inspección separada.

### Límites de esta entrega

La recuperación de preparación de plugins arbitrarios y de recetas legacy, la
importación de bundles externos, la continuación por checkpoint y la actualización
incremental desde el grafo siguen pendientes. Las operaciones legacy existentes
no equivalen a soporte de esas acciones en el editor de grafos. La preparación
supervisada de seis puntos requiere un contexto de frames con coordenadas y
metadatos alineados; no acepta referencias de sesiones ni ventanas VAME ya
preparadas como sustituto de esos frames. No se certifica equivalencia histórica
de los modelos supervisados sin su procedencia científica.

Cuando una corrida recuperada conserva coordenadas pero no nombres de variables,
la evidencia visual consulta la declaración de puntos corporales del contexto
original guardado. Solo la utiliza si el orden declarado es único y la cantidad
de coordenadas coincide; una salida identity sobre etiquetas no se interpreta
como pose. Regenerar visualizaciones no vuelve a inferir ni entrenar.

Las métricas contra referencias solo ofrecen una acción de cálculo cuando hay
observaciones con etiquetas evaluables. Una distribución de estados o cobertura
de inferencia no demuestra exactitud conductual. El video requiere fuentes de
las mismas sesiones; cambiar el dataset activo no vincula videos de otras sesiones.

## Gráficas comparativas y tiempos

**Resultados → Comparar corridas** presenta paneles geométricos por salida y una
vista en un espacio de referencia común. Esta última conserva las coordenadas
de una salida y muestra las etiquetas de los otros modelos sobre identidades
compartidas de la misma sesión, partición y fuentes verificadas. Las coordenadas
no se superponen entre espacios latentes independientes. Se pueden elegir ejes;
no se aplica reducción dimensional implícita. La geometría utiliza una muestra
determinista de hasta 2.000 puntos por espacio y declara cuántos representa.

Las distribuciones usan todas las predicciones válidas, muestran las 12 etiquetas
más frecuentes y agrupan el resto. La tabla de contingencia de un par usa toda la
cohorte común, no la muestra geométrica. No interpreta igualdad numérica de IDs
como equivalencia conductual. Las corridas legacy con índices y sesiones/frames
alineados también pueden mostrar su propia geometría; sin hashes verificables
no se enlazan con otras fuentes.

El panel de tiempos distingue duración del nodo, duración total de la corrida y
duración del nodo original cuando se recuperan pesos. Las próximas corridas de
grafo guardan tiempos de entrenamiento e inferencia separados en la evidencia.
Los históricos que solo guardaron la suma se identifican como «no registrado por
separado»; no se infiere el tiempo de entrenamiento desde la duración total.
Las pérdidas por época conservadas en el checkpoint aparecen en su propio gráfico.
La comparación visual no entrena, infiere ni recalcula métricas científicas.
