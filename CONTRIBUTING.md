# Protocolo de contribución STORM + RAINSTORM

1. Reproducir el problema y registrar evidencia/versiones.
2. Escribir la prueba de regresión mínima y comprobar que falla por la causa esperada.
3. Aplicar el cambio más pequeño que resuelva el problema, conservando compatibilidad.
4. Verificar la regresión nueva y las pruebas existentes relevantes.
5. Actualizar contrato, guía y evidencia de aceptación; identificar interfaces futuras como propuestas.
6. Cerrar el issue sólo cuando cumpla su aceptación y tenga evidencia enlazada.

STORM define contratos, validación, ejecución y trazabilidad; RAINSTORM define
carga científica, preparación de pose, modelos, recetas y controles específicos.
No cerrar tickets por documentación, navegación HTTP 200 o pruebas GPU sintéticas.
Conservar revisiones históricas, cambios locales y trabajos activos. Entrenamientos
finales y reinicios de workers requieren una tarea operativa aparte.
