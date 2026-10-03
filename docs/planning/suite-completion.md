# Plan de cierre de la suite genérica

Fecha: 2026-09-21. Estado: propuesto; las etapas siguientes están pendientes.
Base: [inventario de faltantes](../architecture/suite-gaps.md),
[arquitectura vigente](../architecture/monorepo.md) y
[guía de ejecución](../guides/django-suite.md).

## Objetivo y reglas

Completar el recorrido local: cargar → preparar → configurar → entrenar o
recuperar → inferir → inspeccionar → corregir → comparar → adoptar → reportar.
Debe funcionar con componentes sintéticos y plugins externos, sin aplicaciones
de dominio ni dependencias científicas obligatorias en el motor.

Mantener el monorepo y Python/Django/templates. Usar JavaScript pequeño cuando la
interacción lo necesite; no introducir otro frontend o backend. Primero completar
la composición secuencial existente; un DAG arbitrario no es requisito de cierre.

Cada entrega debe escribir primero una prueba del faltante, comprobar su fallo,
implementar el cambio mínimo y ejecutar regresiones. No trasladar módulos enteros
ni reestructurar `suite.py` sólo para ordenar el código. Si cambia un contrato,
documentar formato anterior, formato nuevo y adaptación antes de eliminarlo.

## Orden y dependencias

| Entrega | Faltantes | Dependencias | Resultado verificable |
|---|---|---|---|
| E1. Contratos y validación | F01, F15 | Ninguna | Configuración inválida se rechaza igual por Python y por web; API actual preservada. |
| E2. Integridad y recuperación | F04, F05 | E1 para versionado | Una decisión no se duplica; interrumpir y recuperar no pierde el origen de la ejecución. |
| E3. Identidad de datos y preparación | F02, F07 | E1 | Cada salida mantiene correspondencia comprobable con la observación original. |
| E4. Autoría visual y plugins | F07, F08 | E1, E3 | Agregar un conector y un paso externos produce formularios utilizables sin editar Studio. |
| E5. Comparación y persistencia científica | F03, F06 | E1–E3 | La suite explica si dos resultados son comparables y cómo recuperarlos. |
| E6. Corrección y revisión | F09 | E2, E3, E5 | Corrección aprobada crea un candidato trazable sin alterar el resultado previo. |
| E7. Estudio visual recuperable | F10, F11 | E3, E5, E6 | Se recupera la vista de comparación y anotación sin ejecutar ni adoptar modelos. |
| E8. Reportes reproducibles | F12 | E5, E7 | Un reporte congelado se reconstruye con las mismas corridas y revisiones. |
| E9. Asistencia opcional | F13 | E1, E2, E6 | Apagada implica cero invocaciones; las propuestas nunca mutan un plan por sí solas. |
| E10. Cierre de distribución local | F14, F15 | E1–E8; E9 sólo si se distribuye | Instalación, pruebas y recuperación verificadas en la matriz declarada. |

Las comprobaciones de accesibilidad y empaquetado comienzan desde cada entrega;
E10 es su puerta de aceptación, no el primer momento de probarlas. No se fijan
fechas sin estimar cada entrega sobre su alcance definitivo.

## E1 — Contratos antes de más interfaz

Áreas: `storm.suite.Component/Catalog`, registros existentes, formularios y API.

- Definir un descriptor serializable con ID estable, versión, clase de componente,
  capacidades, esquema de configuración y compatibilidad de entradas/salidas.
- Especificar el subconjunto de esquema admitido: tipos, requeridos, límites,
  enumeraciones y defaults. Rechazar declaraciones no soportadas, no ignorarlas.
- Compartir validación entre motor y formularios; los descriptores no importan Django.
- Versionar planes/resultados nuevos y definir lectura explícita de documentos anteriores.
- Documentar `Study` frente a `suite.execute/infer/evaluate`; no cambiar sus métricas
  ni particiones silenciosamente. Proponer adaptación antes de converger APIs.

Aceptación: tests de tipos inválidos, campos faltantes, capacidades ausentes,
versiones no soportadas y carga de una especificación anterior. El plugin de
ejemplo y el ejemplo `Study` continúan funcionando sin cambios obligatorios.

## E2 — Integridad de historial y trabajos

Áreas: modelos/migraciones Django, servicios de revisión, worker y CLI.

- Definir transiciones válidas e idempotencia de envío, cancelación y aceptación.
- Proteger revisiones frente a vías de escritura soportadas, incluidas operaciones
  masivas; decidir y documentar garantías de base de datos frente a garantías de servicio.
- Recuperación guiada de lock obsoleto, sin retirar el lock de un worker activo.
- Persistir eventos y errores por ejecución; distinguir reintento, reanudación y actualización.
- Documentar respaldos coherentes de SQLite y artefactos, sin borrar evidencia parcial.

Aceptación: doble aceptación concurrente produce una sola decisión; reinicio tras
interrupción mantiene lineage; cancelación no termina como éxito; lock activo
impide un segundo worker. Probar recuperación con procesos reales. Mantener la
prueba existente de checkpoint que evita volver a contar muestras.

## E3 — Dataset y alineación como contratos

Áreas: carga de datos, `ObservationAlignment`, pipeline y manifiestos.

- Incorporar referencias versionadas a datasets, manteniendo datos inline para demos.
- Conservar IDs de observaciones, particiones y grupos independientemente de la posición.
- Integrar alineación en preparación, inferencia y visualización. Primero soportar
  identidad/permutación explícita; rechazar filtrados o agregaciones sin mapeo definido.
- Especificar el ajuste de transformaciones sólo en train y reutilizar su estado al inferir.
- Previsualizar por páginas y validar particiones antes de encolar; no cargar todo
  el dataset sólo para mostrar su primera página.

Aceptación: un paso que permuta filas conserva targets y evidencia correspondientes;
sin mapeo falla de forma legible. No hay fuga entre grupos/particiones. Reabrir un
dataset detecta cambios de contenido. Ejemplos numéricos y categóricos siguen válidos.

## E4 — Construcción sin código y autoría de extensiones

Áreas: catálogo, formularios, templates y ejemplos de plugins.

- Formularios generados desde descriptores para conectores, pasos, modelos y métricas.
- Bloques secuenciales con validación de compatibilidad y edición accesible por teclado.
- Asistente de conectores: esquema de entrada, preview, validación y skeleton descargable.
- Asistente de adaptadores: capacidades elegidas, métodos requeridos y tests de conformidad.
- Mantener escape hatch JSON y registro Python explícito; el asistente no instala paquetes.

Aceptación: un paquete de ejemplo registra un conector, paso, modelo, métrica y
renderer sin modificar el núcleo. Un usuario configura y ejecuta ese recorrido
desde formularios. El código generado incluye pruebas y falla claramente hasta
completar los métodos pendientes, sin simular un entrenamiento exitoso.

## E5 — Comparación científicamente válida

Áreas: métricas, manifiestos, artefactos y servicio de comparación.

- Registrar versión/configuración/dirección de métricas, tarea, targets y partición evaluada.
- Devolver razones de incompatibilidad; no generar ranking entre resultados incompatibles.
- Registrar versiones de componentes, entorno relevante y política de semillas.
- Introducir codecs por capacidad/necesidad real; conservar recuperación de artefactos
  locales existentes. No deserializar pickle de fuentes no confiables.

Aceptación: igual nombre y distinta versión de métrica impide ranking; mismo
dataset con targets/partición distintos no se compara silenciosamente. Verificar
integridad de artefactos y recuperación en proceso nuevo, incluyendo ausencia de
dependencias opcionales y mensajes accionables.

## E6 — Revisión humana y aprendizaje asistido

Áreas: estrategias, anotaciones, servicios y controles de aprobación.

- Registrar estrategias de consulta y declarar los requisitos de incertidumbre.
- Incorporar pool no etiquetado independiente; excluir test de consultas destinadas a entrenamiento.
- Vincular una versión de anotaciones con una versión de targets mediante una operación
  explícita y trazable; no reinterpretar automáticamente IDs de grupos como clases.
- Diferenciar agregar muestras (`partial_fit`) de corregir muestras existentes (reentrenar
  salvo contrato específico del adapter). Checkpoints incrementales quedan condicionados
  a una capacidad explícita; no prometerlos para todos los modelos.

Aceptación: un modelo sin `predict` no ofrece consulta por incertidumbre; falta de
confianza válida produce diagnóstico, no excepción accidental. Corregir y aprobar
crea un plan sucesor, ejecutar crea candidato, adoptar exige otra decisión. Los
outputs y targets de evaluación originales permanecen intactos.

## E7 — Estado propio del estudio visual

Áreas: evidencia, renderers, comparación, snapshots y templates.

- Separar el estado visual del plan: corridas seleccionadas, filtros, rango/cursor,
  paneles, selección de observaciones y versión de anotaciones.
- Comparación gráfica sincronizada por identidad/alineación, con fallback tabular.
- Renderers declaran formatos/unidades admitidos; no aplicar gráficas numéricas a registros arbitrarios.
- Restaurar snapshot crea un nuevo evento, no reescribe el pasado. La adopción de un
  modelo guardado en el snapshot requiere confirmación independiente.

Aceptación: cerrar/reabrir recupera selecciones y filtros; observar/restaurar no
lanza entrenamiento. Dos resultados sobre las mismas observaciones comparten
cursor; desalineación se informa. Pruebas de navegador cubren teclado, foco,
contraste, pantallas pequeñas y ausencia de errores JavaScript.

## E8 — Reportes como artefactos

Áreas: servicios de reporte, templates y almacenamiento.

Estado actual: Studio permite congelar una revisión `frozen_report` desde
Reportes. Guarda resultados completos de corridas seleccionadas, planes,
revisiones de datos y hashes de fuentes, anotaciones con su compatibilidad por
fingerprint y el snapshot visual elegido. Ofrece HTML, CSV y JSON. Los
archivos vinculados también pueden exportarse en un ZIP con manifiesto de hashes:
fuentes, artefactos, checkpoints disponibles y vistas de pose. La exportación
copia los binarios por bloques y no deserializa los payloads pickle. La
restauración portable entre workspaces queda pendiente.

- [x] Congelar resultados y procedencia de la selección en HTML, CSV y JSON;
  distinguir predicciones, anotaciones de intervalos e interpretaciones humanas.
- [x] Conservar la exportación individual y los resúmenes agregados actuales.
- [x] Empaquetar fuentes y artefactos vinculados, con hashes por archivo y sin
  cargar binarios completos en memoria.
- [ ] Agregar figuras con alternativa textual y un importador que restaure el
  estudio en otro workspace. PDF/LaTeX no son requisito inicial.

Aceptación: modificaciones posteriores no cambian un reporte congelado; regenerar
desde sus referencias reproduce tablas y selección. Un reporte no mezcla rankings
incompatibles y sus figuras tienen alternativa textual.

## E9 — Asistencia opcional, nunca autoridad de ejecución

Áreas: `AssistantProvider`, preferencias, servicios y UI.

- Guardar habilitación y alcance de datos en servidor; mantener desactivado por defecto.
- Usar primero el proveedor simulado para validar permisos y estados propuesto/aprobado/rechazado.
- Un proveedor externo será plugin opcional. Credenciales fuera de planes, reportes y logs;
  envío de datos sujeto a consentimiento y límites explícitos.
- Desactivar impide solicitudes nuevas; una respuesta tardía no se aplica automáticamente.

Aceptación: proveedor espía recibe cero llamadas cuando está desactivado, incluso
por POST directo. Aprobar una propuesta crea revisión validada, no código ejecutado
ni entrenamiento implícito. Fallos del proveedor no bloquean el estudio manual.

## E10 — Entrega local verificable

- Matriz Linux/Windows/macOS para instalar wheels, migrar workspace, entrenar,
  cancelar, recuperar e inferir. Declarar explícitamente entornos no verificados.
- Test del motor aislado sin Django ni dependencias científicas; prueba del plugin externo.
- Revisar licencias y procedencia de código/activos y metadatos LGPL-3.0-or-later;
  no asumir que una licencia del repositorio cubre código de terceros.
- Publicar guía de actualización, compatibilidad, backup/restauración y límites de seguridad local.
- Actualizar API, ejemplos y matriz de faltantes en cada entrega; archivar mockups
  históricos sin presentarlos como funcionalidad productiva.

## Primer cambio recomendado

Empezar por un subcorte de E1: validación común de campos requeridos y tipos de
configuración de modelos, con errores legibles en formulario y llamada Python.
Es acotado a catálogo/formulario/tests; no exige migrar datos ni cambiar arquitectura.
Después extender descriptores gradualmente a pasos y conectores. No implementar
las diez entregas en una sola modificación.

## Definición de terminado

Una entrega se cierra con pruebas de regresión, documentación de API/uso actualizada,
ejemplo genérico reproducible y limitaciones explícitas. Los cambios de esquema
incluyen migración y lectura de fixtures anteriores. Los cambios de UI incluyen
prueba de navegador. La documentación se valida con MkDocs estricto y enlaces
internos. Ninguna entrega necesita importar una aplicación de dominio.

## Seguimiento vigente STORM + RAINSTORM (2026-10-02)

Las etapas E1–E10 anteriores conservan sus objetivos. El seguimiento ejecutable
se concentra en los siguientes issues; no se crea un roadmap independiente.
Épicas relacionadas: [STORM](https://github.com/munhof/STORM/issues/1) y [RAINSTORM](https://github.com/munhof/RAINSTORM/issues/1).

[Diagnóstico fechado](https://github.com/munhof/RAINSTORM/blob/contracts-backlog-20261002/docs/plans/01_diagnostico_estudios_20261002T220735.md) y [propuesta detallada de origen](https://github.com/munhof/RAINSTORM/blob/contracts-backlog-20261002/docs/plans/02_plan_backlog_estudios_20261002T220735.md).

| ID | Prioridad | Issue definitivo | Estado de esta entrega |
|---|---|---|---|
| ST-01 | P0 | [Implementar contratos de entrada y validación compartida antes de encolar](https://github.com/munhof/STORM/issues/2) | Implementado con regresiones; issue abierto para revisión |
| ST-02 | P0 | [Separar plan activo de variantes de ejecución y evitar ramas heredadas](https://github.com/munhof/STORM/issues/3) | Pendiente |
| ST-03 | P0 | [Permitir dataset y preparación propios por rama](https://github.com/munhof/STORM/issues/4) | Pendiente |
| ST-04 | P1 | [Reutilizar preparación compatible y mostrar motivos de cache hit/miss](https://github.com/munhof/STORM/issues/5) | Pendiente |
| ST-05 | P1 | [Retener checkpoints históricos y validar continuidad](https://github.com/munhof/STORM/issues/6) | Pendiente |
| ST-06 | P1 | [Normalizar avance por fase, lote y sesión; separar heartbeat de progreso](https://github.com/munhof/STORM/issues/7) | Pendiente |
| ST-07 | P1 | [Validar tareas, métricas y compatibilidad antes de comparar](https://github.com/munhof/STORM/issues/8) | Pendiente |
| ST-08 | P2 | [Extender catálogo y hosts de UI mediante descriptores de plugins](https://github.com/munhof/STORM/issues/9) | Pendiente |
| ST-09 | P2 | [Editor gráfico con validación, teclado y equivalencia con especificaciones Python](https://github.com/munhof/STORM/issues/10) | Pendiente |
| ST-10 | P2 | [SDK de extensiones y publicación opcional de código en workers aislados](https://github.com/munhof/STORM/issues/11) | Pendiente |
| ST-11 | P3 | [Dashboards con gráficos, tablas y evidencia sincronizada](https://github.com/munhof/STORM/issues/12) | Pendiente |
| ST-12 | P3 | [Exportación, restauración y aceptación desde instalación limpia](https://github.com/munhof/STORM/issues/13) | Pendiente |


ST-01 y RS-01 se implementan juntos. ST-03 depende de ST-01/ST-02; RS-04 y RS-05
preceden RS-06 y RS-09. RS-07 depende de ST-03. ST-08 y RS-10 preceden ST-09;
ST-11 depende de alineación y comparación verificadas. GPU Hang se trata por
RS-08 independientemente del preprocesado. Dependencias detalladas y aceptación
están en cada issue.

La primera entrega implementa contratos y preflight; reconstrucción científica,
plan activo/variantes, inputs por rama, editor gráfico y dashboards permanecen
pendientes. No se modifican revisiones históricas ni se inician entrenamientos.

### Verificación de la primera entrega

Pruebas escritas antes de implementación y comprobadas fallidas por interfaces
faltantes; la regresión adicional del endpoint reprodujo HTTP 500 antes de su fix.
14 pruebas nuevas de STORM y 2 de RAINSTORM cubren diagnóstico por rama, cero jobs,
proveniencia materializada, shapes, plugins numéricos, imports lazy y callbacks.
La suite STORM (sin navegador) reportó 255 aprobadas y 1 fallo previo de retención;
el mismo fallo fue reproducido con suite.py de HEAD previo en una copia aislada.
Se mantiene en [ST-05](https://github.com/munhof/STORM/issues/6). La suite dirigida RAINSTORM reportó 58 aprobadas y 17 omitidas
por runtimes opcionales; no se realizaron entrenamientos ni pruebas GPU.

MkDocs estricto y comprobación de enlaces/diffs forman parte de la publicación.
El [protocolo compartido](https://github.com/munhof/STORM/blob/contracts-backlog-20261002/CONTRIBUTING.md)
exige reproducir, comprobar test fallido, cambio mínimo, regresión, contrato/guía/evidencia
y cierre sólo con aceptación. Roadmaps anteriores permanecen históricos.

La copia aislada del contenido exacto publicado, sin cambios locales previos,
pasó 256 pruebas de STORM (excluye tests de navegador). El workspace conserva
la regresión local de retención que motivó ST-05; no se incluye en estos commits.
STORM contratos: `e08b9a4059e22191ed4dbfb6f31d9897a5368e05`.
