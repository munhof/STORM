# STORM Studio: propuesta de configuración visual

> Documento histórico. El stack vigente es Django y templates HTML dentro de
> un monorepo. Ver [implementación actual y límites](../guides/django-suite.md).
> La propuesta React/FastAPI de esta página no es el plan de implementación activo.

Fecha: 2026-09-19. Estado: propuesta documentada, pendiente de implementación.
«STORM Studio» es un nombre de trabajo. Ninguna API o comando nuevo de esta
página está disponible todavía.

## Objetivo y decisión propuesta

La interfaz se organiza como un **estudio visual persistente** que contiene
planes, ejecuciones y herramientas de investigación. Comparar modelos, recuperar
estados, revisar etiquetas y activar asistencia LLM son capacidades de ese
espacio, no bloques ni campos del plan ejecutable.
Ver [diseño del estudio visual](../design/visual-study.md) para contratos,
persistencia y límites de asistencia.

Construir una aplicación web que permita configurar y ejecutar procesos de
STORM mediante bloques conectables, formularios y plantillas, sin escribir
Python para las tareas habituales. RAINSTORM reutilizará la aplicación mediante
un plugin de dominio: catálogo, validaciones, plantillas y marca. «Heredar» se
entiende como reutilizar por composición, sin bifurcar el editor.

La viabilidad es alta para un editor de configuración secuencial y moderada para
ejecución interactiva completa. La parte compleja es definir contratos de
componentes, preservar trazabilidad y gestionar procesos, no dibujar bloques.
Un navegador permite compartir la interfaz entre sistemas operativos; no vuelve
portables automáticamente los modelos, drivers ni runtimes científicos.

Se recomienda React + TypeScript + React Flow para el editor, y un servicio
Python opcional que use STORM. FastAPI es un candidato para ese servicio, sujeto
a una prueba de empaquetado. Son decisiones propuestas; no se añaden dependencias
en esta etapa. El núcleo Python continúa siendo utilizable sin UI o servidor.

## Base real inspeccionada

| Código actual | Qué permite | Brecha para Studio |
|---|---|---|
| `pipeline/spec.py` | `StepSpec`, `PipelineSpec`, JSON y fingerprint | no declara puertos, schemas de parámetros ni DAG |
| `pipeline/runner.py` | ejecución secuencial con contexto y trazas | no hay scheduler de grafos, jobs ni cancelación |
| `plugins/discovery.py` | importar un paquete habilitado y reflejar subclases | importar ejecuta código; no es un catálogo seguro para paquetes arbitrarios |
| `models/registry.py` | builders por nombre | no expone descriptores de formularios ni catálogo público |
| `studies/study.py` | loader y corridas secuenciales | entrenar/evaluar usan hoy el mismo dataset |
| `visualization/` | interfaces, registro y SVG clásicos | falta transporte de resultados y asociación con nodos |
| `pyproject.toml` | Python >=3.11, core sin dependencias | distribución opcional del servidor y frontend |

En el checkout hermano `RAINSTORM`, `pyproject.toml` requiere Python
`>=3.9,<3.10`; `src/rainstorm/backend`, `frontend` y `models` contienen solamente
sus `__init__.py` en el árbol inspeccionado. Por eso no se considera terminada
su integración con STORM ni se promete reutilizar ya un backend funcional.
Estos hechos deben revalidarse al iniciar la implementación.

La relación operativa entre estas piezas está documentada en
[Componentes reales en STORM Studio](../guides/studio-components.md). Esa guía
es la referencia para registrar código existente, cargar inputs y conectar
RAINSTORM; este documento conserva la arquitectura futura de la interfaz.

## Interacción: Orange más piezas encastrables

Tomar de Orange el catálogo de componentes, el lienzo y la inspección de
resultados. Tomar de los editores por bloques los encastres visibles y la ayuda
para combinar piezas compatibles. Orange documenta su composición visual por
widgets conectados en [Visual Programming](https://orangedatamining.com/home/visual-programming/).

El MVP ofrece un flujo ordenado de izquierda a derecha: fuente → preparación →
estudio → resultados. Cada grupo contiene piezas. El usuario puede arrastrarlas
o usar «Agregar después», «Mover antes» y «Conectar con…». Un encastre representa
un contrato, no solo proximidad geométrica. La compatibilidad se indica con
texto y forma, además de color.

No ejecutar al arrastrar, abrir ni modificar un parámetro. «Validar» revisa el
flujo y «Ejecutar» inicia una revisión concreta. Esto hace predecible el trabajo
cuando un bloque implica entrenamiento costoso.

```text
STORM Studio | Proyecto: Demo | Guardado | Validar | Ejecutar
────────────────────────────────────────────────────────────
Catálogo       Flujo                            Configuración
Buscar…        [Datos] → [Escalar] → [Estudio]    Escalar
Datos                                  ↓       Factor [2   ]
Preparación                         [Resultados] Ayuda y ejemplo
Modelos                                        Entrada: números
Métricas                                       Salida: números
Vistas
────────────────────────────────────────────────────────────
Problemas (0) | Ejecuciones | Resultados | Registro
```

Recorrido inicial:

1. Crear desde «Ejemplo numérico» o «Flujo vacío».
2. Elegir una fuente registrada; previsualizar una muestra acotada.
3. Agregar pasos; completar formularios con defaults, unidades y ayuda.
4. Elegir modelos, métricas y semillas dentro del bloque Estudio.
5. Validar; cada problema permite ir al campo o conexión responsable.
6. Ejecutar; ver estados y resultados asociados a esa revisión.
7. Exportar configuración, recuperar la corrida o duplicar el flujo.

La experiencia es codeless para componentes instalados y descritos. Agregar un
algoritmo nuevo sigue siendo una tarea de desarrollo del plugin.

## Arquitectura y responsabilidades

```text
Navegador: catálogo + lienzo + formularios + resultados
                         |
                    API de Studio
                         |
      catálogo de plugins / validación / compilación
                         |
             supervisor de jobs y workers
                         |
   PipelineRunner → PipelineDataLoader → Study / RunEngine
                         |
               ArtifactStore y resultados

RAINSTORM plugin → contratos de Studio y STORM
```

Organización candidata dentro del repositorio:

| Ubicación propuesta | Responsabilidad |
|---|---|
| `src/storm/composition/` | descriptores y compilación de configuraciones, sin HTTP ni React |
| `packages/storm-studio/` | servidor opcional, catálogo, proyectos, jobs y recursos web compilados |
| `web/studio/` | frontend TypeScript y tokens de diseño |
| repositorio RAINSTORM, paquete de integración | componentes de dominio, descriptores, plantillas y traducciones |

Primero probar que esta división se empaqueta y funciona offline. Una
distribución separada `storm-studio` dependería de `storm-traceable`; instalar
STORM no instalaría el servidor. El usuario final no necesitaría Node.js: se
incluirían los recursos compilados. Node.js se usaría para desarrollar el editor.

### Catálogo y reflexión

El backend descubre exclusivamente paquetes habilitados por el administrador.
La reflexión existente identifica pasos y visualizadores. Modelos y métricas
mantienen su registro explícito; un adaptador de catálogo une sus nombres con
descriptores públicos. No leer diccionarios privados de los registros desde la UI.

El flujo de modelos vigente es `ModelRegistry.register(name, builder)`. El builder
recibe una configuración JSON y devuelve un objeto con `fit` y `predict`; la
salida se normaliza a `ModelOutput`. El flujo de métricas vigente es
`MetricRegistry.register(name, metric)`, donde `evaluate` devuelve un escalar.
El catálogo futuro debe envolver estas operaciones y conservar una referencia
al registro de runtime, sin reemplazarlas por clases ficticias en el frontend.

Contrato propuesto `ComponentDescriptor`:

- `id` namespaced, `version`, `kind`, `runtime_name` y `provider`;
- título, descripción, categoría, documentación y etiquetas traducibles;
- `config_schema` en JSON Schema y hints de presentación separados;
- puertos de entrada/salida con tipo lógico, cardinalidad y obligatoriedad;
- capacidades: preview, ejecución, disponibilidad y runtime requerido;
- efectos conocidos: lectura, escritura, entrenamiento; referencias a recursos.

`inspect.signature` y anotaciones pueden sugerir campos simples, pero no inferir
de forma fiable unidades, rangos, dependencias entre campos o compatibilidad de
datos. El plugin aporta esa información explícita. Una clase descubierta sin
descriptor aparece como «requiere descriptor», no con un formulario inventado.

Ejemplo ilustrativo de descriptor, todavía no implementado:

```json
{
  "id": "storm.demo.scale",
  "version": "1",
  "kind": "step",
  "runtime_name": "scale",
  "provider": "storm.testing",
  "title": "Escalar valores",
  "config_schema": {
    "type": "object",
    "properties": {"factor": {"type": "number", "default": 1}},
    "required": ["factor"],
    "additionalProperties": false
  },
  "inputs": [{"name": "data", "type": "numeric-sequence", "required": true}],
  "outputs": [{"name": "data", "type": "numeric-sequence"}]
}
```

Los tipos lógicos requieren un registro versionado y reglas de compatibilidad
explícitas. No basta comparar nombres de clase ni asumir que `Any` garantiza
compatibilidad. El servidor valida configuración y el worker valida datos reales.

### Documento visual y compilación

Proponer `WorkflowDocument` versionado con `schema_version`, ID, revisión,
componentes y versiones requeridas, nodos, conexiones, referencias de datos y
parámetros. Guardar posiciones, zoom y paneles en una sección `presentation`.
Mover una pieza no debe cambiar la identidad del proceso ejecutable.

El compilador produce un plan compuesto por `PipelineSpec`, `StudySpec`,
`RunSpec`, `VisualizationSpec` y bindings de recursos y loaders registrados.
`PipelineSpec` no contiene callables; el servidor resuelve los bindings al crear
`PipelineDataLoader` y `Study`. Cada nodo mantiene un mapa hacia el paso, corrida
o vista que genera.

El MVP admite una sola cadena de preparación, una fuente y un estudio con uno
o más modelos. Métricas y vistas son configuración/salidas del estudio. Rechaza
ciclos, merges y bifurcaciones arbitrarias con un error claro. No convertir un
DAG general en una lista ignorando sus conexiones. Un scheduler DAG será otra
fase, con semántica propia de estado y artefactos entre nodos.

Ejemplo de compilación: «Datos demo → Escalar(factor=2) → Estudio(constante,
media; MAE/MSE) → Barras» produce un `StepSpec("scale", {"factor": 2})`, dos
`RunSpec`, las métricas `mae/mse` y `VisualizationSpec("metric_bar")`.

Separar tres identidades: revisión editable, hash del plan semántico y job ID.
Al ejecutar, congelar una copia del documento, plan, schemas, versiones de
plugins y fingerprints de datos; vincularlos con execution IDs de STORM.
Persistir también trazas de preparación: hoy su metadata en `Dataset` no implica
automáticamente su conservación en `RunRecord`. Un job manifest de Studio debe
registrarla explícitamente sin introducir posiciones gráficas en el core.

### API y ejecución propuestas

| Operación futura | Responsabilidad |
|---|---|
| `GET /api/v1/catalog` | componentes, schemas, disponibilidad y versiones |
| `POST /api/v1/workflows/validate` | problemas por nodo/campo y compatibilidad |
| `POST /api/v1/workflows/compile` | plan normalizado y hash; sin entrenar |
| `POST /api/v1/jobs` | ejecutar revisión validada con clave de idempotencia |
| `GET /api/v1/jobs/{id}` | estado, revisión, ejecución y resultados |
| `GET /api/v1/jobs/{id}/events` | eventos SSE; polling como alternativa |
| `POST /api/v1/jobs/{id}/cancel` | solicitar cancelación, no prometer interrupción inmediata |
| `GET /api/v1/artifacts/{id}` | consultar/descargar outputs autorizados |

Estados: queued, running, succeeded, failed, cancel_requested y cancelled.
El supervisor ejecuta Python fuera del hilo HTTP, al principio un job por
workspace. Desconectar el navegador no cancela el trabajo. Reabrir consulta el
job persistido; reiniciar el servidor marca como interrumpidos los workers cuya
supervivencia no pueda confirmarse. Las cancelaciones se verifican entre pasos;
interrumpir entrenamiento depende del adapter. No marcar un job como cancelled
hasta confirmar que terminó el worker. Reintentar crea un nuevo intento vinculado.

## Reutilización por RAINSTORM

El plugin RAINSTORM aportará lectores DLC, pasos de pose/ROI, adapters de modelos,
métricas y vistas propias, con sus schemas y descriptores. El editor común
renderiza esos descriptores sin condicionales `if rainstorm`. La marca se configura
con tokens y el nombre «RAINSTORM · powered by STORM»; las reglas de accesibilidad
y el significado de estados son compartidos.

Resolver antes la incompatibilidad de Python:

1. Preferencia: host STORM/Studio en Python >=3.11 y runtime científico legado
   en un entorno Python 3.9 separado, mediante un adapter de proceso.
2. Intercambiar referencias de archivos y manifiestos versionados, no objetos
   pickle entre intérpretes. Preservar pesos y comparar outputs de referencia.
3. Alternativa posterior: modernizar RAINSTORM y dependencias si tests de paridad
   demuestran que puede compartir entorno. No bajar el mínimo de STORM solo para
   intentar instalar ambos en el mismo entorno.

Herencia de UI y capacidad de ejecutar modelos son hitos diferentes. El plugin
puede mostrar componentes deshabilitados con el motivo «runtime no instalado».

## Entornos y distribución

| Entorno objetivo | Experiencia prevista | Condición |
|---|---|---|
| Windows, macOS, Linux desktop | navegador + servicio local | probar instalación/ejecución en cada SO |
| servidor Linux/headless | navegador remoto + backend Python | autenticación, TLS y workspace por usuario |
| notebook/Jupyter | enlace al mismo servicio | no exigir extensión de notebook |
| contenedor | backend y assets precompilados | montar datos; GPU solo si el runtime lo soporta |
| offline | editor local y plugins ya instalados | sin CDN, fuentes remotas ni descarga al abrir |
| tablet/teléfono | consulta y formularios; vista en lista | edición avanzada del lienzo no es prioridad MVP |
| navegador sin servidor | diseño/exportación futura | no ejecuta modelos Python nativos |

Objetivo de pruebas: Chrome/Edge y Firefox vigentes, Safari vigente en macOS;
validación manual de Safari además de pruebas automatizadas WebKit. «Todos los
entornos» se traduce en esta matriz verificable, no en soporte universal de
navegadores antiguos o de cada combinación GPU/modelo.

El servidor local se enlaza a loopback por defecto. La selección de datos usa
un selector/upload o recursos del workspace; el navegador no obtiene acceso
arbitrario al disco. Los documentos no pueden solicitar imports, comandos ni
instalación de paquetes. Solo se ejecutan componentes del catálogo habilitado.
Servir vistas HTML/SVG de plugins con aislamiento y política de contenido, y
cargar pickle únicamente desde el store confiable. La exposición remota requiere
un hito adicional de autenticación, autorización y límites de recursos.

## Alternativas evaluadas

| Opción | Ajuste a la necesidad | Decisión propuesta |
|---|---|---|
| React Flow | nodos personalizables, handles y soporte de teclado | preferida para lienzo y piezas con encastres |
| Blockly | excelente metáfora de rompecabezas y bloques | alternativa si el prototipo demuestra que la sintaxis por bloques funciona mejor |
| adoptar Orange completo | interacción cercana al objetivo | referencia de UX; no base del framework genérico |
| UI de formularios Python | rápida para validar parámetros | útil como alternativa de lista, insuficiente por sí sola para el lienzo solicitado |
| Python científico en navegador | evita servidor en demos pequeñas | no base para modelos nativos y runtimes heterogéneos |

React Flow documenta [nodos personalizados](https://reactflow.dev/learn/customization/custom-nodes)
y [accesibilidad](https://reactflow.dev/learn/advanced-use/accessibility), y declara
licencia MIT en [ProOptions](https://reactflow.dev/api-reference/types/pro-options).
Eso sustenta la recomendación, pero la accesibilidad de nuestro editor se prueba
por separado. Blockly describe su biblioteca de programación por bloques en
[su documentación](https://docs.blockly.com/guides/app-integration/attribution/).

## Licencias y procedencia

Mantener LGPL-3.0-or-later para el código propio de STORM/Studio. Orange3 declara
GPL-3.0-or-later en su [licencia](https://github.com/biolab/orange3/blob/master/LICENSE);
se usa como referencia de interacción, sin incorporar código, iconos o marca.
Al elegir dependencias, fijar versiones, revisar sus archivos LICENSE/NOTICE y
generar inventario de terceros. La licencia de Blockly se revisa en su
[repositorio oficial](https://github.com/RaspberryPiFoundation/blockly/blob/main/LICENSE),
no se deduce de la licencia del sitio de documentación. No se incorpora código
externo ni se cambia la licencia con este plan.

Continuar con la [guía de marca y UX](../design/studio-brand.md) y el
[plan de implementación](../planning/studio-roadmap.md).
