# Estudio visual: espacio de trabajo de investigación

Estado: diseño propuesto. Amplía Studio más allá de configurar un flujo.
Las capacidades descritas todavía no están implementadas.

La guía [Componentes reales en STORM Studio](../guides/studio-components.md)
explica cómo estas vistas se conectan hoy con `ModelRegistry`, `DataLoader`,
`MetricRegistry`, `ArtifactStore`, `Study` y `RunEngine`.

## Estudio visual y plan de estudio

El **estudio visual** es el espacio persistente donde una persona explora datos,
compara modelos, revisa etiquetas, recupera estados y recibe asistencia opcional.
Puede reunir múltiples planes, revisiones y ejecuciones.
El **plan de estudio** es la configuración ejecutable: datos, pasos, modelos,
métricas y semillas. Corresponde a los specs y al plan compilado ya propuestos.

| Estudio visual | Plan de estudio |
|---|---|
| comparaciones guardadas, filtros y selección de modelos | configuraciones de modelos a ejecutar |
| historial, vistas y puntos de recuperación | revisión concreta de pasos y parámetros |
| etiquetas y sugerencias pendientes de revisión | referencia a una versión aceptada de etiquetas si se usa |
| preferencias y actividad del asistente LLM | no contiene conversaciones ni el interruptor del asistente |
| referencias a resultados y checkpoints | bindings explícitos de los recursos necesarios |

Cambiar filtros, abrir un modelo o activar asistencia no modifica un spec ni su
fingerprint. Si una sugerencia cambia datos, etiquetas o parámetros, se muestra
la diferencia y se crea una nueva revisión únicamente al aplicarla. Ningún
resultado histórico se recalcula o reinterpreta silenciosamente.

## Navegación

Dentro de cada estudio visual: **Flujo · Comparar · Etiquetas · Historial**.
La ayuda LLM es un panel lateral opcional disponible en esas vistas.
«Guardar estado» y «Recuperar estado» son acciones del espacio de trabajo.
«Validar plan» y «Ejecutar plan» pertenecen al editor del plan seleccionado.
No crear bloques del pipeline para representar estas herramientas de interfaz.

![Estudio visual editable](../assets/mockups/estudio-visual.svg)

[Descargar SVG editable](../assets/mockups/estudio-visual.svg).
Los paneles reunidos en el mockup muestran las capacidades del espacio; cada
pestaña tendrá una vista ampliada en el prototipo.

## Inspección de datos crudos y clasificación

En modelos de comportamiento no alcanza con mostrar una métrica o un histograma
de predicciones. La persona debe poder comprobar qué sucede en la observación
original cuando el modelo cambia de clase. El estudio visual tendrá una vista de
**evidencia temporal** inspirada en el visualizador de RAINSTORM:

- panel de datos crudos: frame/video, trayectoria, coordenadas, variables o
  señales originales, según lo que entregue el plugin;
- timeline sincronizado con índice de muestra, frame o timestamp;
- bandas de clasificación predicha, etiqueta humana, etiqueta geométrica y
  segmentos sin etiqueta, con colores y leyenda configurables;
- cursor común y reproducción/avance paso a paso para inspeccionar una muestra;
- selección de un intervalo que actualiza la tabla de valores, la predicción,
  la confianza si el modelo la declara y los artefactos de origen;
- comparación de dos modelos sobre exactamente el mismo intervalo, mostrando
  desacuerdos y transiciones;
- filtros por clase, sujeto, sesión, región o canal únicamente cuando el
  plugin declare esos campos.

La vista debe dejar visible la relación:

```text
datos crudos → preparación registrada → observación/índice temporal
            → predicción y etiqueta → decisión humana / métrica
```

La **alineación temporal** es obligatoria para pintar una clasificación sobre
datos crudos. Si el adapter no puede demostrar cómo relacionar una predicción
con frame, timestamp o índice de observación, Studio muestra la predicción como
tabla no alineada y no la dibuja sobre el video o la trayectoria. También debe
mostrar pérdidas, padding, ventanas y downsampling introducidos por la
preparación; nunca desplazar silenciosamente una etiqueta para que parezca
coincidir.

STORM aporta el contrato neutral de visualización y las referencias trazables;
RAINSTORM aporta el lector de video/pose, la semántica conductual y el renderer
específico. El core no incorpora DLC, ROI ni alineación animal. La extensión
puede implementar, por ejemplo:

```python
from storm import Visualization, VisualizationRequest, VisualizationResult


class TemporalClassificationVisualization(Visualization):
    visualization_type = "temporal_classification_overlay"

    def render(self, request: VisualizationRequest) -> VisualizationResult:
        # request.data: vista cruda + índice temporal declarado por el plugin
        # request.output: ModelOutput con predicciones alineadas
        # request.metadata: clases, selección, colores y referencias de origen
        svg = render_raw_with_labels(
            raw=request.data,
            output=request.output,
            metadata=request.metadata,
        )
        return VisualizationResult(
            content=svg,
            media_type="image/svg+xml",
            metadata={"alignment": "frame", "source": "rainstorm"},
        )
```

El visualizador no modifica `ModelOutput`, etiquetas ni el plan. Al aceptar una
corrección desde el timeline se crea una nueva `AnnotationRevision`, conservando
la predicción y la evidencia que originaron la decisión. Una etiqueta sugerida
por LLM se muestra como capa separada y nunca se confunde con la clasificación
del modelo evaluado.

![Datos crudos y clasificación alineada](../assets/mockups/raw-classification.svg)

[Descargar mockup editable](../assets/mockups/raw-classification.svg).

## Comparar modelos y ejecuciones

Seleccionar corridas existentes, incluso de distintos planes, y guardar la
comparación: tabla de métricas, diferencias de configuración, semillas,
predicciones, tiempos y artefactos disponibles. Permitir inspección lado a lado
de una misma muestra y selección de la dirección de cada métrica.

La comparación debe poder abrir esta vista de evidencia para dos modelos: mismo
dato crudo, mismo intervalo, dos bandas de clasificación y una capa de
desacuerdos. No es suficiente comparar sus valores agregados.

Antes de ordenar un ranking, verificar identidad/versión de datos, partición,
versión de etiquetas y definición de métricas. Advertir diferencias y bloquear
la presentación como ranking comparable cuando esas condiciones no coincidan.
Se permite una inspección exploratoria explícitamente marcada. Métrica ausente
se muestra «No disponible», no cero. No promediar semillas ni normalizar métricas
automáticamente; cada agregación debe declarar su criterio.

«Abrir modelo» recupera el artefacto existente. «Usar en nuevo plan» genera una
propuesta de revisión; no vuelve a entrenar ni promete inferencia preentrenada
en el engine actual. Esa capacidad requiere su adapter y contrato.

## Recuperar estados

Separar tres acciones para evitar ambigüedad:

1. **Recuperar estado visual:** reabrir una revisión del workspace con sus
   planes seleccionados, filtros, comparación, etiquetas y paneles.
2. **Recuperar resultados/modelo:** cargar artefactos persistidos y consultar
   la ejecución que los produjo.
3. **Reanudar ejecución desde checkpoint:** solo cuando el runtime/adaptador
   declare que puede hacerlo y valide checkpoint, datos y versiones.

Guardar snapshots nombrados e historial automático con fecha y autor. Antes de
restaurar, mostrar diferencias y guardar el estado actual. Restaurar crea una
nueva revisión basada en el snapshot, sin borrar el historial ni cambiar jobs
en curso. Referencias perdidas o plugins ausentes se muestran como no disponibles.
Un snapshot guarda referencias, no duplica modelos ni conexiones vivas.

## Asistencia de etiquetas

Herramienta general para revisar anotaciones de muestras, intervalos u otros
elementos identificables. STORM aporta contratos de entidades, taxonomía,
versionado y revisión; el plugin de dominio define cómo visualizar y seleccionar
una entidad. RAINSTORM puede aportar frames/segmentos, video y vocabulario
conductual sin introducirlos en el núcleo.

La vista ofrece taxonomía, etiqueta actual, candidatos, fuente de la sugerencia,
evidencia disponible, estado y acciones Aceptar/Editar/Rechazar. Incluir filtros
por pendientes, desacuerdo y ausencia de etiqueta. Aceptación masiva exige
selección explícita y resumen del cambio.

Las sugerencias pueden provenir de reglas, modelos, importaciones o LLM.
La asistencia de etiquetas **sigue funcionando sin LLM**. Las sugerencias no son
ground truth: guardar origen, versión del modelo/regla, fecha, revisión de datos
y decisión humana. Una confianza numérica se muestra solo si el proveedor
define su significado; no inventar probabilidades para respuestas de un LLM.

Una edición aceptada crea una nueva versión de anotaciones. Usarla en entrenamiento
o evaluación exige vincular esa versión en una nueva revisión del plan. Mantener
separadas etiquetas humanas y propuestas del modelo para evitar evaluar un
modelo contra sus propias sugerencias sin advertirlo.

## Asistencia LLM opcional

Interruptor visible **«Asistencia LLM: desactivada / activada»**, apagado por
defecto por estudio visual. Al activarlo, elegir proveedor/runtime disponible,
modelo y alcance: explicar un bloque, ayudar a configurar, resumir una comparación
o proponer etiquetas. No seleccionar proveedor comercial ni agregar SDK al core
en esta etapa.

Mostrar qué contexto se enviará antes de cada solicitud: por defecto metadatos,
schemas y resúmenes seleccionados. Enviar muestras, imágenes o anotaciones a un
proveedor remoto requiere selección explícita. Credenciales en la configuración
del servicio, nunca en el documento exportado. El modo local y remoto deben
identificarse en el panel.

El asistente devuelve propuestas revisables con procedencia. No puede ejecutar
planes, instalar plugins, aceptar etiquetas ni cambiar parámetros por sí solo.
Desactivarlo impide solicitudes nuevas y cancela las pendientes cuando sea
posible; ignora respuestas tardías. Aclarar que una solicitud remota ya enviada
no puede deshacerse. Mantener sugerencias previas con su origen y permitir
borrarlas; no borrar etiquetas ya aceptadas.

Estados adicionales: no configurado, no disponible y error, con mensajes
específicos. Una caída del proveedor no debe bloquear edición manual, comparación
ni recuperación. Si un LLM es el modelo científico que se evalúa, ese modelo sí
se declara en el plan: es independiente del asistente de interfaz.

## Contratos y persistencia propuestos

Objetos de Studio, no nuevos campos obligatorios de `StudySpec`:

- `VisualStudyDocument`: ID, versión de schema, revisión, planes y ejecuciones
  referenciadas, comparaciones, versiones de anotaciones y preferencias.
- `WorkspaceSnapshot`: revisión base, autor, fecha y referencias necesarias
  para recuperar el estado visual.
- `ComparisonView`: ejecuciones, métricas, filtros y comprobaciones de
  comparabilidad.
- `AnnotationRevision`: entidades identificadas, taxonomía y cambios aceptados.
- `AssistanceProposal`: proveedor, contexto referenciado, propuesta, procedencia
  y decisión humana; almacenamiento/retención del contenido configurables.
- `AssistantProvider`: contrato opcional para solicitar propuestas y cancelar,
  sin acoplar la UI a un proveedor concreto.

Persistir mediante repositorios de Studio y referencias a `ArtifactStore`.
El documento de workflow queda dentro de una revisión de plan referenciada por
el estudio visual. Un evento aceptado que afecte al experimento crea una revisión
de plan/datos; un cambio de interfaz solo crea una revisión de workspace.

## Criterios de aceptación de la futura implementación

1. Comparar dos corridas compatibles y recuperar sus modelos sin reentrenarlas.
2. Identificar comparación no equivalente y métricas ausentes.
3. Restaurar un snapshot preservando revisión actual, historial y jobs activos.
4. Distinguir recuperación de artefactos de reanudación de entrenamiento.
5. Revisar etiquetas con LLM apagado y conservar el origen de cada sugerencia.
6. Cambiar etiquetas aceptadas sin alterar evaluaciones históricas.
7. Apagar LLM durante una solicitud e impedir que una respuesta tardía se aplique.
8. Filtrar/comparar/activar ayuda sin cambiar el hash del plan.
9. Aplicar una sugerencia experimental solo tras revisar el cambio, generando una
   nueva revisión.
10. Reutilizar contratos y pantallas con un plugin RAINSTORM, sin bifurcar Studio.
11. Abrir una predicción desde una comparación y localizarla en el dato crudo,
    con alineación temporal verificable y sin cambiar el resultado almacenado.
