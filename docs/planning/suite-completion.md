# STORM: estudios reproducibles sobre contextos y pipelines

Plan vigente desde 2026-10-06. Reemplaza la restricción previa de cierre mediante
composición secuencial. Los objetivos anteriores de historial, revisión, reportes
y distribución siguen abiertos en sus issues; el contenido anterior se conserva
en el historial Git. No hay otro roadmap vigente para esta iniciativa.

## Objetivo

Expresar el mismo experimento en Python y Studio: fuentes, contexto, transformaciones,
entradas de modelos, variantes, parámetros, semillas, particiones y métricas.
El criterio final exige autoría visual, exportación Python equivalente y evidencia
reproducible; un formulario o una captura no cierran una entrega.

El contrato vigente y los ejemplos están en
[Contextos y experimentos](../guides/context-experiments.md).

## Entregas y seguimiento

| Orden | Entrega | Prioridad | Dependencias / seguimiento |
|---|---|---|---|
| 1 | [ST-13](https://github.com/munhof/STORM/issues/14): modelo conceptual y contratos | P0 | ST-01 #2 |
| 2 | [ST-14](https://github.com/munhof/STORM/issues/15): contexto completo y adaptadores separados | P0 | ST-13; ampliar ST-08 #9 |
| 3 | [ST-15](https://github.com/munhof/STORM/issues/16): DAG, aislamiento y alineación multifuente | P0 | ST-13/14; reutilizar ST-03 #4 y ST-04 #5 |
| 4 | [ST-16](https://github.com/munhof/STORM/issues/17): scheduler común y lectores legacy | P0 | ST-13/15 |
| 5 | [ST-17](https://github.com/munhof/STORM/issues/18): variantes, barridos y selección protegida | P1 | ST-16; ampliar ST-07 #8 |
| 6 | Editor de experimentos e inspector | P1 | ST-08 #9, ST-09 #10, descriptores y validación |
| 7 | SDK, recuperación y ejemplos | P2 | ST-10 #11, ST-12 #13 |
| 8 | Resolución científica y aceptación estudio 8 | P0 científico | RAINSTORM RS-02 #3, RS-04 #5, RS-05 #6, RS-06 #7 y RS-08 #9 |

Épicas: [STORM](https://github.com/munhof/STORM/issues/1) y
[RAINSTORM](https://github.com/munhof/RAINSTORM/issues/1).
Los números definitivos de ST-13…17 están enlazados en la épica.

## Estado verificable de la implementación local

- Contexto completo entre pasos de suite, checkpoint de preparación y modelo mediante
  `bind_context`. Se preservan las firmas existentes y los campos públicos.
- Descriptores de contenidos, adaptadores separados y ciclo fit/predict contextual.
- DAG versionado con nodos/puertos declarados, orden determinista, aislamiento de
  valores mutables, procedencia, bloqueo de descendientes y estados globales.
- Unión por identidad y temporal exacta/anterior/cercana/lineal. Tolerancia explícita,
  sin extrapolación, empates anteriores, rechazo de relojes incompatibles y duplicados.
- Fuentes inline particionadas, escala, centrado aprendido exclusivamente en train,
  ventanas con IDs de procedencia, adaptación de entradas, modelos y métricas.
- Dos variantes del mismo modelo, barridos cartesianos sobre parámetros de nodos,
  semillas y expansión completa antes de crear trabajos.
- Scheduler común: `suite` y `RunEngine` pasan por operaciones legacy atómicas.
  Se conserva su ciclo interno; **no está completada la migración granular**.
- Editor Django/SVG/JS: catálogo, canvas, conexiones en inspector, edición, duplicado,
  teclado, undo/redo, zoom, desplazamiento, guardado, exportación/importación y jobs.
- Estado visual separado; preview inline y callbacks acotados de plugins sin entrenar.
- RAINSTORM: fuentes de sesiones con hashes, receta explícita de preparación completa,
  ventanas desde archivos inmutables y muestreo después de auditar todas las sesiones.
- Selección congelada y acción de test separada para las operaciones con replay.
- Receta RAINSTORM `pose.reconstruct_v2`: exige centímetros verificados, conserva
  máscaras y rechaza segmentos sin datos suficientes. Fixture comparado con Tesis_Facu.

## Puertas que siguen pendientes

- Integración científica completa de RAINSTORM en el editor: contratos de entrada
  especializados por puerto y edición individual de todos los filtros. La fuente,
  preview acotado y arquitectura VAME como detalle separado existen.
- Reutilización/caché del DAG por versiones, hashes de fuentes, particiones y bindings.
- Modelo común granular para las rutas legacy, más allá del scheduler compartido.
- Replay de test y recuperación portable para plugins arbitrarios; el motor rechaza
  operaciones sin replay, sin volver a entrenar sobre test.
- Descriptores ricos de formas/unidades compatibles por puerto para todos los plugins,
  transformaciones explícitas de reloj y validación científica completa del dataset.
- Recuperación portable y matriz completa de dependencias científicas. Los wheels
  engine/Studio/plugin se construyeron y verificaron en un entorno limpio; el
  catálogo del plugin no importó bibliotecas científicas y los dos ejemplos pasaron.

## Estudio 8: aceptación acotada y límites científicos

La revisión de datos 11 del estudio 8 contiene realmente 86 sesiones y 1.523.338
frames: 62 train (1.108.194), 2 validation (37.781), 22 test (377.363).
Se verificaron los hashes y datos crudos de las 64 sesiones train/validation;
los archivos de test no se abrieron. Hay 1.324.410 puntos inválidos o bajo umbral,
0 pares crudos de coordenadas cero y 0 coincidencias crudas nose/body en lo auditado.
Esto no demuestra validez después de preparar.

Evidencia reproducible en RAINSTORM:
`docs/reconstruction/study8-context-audit.json` y `scripts/audit_study8.py`.
Se recuperaron de Tesis_Facu confianza 0,6; velocidad 120 cm/s; distancias 4/12 cm;
máximo 3 conexiones lejanas; PCHIP; mediana 5 y gaussiano sigma 0,6.
La nueva receta declara escala 15,02 px/cm y altura 1052, respaldadas por el
ROI histórico con hash incluido en el grafo. Las 64 sesiones pasaron preparación
estricta y orientación sin fallback; las ventanas respetan frames y particiones.
Se descartaron por el umbral de confianza 14 coordenadas que excedían la altura;
ninguna coordenada confiable excedió ese límite. Una discrepancia confiable sigue
bloqueando la preparación.

VAME nativo completó una época CPU con 256 ventanas train deterministas,
semilla 156, y produjo 37.743 predicciones de validation. La comparación de etapas con Tesis_Facu sobre las 64 sesiones obtuvo máscaras
iguales y coordenadas dentro de atol=1e-6, rtol=1e-6 bajo esa misma calibración.
La revisión científica sucesora 85 y run 86 conservan la evidencia en Studio.
Se conservaron 50 estados,
20 dimensiones latentes, hidden 128, batch 256, LR 0,001 y peso KL 0,5. Test no se
abrió ni se creó una nueva partición porque ya existe validation.
Los scripts `prepare_study8_context.py` y `accept_study8_context.py` guardan grafo,
configuración resuelta, máscaras, IDs, selección de ventanas, modelo y resultados.

La calibración explícita difiere de la carga histórica de la rama nativa. No se
certifica equivalencia del experimento completo, warmup KL histórico, calidad del entrenamiento completo ni estabilidad GPU.
La aceptación acotada no completa las puertas generales de esta iniciativa.

## Verificación local final de esta entrega

STORM: 317 pruebas correctas más 4 pruebas desktop a 1920×1080/2560×1440.
Las pruebas de navegador antiguas con otros tamaños/móvil quedaron fuera del
recorrido de esta iniciativa. RAINSTORM focalizado: 52 correctas y 14 omitidas
por runtimes opcionales. MkDocs estricto, sintaxis JS y diff check correctos.
Se corrigió la pérdida de checkpoints por época preservando la referencia latest.
Los wheels se instalaron en un entorno limpio y ejecutaron ambos ejemplos; el
catálogo RAINSTORM se cargó sin NumPy, pandas, SciPy ni PyTorch.
Las capturas reales y evidencia de Studio están bajo
`.storm/rainstorm/study8-context-v2/`. El código está local, aún sin publicar.

## Continuación: recuperación y comparación (2026-10-08)

Implementado y cubierto por regresiones: apertura de las pantallas de recuperación
y comparación; fuentes entrenadas de estudios locales; inferencia sin `fit`;
control de versión; copia de parámetros para entrenamiento nuevo; recuperación
acotada de centrado, escala, ventanas y selección de entradas; comparación de
métricas descendientes del modelo y alineación por sesión e identidad. La evidencia
exploratoria y las categorías incompatibles no habilitan ranking. El recorrido del
inspector se verifica en 1920×1080 y 2560×1440.

Pendiente de esta ampliación: fuentes de frames RAINSTORM para una rama supervisada
completa y auditada; manifiestos portables de bundles importados; replay de pasos
arbitrarios y legacy; continuación desde checkpoint y actualización incremental
en el grafo; comparación histórica con reconstrucción portable y cohortes comunes
recalculadas. Estas capacidades no se consideran completadas por tener un selector.

Verificación de esta continuación: 364 pruebas sin navegador y 16 pruebas de
escritorio correctas; MkDocs estricto, sintaxis JavaScript y `git diff --check`
correctos. Studio y worker existentes iniciados sin trabajos pendientes. Las
páginas reales de Experimento, Resultados y Comparar corridas del estudio 8
respondieron HTTP 200 sin errores JavaScript ni desbordamiento horizontal.
Capturas locales: `/tmp/storm-model-recovery-1920.png`,
`/tmp/storm-model-recovery-2560.png` y `/tmp/storm-study8-comparisons.png`.

### Comparación del estudio de 86 sesiones

Revisión 91 y corrida `a77c6f5d-31ea-43e4-8608-9f5238e0233a` completadas con
VAME recuperado, constant, identity como control de VAME y los supervisados
simple/wide. Se agregó recuperación explícita de contextos de la corrida anterior
(`adapter.saved_context`) y carga de frames de pose con descarte por confianza
(`pose.load_frames`). Los supervisados permanecen exploratorios y produjeron
únicamente clase 0 con esta receta; no se certifica preparación histórica ni
ranking. Se preservaron las 22 sesiones de test. La comparación visual muestra
frecuencias por salida. STORM: 367 pruebas correctas; plugin de pose: 11; regresión
PyTorch de límite de hilos en inferencia: 1. Las capturas del estudio están en
`/tmp/study8-comparison-*.png`, en las dos resoluciones de escritorio.

### Panel comparativo de geometría, etiquetas y tiempos · 2026-10-09

Implementados paneles de geometría propia y en referencia común, distribución
comparativa, contingencia por identidad sobre cohortes completas, tiempos por
nodo/corrida/origen e historial de pérdidas. Se admiten salidas legacy alineadas
para inspección propia, sin habilitar cruces de fuente o partición no verificables.
El grafo registra tiempos separados de fit y predict para las próximas corridas;
los históricos conservan la distinción entre duración conjunta y medición separada.
Verificación: 377 pruebas sin navegador, 2 regresiones desktop nuevas y comprobación
de las dos corridas de la captura del usuario a 1920×1080 y 2560×1440. Se vieron
6 geometrías propias y 5 alineadas, sin errores JavaScript ni desbordamiento.
No se volvió a entrenar ni inferir para producir estas gráficas.
