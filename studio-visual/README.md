# STORM Studio Visual

**Archivo histórico del mockup.** El paquete Python se trasladó a
`packages/storm-visualization`; la aplicación funcional está en
`packages/storm-studio`. Usar los comandos del README raíz para abrir Django.
Los comandos de desarrollo antiguos que siguen abajo se conservan como
referencia del prototipo y no describen el workspace actual.

Primer subproyecto de la visualización temporal de STORM. Provee una vista SVG
portable que relaciona una serie cruda con la salida de clasificación de un
modelo. No lee DLC, video ni pose: esos datos deben llegar mediante un adaptador
de dominio, por ejemplo RAINSTORM.

## Desarrollo

Desde el repositorio padre:

```bash
PYTHONPATH=src:studio-visual/src \
  uv run --project studio-visual --extra test pytest studio-visual/tests -q
```

El paquete implementa `Visualization` de STORM y puede registrarse mediante el
registro explícito o el descubrimiento del paquete:

```python
from storm import VisualizationRegistry

registry = VisualizationRegistry()
registry.discover("storm_studio_visual")
```

El contrato de entrada es `TemporalTrace`; `TemporalClassificationVisualization`
rechaza salidas cuyo número de predicciones no coincide con las observaciones.

La demo genera un SVG inspeccionable:

```bash
PYTHONPATH=src:../src uv run --project studio-visual python studio-visual/examples/demo.py
```

## Demo de página web

La maqueta web está en `web-demo/index.html`. No necesita instalar nada ni
construir JavaScript:

```bash
cd studio-visual
python -m http.server 8000 --directory web-demo
```

Abrí `http://localhost:8000` para probar el selector de modelo, el cursor del
timeline, la reproducción y la comparación sobre el mismo dato crudo.

La demo contiene las pestañas del estudio visual: **Flujo**, **Comparar**,
**Etiquetas**, **Evidencia** e **Historial**. Son mockups navegables con datos
ficticios; todavía no ejecutan planes ni escriben revisiones en `ArtifactStore`.
