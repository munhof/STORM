# Faltantes de la suite STORM

Fecha de revisión de la base genérica: 2026-09-28. Estado: diagnóstico del código
actual, no promesa de completar toda la suite. RAINSTORM ya se conecta mediante
un plugin externo; esta tabla refleja sólo los faltantes que siguen vigentes.
El [plan de cierre](../planning/suite-completion.md) define entregables y pruebas.

## Alcance y evidencia

Se revisaron `storm.suite`, `storm.learning`, los modelos, servicios, vistas y
worker de `storm_studio`, y las pruebas `test_suite`, `test_suite_plugins`,
`test_learning_lifecycle`, `test_studio`, `test_studio_worker` y
`test_studio_browser`. Las rutas Python del motor parten de
`packages/storm-engine/src`; las de Django, de `packages/storm-studio/src`.

**Parcial** significa que hay un recorrido implementado pero falta completar
su contrato o experiencia. **Pendiente** significa que no se encontró el
recorrido integrado; puede haber interfaces o prototipos aprovechables.
La existencia de tests no implica cobertura completa ni validación multiplataforma.

La base ya incluye un monorepo Python, Django con templates, registros extensibles,
preparación secuencial, entrenamiento, evaluación separada, inferencia persistida,
actualización incremental, checkpoints de entrenamiento, anotaciones versionadas,
revisión explícita, comparación tabular y exportaciones por ejecución.
No se propone reconstruir estas capacidades.

## Inventario priorizado

P0: integridad científica y contratos; P1: recorrido de producto completo;
P2: distribución y extensiones opcionales. La prioridad no implica que se haya
demostrado un fallo en producción: algunos puntos son límites observados en código.

| ID / prioridad | Área y estado | Evidencia actual | Faltante / riesgo |
|---|---|---|---|
| F01 / P0 | Descriptores y configuración: parcial | `suite.Component`, `Catalog.validate/build`; registros separados de pasos, métricas y vistas | Validación comprueba campos desconocidos y números finitos, no un esquema completo. Faltan contratos uniformes, tipos, campos requeridos, capacidades incompatibles y versiones de especificación. |
| F02 / P0 | Datos y alineación: parcial | `validate_data`, `transform_aligned`, `learning.ObservationAlignment`; RAINSTORM conserva sesiones, segmentos y frames desde DLC hasta preparación/inferencia | La alineación está integrada en el recorrido de pose y los pasos que filtran deben declarar el mapa de observaciones. Falta un contrato universal y paginado para conectores externos heterogéneos. |
| F03 / P0 | Comparabilidad: parcial | `services.compare_reasons` contrasta fingerprint, observaciones, partición, definiciones de métricas, semántica, tarea, taxonomía y correspondencia binaria | El ranking se bloquea ante diferencias o metadatos ausentes. Todavía no todos los adapters declaran sus tareas, alcance del discretizador o procedencia de entrenamiento. |
| F04 / P0 | Revisiones e integridad: parcial | `Revision.save` y su QuerySet rechazan cambios y borrados por `save`, `update`, `bulk_update` y `delete`; revisión de lotes y correcciones de datos crean sucesores | Las escrituras SQL directas quedan fuera de la protección ORM. Revisar concurrencia/idempotencia de aceptación de lotes; faltan versiones de payload y migración explícita. |
| F05 / P0 | Recuperación operativa: parcial | Worker con estados persistidos, recuperación de locks obsoletos, reintentos y checkpoints cuando el adapter los declara; VAME nativo continúa desde checkpoints por época con pesos, optimizador y RNG | Los checkpoints dependen del modelo. VAME oficial no declara reanudación; la continuidad de entrenamiento no cubre interrupciones dentro de una época. |
| F06 / P1 | Reproducibilidad y persistencia: parcial | Fingerprints, semillas, referencias y versión del modelo; `FileArtifactStore` | Faltan manifiesto de entorno y versiones de todos los componentes, política de compatibilidad y codecs extensibles. Pickle exige origen confiable; no sirve como formato seguro de intercambio. |
| F07 / P1 | Carga y preparación visual: parcial | Revisiones separadas de datos, inventario H5/CSV, asociaciones versionadas de ROI/video/etiquetas, pasos codeless y vistas previas por sesión. El calibrador muestra el frame pose elegido y permite buscar visualmente el correspondiente en el video; calcula el offset y lo carga en el vínculo que crea la siguiente revisión. STORM también permite reemplazar por sesión los límites automáticos y exige una decisión versionada antes de preparar o ejecutar. | La tabla conserva una muestra de hasta 256 observaciones; la vista de pose usa bloques comprimidos por sesión y permite recorrer cualquier observación sin enviar el dataset completo al navegador. La sincronización consulta el mapeo de frame del adapter. Las revisiones antiguas requieren actualizar el inventario para generar el índice de navegación. |
| F08 / P1 | Modelos y extensiones: parcial | Catálogo de componentes y plugin RAINSTORM con DLC H5/CSV, preparación de pose, VAME y modelos supervisados | Los plugins se instalan y habilitan al iniciar Studio; el navegador no instala código. Faltan pruebas de conformidad reutilizables y documentación de recuperación para cada familia. |
| F09 / P1 | Corrección y aprendizaje asistido: parcial | `propose/review/annotate`, selección aleatoria/incertidumbre, `partial_fit` | Pool restringido a train, estrategias no registrables en catálogo, anotaciones no vinculadas explícitamente a nuevos targets. Incertidumbre llama `predict` sin comprobar antes esa capacidad. Corregir muestras existentes requiere reentrenar, no sumarlas otra vez. |
| F10 / P1 | Estudio visual: parcial | Tablas y gráficos de comparación, evidencia por frame, timeline de pose/estados, video sincronizado por frame/FPS, calibración manual del offset, superposición ROI compatible, referencia con taxonomía/máscara, predicciones de hasta cuatro corridas alineadas por fuente/observación/sesión/frame, anotaciones humanas de corridas verificadas por fingerprint, y filtros paginados para etiquetas, correcciones y anotaciones | El worker incorpora correcciones sólo a observaciones de entrenamiento sin reserva y conserva IDs aplicados/excluidos; las correcciones de evaluación permanecen aisladas. |
| F11 / P1 | Recuperación de sesión: parcial | `views.snapshot/revise` conserva sección y plan; restaura filtros, corridas seleccionadas, sesión, punto corporal y frame de pose; registra modelo adoptado | No restaura la posición de reproducción del video ni todo el estado del navegador. Restaurar una vista no adopta automáticamente un modelo ni lanza entrenamiento. |
| F12 / P1 | Reportes: parcial | Reporte congelado HTML/CSV/JSON y paquete ZIP conservan las corridas seleccionadas, planes, revisiones de datos, estado visual, anotaciones y archivos fuente/artefactos vinculados; la exportación verifica hashes y copia binarios por bloques sin deserializarlos | El ZIP transporta evidencia y archivos, pero todavía no restaura automáticamente el estudio en otro workspace ni incluye figuras compatibles. |
| F13 / P2 | Asistencia LLM: simulada | `AssistantProvider`, `LocalAssistant`, botón/toggle y propuesta persistida | No hay proveedor LLM real. Falta preferencia persistente y control servidor de habilitación, alcance de datos y ciclo de propuestas. El toggle visual no es una política de autorización. |
| F14 / P2 | Distribución y calidad UX: parcial | Wheels, pruebas Python y recorrido de navegador; interfaz clara | Faltan matriz real Linux/Windows/macOS, instalación limpia, respaldo/restauración, auditoría de accesibilidad y revisión de licencias/procedencia para distribución. No afirmar soporte validado en todos los entornos. |
| F15 / P1 | API y documentación: parcial | `Study`/`RunEngine` y recorrido nuevo `storm.suite` coexisten | Sus semánticas de entrenamiento/evaluación difieren. Documentar límites y preparar convergencia sin cambiar silenciosamente la API existente ni renombrar todo el motor. |

## Fronteras que se deben conservar

- El engine no depende de Django, vistas HTML, aplicaciones de dominio ni librerías científicas pesadas.
- Studio depende de contratos del motor; las extensiones registran implementaciones desde paquetes habilitados.
- Las vistas consumen resultados normalizados y alineación; no interpretan grupos como etiquetas semánticas automáticamente.
- Un plan experimental describe qué ejecutar. Un estado de estudio visual describe qué observar, comparar y revisar. Son revisiones distintas.
- Anotación humana, predicción del modelo, propuesta del asistente y decisión aprobada deben permanecer distinguibles.
- Las correcciones producen nuevos candidatos. La adopción siempre es explícita.

## Fuera del alcance de este cierre

RAINSTORM, DLC, pose, ROI y VAME ya se integran como plugin fuera del núcleo de
STORM; su estado específico está documentado en
`RAINSTORM/docs/reconstruction/README.md`. Tampoco se incluyen como requisitos
iniciales ejecución distribuida, SaaS multiusuario, un editor de redes neuronales,
AutoML, PDF/LaTeX ni proveedores comerciales concretos.

El despliegue actual es local y de un investigador. Exponerlo a una red requiere
otro análisis de autenticación, autorización, almacenamiento y aislamiento.
La extensión mediante plugins ejecuta código Python confiable; no es un sandbox.
