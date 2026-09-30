# STORM Suite con Django

Estado: implementación funcional para estudios genéricos y conectable a plugins
de dominio. RAINSTORM se integra desde un paquete separado; STORM conserva los
contratos y la ejecución comunes. El mockup HTML previo se conserva como
referencia; esta aplicación ejecuta código Python y persiste resultados reales.

## Instalación y ejecución

Desde la raíz del monorepo:

```bash
uv sync --extra test --extra docs
uv run storm-studio migrate
uv run storm-studio demo
uv run storm-studio runserver 127.0.0.1:8000
```

En otra terminal, en el mismo directorio:

```bash
uv run storm-studio worker
```

Abrir `http://127.0.0.1:8000`. No requiere npm, React ni servicios remotos.
`demo` crea un estudio nuevo cada vez; no comienza entrenamientos.

`STORM_WORKSPACE` configura la carpeta de SQLite, clave de sesión y artefactos.
Servidor y worker deben usar el mismo valor absoluto. El valor predeterminado es
`.storm/` respecto del directorio de ejecución. No está pensado para publicarse
en una red multiusuario.

## Recorrido

1. Crear proyecto y estudio, y registrar las fuentes en Datos. Un plugin puede
   ofrecer H5/CSV de pose, ROI, videos y anotaciones; cada archivo queda con hash
   y los elementos auxiliares se vinculan a una sesión.
2. Inspeccionar el inventario, revisar sesiones y particiones, y preparar una
   receta codeless. La vista previa permite comprobar coordenadas, frames y
   pasos antes de guardar una revisión procesada.
3. Elegir un modelo del catálogo científico y configurar sus opciones desde el
   schema del adapter. Modelos de referencia y plugins pueden declarar
   entrenamiento, agrupamiento, inferencia, actualización o checkpoints.
4. Guardar el plan y aprobar su puesta en cola en Ejecuciones. El worker registra
   resultados y artefactos sin depender de que el navegador siga abierto.
5. Abrir la evidencia por observación original, comparar ejecuciones compatibles
   y revisar las razones cuando sólo corresponda una inspección lado a lado.
6. En Modelos, aplicar una corrida guardada a otra revisión lista del mismo
   adapter de entrada. Studio reutiliza el artefacto del modelo y sus pasos ya
   ajustados, procesa todas las observaciones y crea una corrida enlazada a la
   fuente; no vuelve a entrenar ni reemplaza el modelo original.
7. Proponer muestras para revisión, adoptar un candidato con justificación y
   descargar reportes HTML, CSV o JSON. Revisiones y snapshots anteriores se
   conservan.

Si el inventario detecta saltos de timestamp o no puede verificar un video,
Datos muestra el timeline y pide una decisión con investigador y motivo. La
decisión guarda los hashes, asociaciones, FPS y límites revisados. Preparar,
previsualizar pasos o ejecutar un modelo queda bloqueado hasta registrarla; si
los timestamps son desconocidos, aceptar deja explícito que se usará el video
como continuo. Cambios en el timeline piden una revisión nueva.

En Datos también se pueden editar los límites por sesión usando índices de frame
del video con base 0. Guardarlos crea una revisión nueva y vuelve a inspeccionar
el dataset. La detección automática se conserva como evidencia; el adapter recibe
los límites efectivos seleccionados y los usa para dividir segmentos. La nueva
revisión requiere su propia decisión antes de procesar o ejecutar modelos.
Cada edición también deja una revisión inmutable con autor, motivo, límites y
referencia a la versión anterior.

## API compartida y extensiones

`storm.suite.execute(spec, store_root, execution_id, catalog=None)` entrena o
agrupa y persiste artefactos; `infer(result, inputs, store_root, catalog=None)`
recupera el modelo para una lista de entradas. Para ejecutar el modelo guardado
sobre un dataset registrado, `execute` acepta `inference_from=source_result`.
Valida modelo, versión, configuración, adapter y pasos; carga el mismo artefacto,
reproduce la preparación ajustada en entrenamiento y guarda una nueva salida
con `source_execution`. No ajusta el modelo ni aprende pasos con los datos de
inferencia. La API antigua `Study` permanece compatible y conserva su
comportamiento original.

La especificación del nuevo recorrido usa `model`, `config`, `seed`, `data`,
`steps` y, opcionalmente, `connector`, `metrics` y `constraints`. No es un
reemplazo silencioso de `StudySpec`.

```python
from storm.suite import execute, infer

result = execute({
    "model": "mean_regressor", "config": {}, "seed": 42,
    "data": {"inputs": [0, 1, 2], "targets": [2, 4, 100],
             "train": [0, 1], "test": [2]},
    "steps": [{"type": "center"}],
}, "artifacts", "example-001")
assert result["predictions"] == [3.0]
assert infer(result, [8], "artifacts") == [3.0]

applied = execute({
    "model": result["model"], "config": result["spec"]["config"],
    "operation": "infer", "connector": result["spec"].get("connector", "numeric_json"),
    "steps": result["spec"].get("steps", []), "metrics": [],
    "data": {"inputs": [8, 9], "train": [], "test": []},
}, "artifacts", "example-apply-001", inference_from=result)
assert applied["model_ref"] == result["model_ref"]
assert applied["source_execution"] == result["execution_id"]
```

En Studio, esta operación aparece para modelos con capacidad `infer` cuando hay
una revisión lista con el mismo conector. Si el modelo produce una salida binaria,
las métricas sólo se calculan cuando la taxonomía y su correspondencia versionada
coinciden; una taxonomía distinta queda disponible para inspección.

`Catalog` expone descriptores de modelos (`Component`), conectores y los registros
existentes de pasos, métricas y visualizaciones. Un módulo habilitado mediante
`STORM_PLUGINS` exporta `register(catalog)`. Los plugins son código instalado y
habilitado por el operador; un archivo subido desde Datos no instala código.

El ejemplo `examples/storm_suite_plugin` registra cinco tipos de componentes.
Para usarlo en desarrollo (configurar ambas terminales):

```bash
export PYTHONPATH="$PWD/examples"
export STORM_PLUGINS=storm_suite_plugin
```

Los adapters pueden declarar `train`, `infer`, `group` y `constraints`.
También pueden declarar `update` y `checkpoint` cuando implementan los métodos
correspondientes. El conector `json_records` acepta registros JSON y targets
categóricos; las métricas y transformaciones deben ser compatibles con ellos.
Las operaciones comprueban la capacidad necesaria. Studio muestra controles
numéricos y simples para propiedades escalares, permite editar listas y objetos
sin escribir código y expande los objetos predeterminados en controles por campo.
El schema puede incluir `description` para explicar valores derivados de las
fuentes registradas, como las rutas que VAME oficial recibe desde cada sesión.

## Revisión y aprendizaje informado

`storm.learning.select_samples` ofrece selección aleatoria reproducible y por
incertidumbre con scores explícitos en [0, 1]. En Evidencia se puede elegir la
estrategia. La incertidumbre exige que el adapter devuelva `confidence` y declare
`confidence_semantics="probability"` para las muestras candidatas; en caso
contrario se rechaza, sin inventar scores.

Las revisiones guardan origen, muestra, corrección y restricciones. Una
restricción `[a, b]` del ejemplo significa must-link. `constrained_groups`
construye componentes conexas que respetan esos pares; sus IDs no son clases ni
conceptos del dominio. Los otros modelos rechazan restricciones incompatibles.

Los contratos de alineación, actualización incremental, checkpoints y proveedor
de asistencia están en `storm.learning`. La ayuda local es un proveedor
determinista simulado, no un LLM. Sus propuestas no ejecutan acciones.

## Actualización, checkpoints y anotaciones

En Modelos, `online_mean` permite aprobar una actualización con un lote nuevo.
El candidato carga una copia persistida del modelo, conserva la preparación
aprendida y ejecuta `partial_fit`. El modelo original no cambia. Este mecanismo
agrega observaciones: corregir etiquetas existentes requiere reentrenar para no
contabilizar las mismas muestras dos veces.

Un adapter con capacidad `checkpoint` implementa `fit_with_checkpoints` si es
supervisado, o `fit_predict_with_checkpoints` si agrupa datos, además de
`save_checkpoint` y `load_checkpoint`. El callback de entrenamiento guarda
estado y compatibilidad. Un trabajo fallido o interrumpido puede reanudarse
desde Ejecuciones si existe checkpoint. La reanudación valida fingerprint del
plan, datos resueltos y versión del modelo; crea un intento nuevo y preserva el
anterior. La prueba de recuperación interrumpe el entrenamiento tras la primera
muestra y verifica que no vuelva a procesarla. Las actualizaciones incrementales
no generan checkpoints automáticamente.

Desde Python se usan los argumentos `update_from=result` o
`resume_from=checkpoint_ref` de `storm.suite.execute`; son operaciones excluyentes.

En Evidencia se guardan taxonomías, intervalos e interpretaciones de grupos.
Los intervalos usan índices originales y la convención `[start, stop)`. Cada
versión humana requiere autor y motivo, referencia la ejecución y fingerprint de
datos, y enlaza a la revisión humana anterior; no reemplaza el output del modelo
ni los targets de evaluación. El formulario del plan puede seleccionar una
revisión de correcciones: el worker sólo aplica sus etiquetas a observaciones de
entrenamiento fuera de la reserva de evaluación y registra qué IDs aplicó o
excluyó. La selección queda guardada en una nueva revisión del plan.

La misma página permite abrir los visualizadores registrados en el catálogo.
Se sirven SVG, PNG o texto. Las incompatibilidades entre el renderer y sus
entradas se informan como errores, sin convertirlas silenciosamente.

## Persistencia y pruebas

Django conserva proyectos, estudios, revisiones y trabajos en SQLite. Cada
trabajo tiene UUID previo al inicio, estado, error, tiempos y referencias a
artefactos. Los reintentos referencian el intento previo.

El worker reclama trabajos pendientes, ejecuta un subproceso por trabajo y
registra interrupciones al reiniciar. Solo se admite un worker por workspace.
Tras una terminación forzada puede quedar `worker.lock`: verificar que no siga
activo el PID guardado antes de retirar ese archivo. Cancelar termina el hijo;
no borra artefactos parciales. Los modelos usan el store confiable de pickle
existente; no importar artefactos de fuentes no confiables.

Pruebas de integración y navegador cubren particiones, recuperación, inferencia,
revisión, importación, plugins y navegación. El motor se construye como wheel
sin dependencias de Django.

## Alcance pendiente del plan completo

El [inventario de faltantes](../architecture/suite-gaps.md) distingue capacidades
parciales y ausentes con referencias al código. El
[plan de cierre vigente](../planning/suite-completion.md) establece prioridades,
dependencias y pruebas de aceptación; la lista siguiente es sólo un resumen.

Esta entrega establece el monorepo y el primer recorrido funcional. Aún quedan:

- lienzo de conexiones más rico y descriptores completos para todas las clases de componentes
  (la secuencia actual se reordena por arrastre o botones);
- conectores paginados y renderers enriquecidos para registros no numéricos
  (ya se admite `json_records` para datos y targets categóricos);
- asistente visual de particiones por grupos (el contrato JSON ya admite
  `validation` y `groups`, y verifica su separación);
- reglas adicionales y codecs de modelo;
- comparación gráfica simultánea y reportes agregados
  (ya se comparan salidas alineadas en una tabla, se explican incompatibilidades y
  existen exportaciones agregadas HTML/CSV/JSON; la gráfica sincronizada sigue pendiente);
- snapshots completos de selección y filtros (el endpoint ya persiste `visual_state`,
  pero la captura automática desde todos los controles de la UI sigue pendiente);
- validación de instalación y empaquetado de la aplicación en Windows y macOS.

El worker acepta `--recover-stale` para retirar un lock sólo cuando el PID guardado
ya no existe. No se elimina un lock cuyo proceso esté activo.

La futura integración de cualquier aplicación de dominio queda fuera de esta
entrega y de sus dependencias.
