# Plan incremental de STORM Studio

> Plan histórico, reemplazado por el monorepo genérico con Django. No se incluye
> integrar aplicaciones de dominio. Ver [estado de implementación](../guides/django-suite.md).
> Los pendientes actuales se organizan en el [plan de cierre vigente](suite-completion.md).

Fecha: 2026-09-19. Estado: plan; no hay implementación de Studio todavía.
Este documento acompaña la [evaluación técnica](../architecture/visual-studio.md)
y la [guía de marca](../design/studio-brand.md).
La relación entre las pantallas y las APIs que ya existen en STORM está
documentada en [Componentes e integraciones en Studio](../guides/studio-components.md).

## Alcance del primer producto

Un usuario instala Studio, abre un navegador y construye un flujo numérico:
datos demo → identidad/escalado → comparación de constante/media → MAE/MSE →
barras. Puede guardarlo, exportarlo, ejecutarlo y recuperar los resultados.
Los componentes dummy existentes permiten completar este recorrido sin un
runtime científico adicional.

El MVP usa una cadena de preparación y un estudio. Deja para etapas siguientes:
DAG arbitrarios, loops, condicionales, edición colaborativa, marketplace,
ejecución distribuida, entrenamiento reactivo y ejecución científica en WASM.

## Etapas, entregables y criterio de salida

Las estimaciones son orientativas para una persona con experiencia Python/web,
sin fechas comprometidas ni esperas externas. Cada etapa termina con una demo
verificable. Las pruebas de comportamiento preceden a cambios del runtime.

| Etapa | Trabajo y entregables | Criterio de salida | Esfuerzo orientativo |
|---|---|---|---|
| 0. Viabilidad y prototipo | probar React Flow con piezas, formularios y lista; comparar un caso en Blockly; probar distribución local offline; inventario de licencias | elegir librería con prueba de teclado y wireframe probado por usuarios | 3–5 días |
| 1. Descriptores y compilador | contrato versionado, catálogo público que envuelva `StepRegistry`, `VisualizationRegistry`, `ModelRegistry` y `MetricRegistry`, schemas, validación y mapeo a specs existentes | un JSON válido compila; ciclos/puertos/configs inválidos fallan antes de ejecutar; round-trip conserva semántica; listar no instancia modelos | 5–8 días |
| 2. Editor de configuración | catálogo, inspector, encastres, undo/redo, guardar/importar/exportar, vista de lista, plantillas dummy, wizard de adapter de modelo y creador de conector de datos | crear el ejemplo sin código; registrar un builder explícito y un `DataLoader` mediante revisión humana; exportar una configuración reproducible | 7–10 días |
| 3. Ejecución local | API, worker, estados persistidos, trazas, resultados y descarga SVG; snapshots, vínculo con `ArtifactStore`, recuperación de `RunResult` y comparación | ejecutar desde UI y Python produce resultados equivalentes; recargar navegador recupera job/resultados; no se confunde recuperación de artefacto con reanudación de checkpoint | 7–10 días |
| 4. Distribución y accesibilidad | assets incluidos, instalación por SO, matriz de navegadores, smoke tests offline y contraste | instalación limpia en Windows/macOS/Linux; no requiere Node al usuario; tareas accesibles | 4–7 días |
| 5. Plugin RAINSTORM | inventariar implementación real, descriptor de un flujo de dominio, runtime aislado si necesario, marca y plantilla | mismo editor ejecuta un caso RAINSTORM y un caso genérico; paridad con resultados de referencia | 7–12 días tras disponer del backend |

MVP STORM: etapas 0–4, aproximadamente 26–40 días de trabajo. La etapa 5 depende
de la recuperación/integración efectiva del backend RAINSTORM y puede requerir
trabajo adicional de duración todavía desconocida. Reestimar después del
prototipo y de los contratos, antes de comprometer calendario.

## Primer corte concreto a implementar

Tras cerrar la etapa 0, crear un descriptor del `ScaleStep` y un documento que
lo componga con una fuente numérica registrada. Compilarlo a `PipelineSpec` y
ejecutarlo con `PipelineRunner`. Implementar una pantalla mínima con catálogo,
tarjeta y campo «Factor», validación y exportación/importación.

Pruebas de aceptación:

1. Configurar factor 2 con datos `[1, 2, 3]` produce `[2, 4, 6]`.
2. Texto en factor devuelve error de campo; no ejecuta.
3. Guardar/reabrir mantiene configuración; mover tarjeta no cambia hash semántico.
4. Clase de paso descubierta con descriptor aparece sin editar el frontend.
5. Paquete o componente no habilitado no se importa por solicitud del navegador.
6. Usuario agrega/configura la pieza con teclado y con botones, sin arrastrar.
7. El core sigue importándose sin dependencias web.

Este corte demuestra la interfaz de configuración antes de sumar jobs,
entrenamiento o plugins científicos. No exige alterar firmas actuales de los
modelos, métricas o pasos. Los schemas se pueden añadir mediante adaptadores.

## Pruebas por capa

- **Contratos Python:** schemas válidos/incorrectos, IDs duplicados, versiones
  incompatibles, plugins ausentes, compilación determinista y specs exportados.
- **Semántica:** orden de pasos, rechazo de grafos no soportados, targets
  preservados, flujo dummy visual equivalente al flujo Python.
- **API/workers:** doble click idempotente, fallo de paso, desconexión del
  cliente, reinicio del servicio, cancelación, limpieza de procesos y artefactos.
- **Frontend:** formularios, conexiones, undo/redo, importación inválida, revisión
  modificada y resultados anteriores; tests de componentes y recorrido E2E.
- **Accesibilidad:** contraste de tokens, teclado sin mouse, lectura de errores,
  zoom 200 %, vista de lista y chequeo manual con lector de pantalla.
- **Portabilidad:** instalar wheel con recursos web incluidos en tres SO;
  Chrome/Edge/Firefox y Safari; desconectar red tras instalar y repetir demo.
- **RAINSTORM:** fixtures pequeñas de referencia, equivalencia numérica con
  tolerancias justificadas, pesos intactos y tests del contrato entre runtimes.

No basta que una figura se dibuje: debe poder rastrearse a documento, nodo,
datos, plugin, job y ejecución STORM. La prueba de exportación/importación
comprueba configuración y resultados, no solo posiciones de tarjetas.

## Riesgos y respuestas

| Riesgo comprobado o esperado | Respuesta | Hito que lo verifica |
|---|---|---|
| runner secuencial presentado como DAG | bloquear topologías no soportadas y mostrar mensaje | 1 |
| reflexión incompleta para formularios | schemas y descriptores explícitos, sin instanciar modelos al listar | 1 |
| entrenamiento bloquea HTTP | worker supervisado y estado durable | 3 |
| pérdida de trazas de preparación | job manifest guarda plan, bindings, trazas y artefactos | 3 |
| datos grandes copiados al navegador | muestras limitadas y referencias a recursos | 3 |
| formatos/runtimes de RAINSTORM incompatibles | host moderno y adapter de proceso versionado | 5 |
| UI fragmentada por aplicación | plugins y tokens; una sola aplicación Studio | 5 |
| confundir métricas de entrenamiento con validación | etiquetas explícitas; split real en un corte posterior | 2–3 |
| dependencia de internet durante uso | assets locales y catálogo ya instalado | 4 |

## Ampliación: herramientas del estudio visual

Estas capacidades pertenecen al producto Studio, no al contenido de
`StudySpec`. La estimación anterior cubre solo el MVP original y deberá
ampliarse con los siguientes cortes:

| Corte adicional | Dependencia | Entregable verificable |
|---|---|---|
| Comparación y snapshots | catálogo y persistencia de ejecuciones | comparar corridas existentes y recuperar una revisión sin reentrenar |
| Etiquetado asistido | entidades y taxonomía del plugin | revisar propuestas con procedencia y versionar decisiones humanas |
| Asistencia LLM opt-in | contratos de propuestas y permisos de contexto | activar/desactivar ayuda sin cambiar el plan; ignorar respuestas tardías |
| Reanudación desde checkpoints | capacidad declarada por cada adapter | recuperar estado de cómputo con paridad y compatibilidad verificadas |

Los criterios detallados están en el [diseño del estudio visual](../design/visual-study.md).
El primer corte de comparación/snapshots es parte del workspace base; asistencia
de etiquetas y proveedores LLM se desarrollan sobre esa base. El asistente LLM
es opcional, no una dependencia para completar el flujo manual.

## Hipótesis de implementación

Avanzar con estas hipótesis para el prototipo: nombre STORM Studio, español,
tema claro, uso local individual, React Flow, backend opcional Python y bloques
secuenciales. Validar las hipótesis con la etapa 0. La UI remota multiusuario,
la evolución a DAG y la modernización del runtime científico de RAINSTORM se
deciden con evidencia de uso y pruebas de compatibilidad.

No se requiere decidir ahora proveedor cloud, sistema de cuentas ni base de
datos distribuida. Para el MVP, un workspace local con documentos y estado de
jobs durable es suficiente; definir atomicidad e índices antes de implementarlo.

## Definición de terminado

La fase documental queda terminada con evaluación, guía de marca, contratos
propuestos, matriz de entornos y este plan navegables desde la documentación.
El futuro MVP queda terminado cuando una instalación limpia permite completar
el flujo dummy, recuperar una ejecución, exportar/importar y operar con teclado,
y sus checks de distribución y compatibilidad pasan. La herencia por RAINSTORM
se considera terminada solo al ejecutar su fixture de dominio con paridad.
