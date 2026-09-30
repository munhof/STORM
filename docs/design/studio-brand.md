# STORM Studio: guía de marca y experiencia

Estado: diseño propuesto, 2026-09-19. Esta guía define la futura interfaz;
no implica que exista un frontend implementado.

Ver los [mockups editables en SVG](studio-mockups.md) del editor, configuración
y resultados.

## Identidad

Nombre de trabajo: **STORM Studio**. Descriptor: «Construí, ejecutá y reutilizá
tus procesos». Tono claro, sereno y preciso. Priorizar tareas sobre terminología
interna: «Datos», «Preparar», «Modelos», «Métricas», «Resultados».

Usar STORM como marca textual. Un futuro símbolo puede representar tres piezas
conectadas; debe funcionar en un color y no reproducir identidad o iconos de
Orange. RAINSTORM puede aportar su logotipo y acento conservando controles y
estados. No cambiar el significado de colores según el plugin.

## Paleta clara

Superficies claras con texto oscuro. Los pasteles identifican categorías, no
reemplazan el contraste de controles ni del texto.

| Token | Color | Uso |
|---|---|---|
| `background` | `#F8FAFC` | fondo general |
| `surface` | `#FFFFFF` | tarjetas, paneles y formularios |
| `surface-muted` | `#F1F5F9` | áreas secundarias |
| `text` | `#0F172A` | títulos y cuerpo |
| `text-secondary` | `#475569` | ayuda y metadata |
| `divider` | `#CBD5E1` | separadores decorativos |
| `control-border` | `#64748B` | bordes necesarios para reconocer controles |
| `primary` | `#1D4ED8` | acciones principales y selección |
| `primary-soft` | `#DBEAFE` | selección de superficie |
| `focus` | `#1D4ED8` | anillo de foco con separación blanca |
| `success` / `success-soft` | `#166534` / `#DCFCE7` | completado |
| `warning` / `warning-soft` | `#92400E` / `#FEF3C7` | requiere atención |
| `error` / `error-soft` | `#B91C1C` / `#FEE2E2` | fallo o campo inválido |

Categorías de bloques: datos azul claro `#DBEAFE`, preparación verde claro
`#DCFCE7`, modelos violeta claro `#EDE9FE`, métricas ámbar claro `#FEF3C7`, vistas
cian claro `#CFFAFE`. Todas usan `text` oscuro, nombre e icono. Colorear solo la
franja de encabezado evita llenar el lienzo de superficies saturadas.

Botón primario: texto blanco sobre `primary`. Botones secundarios: texto oscuro
sobre blanco con borde visible. Una pieza seleccionada usa borde primario y
etiqueta; una pieza fallida muestra icono, «Falló» y mensaje además de rojo.

## Legibilidad y accesibilidad

Objetivo: WCAG 2.2 AA. Texto normal con contraste mínimo 4.5:1 y texto grande
3:1, conforme a [W3C: contraste mínimo](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).
Medir cada combinación realmente usada, incluidos hover, foco, selección y
errores; esta paleta es una especificación, no una certificación de una UI futura.

Contornos esenciales, handles y foco: objetivo mínimo 3:1 con el fondo. No usar
`divider` para el único borde de un input. No usar textos pastel sobre blanco.
Agregar pruebas automatizadas de contraste de tokens en la implementación.

- Tipografía del sistema: `system-ui, -apple-system, Segoe UI, sans-serif`.
  No descargar fuentes externas para trabajar offline.
- Cuerpo 16 px, ayuda 14 px, títulos 20–28 px; interlineado 1.5.
- Espaciado en múltiplos de 4 px; separación usual 8/16/24 px.
- Bordes de tarjeta 8 px, controles 6 px; sombras sutiles solo en paneles flotantes.
- Objetivos de interacción de 44 × 44 px cuando sea posible; ampliar área
  clicable de puertos aunque el dibujo sea pequeño.
- Respetar zoom 200 %, alto contraste del sistema y movimiento reducido.
- Etiquetas siempre visibles, errores asociados al campo y unidades explícitas.
- Foco ordenado, roles/nombres accesibles y anuncios de validación/ejecución.

Arrastrar nunca es el único mecanismo: ofrecer agregar, conectar y reordenar
con botones y teclado, más una vista de lista equivalente. El requisito de
alternativas a movimientos de arrastre está explicado por
[W3C](https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html).

## Piezas y conexiones

Una pieza muestra: título, categoría, estado, resumen de parámetros y entradas/
salidas etiquetadas. Evitar formularios completos dentro de cada tarjeta; se
editan en el inspector lateral. Encastres suaves indican entrada a izquierda y
salida a derecha. La forma refuerza compatibilidad, sin ser su única señal.

Al seleccionar una salida, resaltar destinos compatibles y explicar por qué un
destino no acepta la conexión. Si falta metadata, mostrar «compatibilidad por
validar». Nunca dibujar una conexión como válida solo porque sus puertos tienen
el mismo color. Conexiones seleccionadas deben distinguirse sin animación.

Estados con texto: «Sin configurar», «Listo», «Pendiente», «En ejecución»,
«Completado», «Falló», «Cancelación solicitada», «Cancelado». Estado de guardado
y de ejecución aparecen separados. Editar después de ejecutar muestra
«Resultados de revisión anterior».

## Pantallas y comportamiento

Dentro del estudio visual, usar las pestañas Flujo, Comparar, Etiquetas e
Historial, y un interruptor visible de asistencia LLM. Guardar/recuperar estado
son acciones del workspace; validar/ejecutar corresponden al plan seleccionado.
Ver [estudio visual y asistencia](visual-study.md).

1. **Inicio:** proyectos recientes, abrir/importar y dos plantillas breves.
2. **Editor:** catálogo a izquierda, lienzo central, inspector a derecha;
   acciones Guardar/Validar/Ejecutar siempre localizables.
3. **Resultados:** tabla comparativa de modelos y métricas, gráficos, artefactos
   y enlace a la revisión ejecutada. Mostrar que las métricas actuales son del
   conjunto de entrenamiento; no rotularlas «validación» sin separación real.
4. **Entorno:** plugins y runtimes disponibles; motivos de componentes bloqueados.

Desktop desde 1280 px: tres paneles. Entre 768–1279 px: catálogo/inspector
plegables. Pantallas menores: lista ordenada y formularios, con prioridad en
consultar resultados. No reducir indefinidamente las piezas para que entren.

Atajos propuestos: Ctrl/Cmd+S guarda, Ctrl/Cmd+Z deshace, Escape cierra o cancela
conexión, Tab recorre controles. No capturar teclas de edición dentro de inputs.
Eliminar permite deshacer e indica conexiones afectadas. Ejecutar siempre es
una acción explícita; el autoguardado no inicia trabajos.

Mensajes ejemplo:

- «Factor debe ser un número. Ejemplo: 2».
- «Esta métrica necesita objetivos; seleccioná una fuente que los incluya».
- «El modelo requiere un runtime que no está instalado».
- «Guardado localmente. Todavía no ejecutaste esta revisión».

El primer idioma será español con catálogo de traducciones preparado para
inglés. IDs de componentes, schemas y documentos no se traducen.

## Reutilización por RAINSTORM

Permitir configurar nombre, logo, acento, iconos propios, categorías y plantillas
a través de un `BrandProfile` propuesto. Limitarlo a tokens documentados, sin
CSS arbitrario ni fork. Los colores de error/éxito y controles accesibles son
comunes. Plantillas de pose y comportamiento se instalan con el plugin del
dominio y se reconocen como contenido RAINSTORM.

## Validación del diseño

Antes de implementar todo el producto, probar el wireframe con al menos tres
personas que no conozcan el código. Tareas: abrir ejemplo, cambiar un parámetro,
corregir una conexión inválida, ejecutar y recuperar resultados. Objetivo inicial:
al menos dos completan el flujo en menos de diez minutos sin ayuda del autor.
Registrar errores y ajustar etiquetas/orden, no solo colores.

La prueba técnica debe incluir teclado completo, lector de pantalla, zoom,
contraste y pantalla angosta. La UI deberá poder completar el ejemplo sin
arrastrar ningún elemento.

Ver [arquitectura propuesta](../architecture/visual-studio.md) y
[etapas de implementación](../planning/studio-roadmap.md).
