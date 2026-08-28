# Integraciones

Una integración conecta un dominio o biblioteca externa con contratos pequeños
de STORM. El framework no debe importar el paquete integrado.

## Capas de una integración

```text
paquete consumidor
  |
  +-- DataLoader --------> DataRef -> Dataset
  +-- PipelineStep ------> PipelineContext -> PipelineContext
  +-- ModelBuilder ------> config -> ModelAdapter
  +-- Metric ------------> Dataset + ModelOutput -> escalar
  +-- Visualization -----> request -> VisualizationResult
  +-- Seeder ------------> seed -> backend externo
  `-- ArtifactStore -----> persistencia opcional especializada

STORM solo conoce los contratos de la derecha.
```

## 1. Integrar datos

```python
class DomainDataLoader:
    def __init__(self, repository, preprocessing):
        self.repository = repository
        self.preprocessing = preprocessing

    def __call__(self, reference):
        raw = self.repository.load(reference.identifier)
        inputs, targets = self.preprocessing(raw)
        return Dataset(
            inputs=inputs,
            targets=targets,
            metadata={"source": reference.identifier},
        )
```

El loader traduce formatos y decisiones del dominio. `StudySpec` solo conserva
el `DataRef`.

La lectura y las transformaciones pueden separarse. El loader lee el formato
externo; los pasos se descubren desde un paquete de la integración:

```python
steps = StepRegistry()
steps.discover("my_integration.steps")
runner = PipelineRunner.from_spec(pipeline_spec, registry=steps)
data_loader = PipelineDataLoader(loader=raw_loader, runner=runner)
```

STORM conoce el orden, configuración y traza, pero no interpreta la operación.

## 2. Integrar un modelo

```python
class LibraryModelAdapter:
    def __init__(self, backend):
        self.backend = backend

    def fit(self, inputs, targets=None):
        self.backend.train(inputs, targets)
        return self

    def predict(self, inputs):
        result = self.backend.run(inputs)
        return ModelOutput(
            predictions=result.values,
            metadata={"native_metadata": result.metadata},
        )
```

```python
def build_library_model(config):
    from optional_library import Backend

    return LibraryModelAdapter(Backend(**config))
```

```python
models.register("library_model", build_library_model)
```

El import local permite que instalar STORM no instale la dependencia externa.

## 3. Integrar métricas

Las métricas del dominio deben vivir junto a la integración:

```python
class DomainAgreement:
    def evaluate(self, *, dataset, output, model):
        return domain_agreement(dataset.targets, output.predictions)
```

No deben agregarse al núcleo solo porque varios experimentos del mismo dominio
las utilizan.

## 4. Integrar reproducibilidad

```python
def seed_library(seed):
    from optional_library import set_seed

    set_seed(seed)
```

```python
study = Study(..., seeders=(seed_library,))
```

Documente cualquier flag adicional necesario para determinismo.

## 5. Composition root

Centralice el wiring en el consumidor:

```python
def build_runtime(artifact_root):
    models = ModelRegistry()
    models.register("library_model", build_library_model)

    metrics = MetricRegistry()
    metrics.register("domain_agreement", DomainAgreement())

    return {
        "models": models,
        "metrics": metrics,
        "artifacts": FileArtifactStore(artifact_root),
        "seeders": (seed_library,),
    }
```

El spec permanece independiente de estas instancias.

## 6. Integrar visualizaciones

```python
visualizations = VisualizationRegistry()
visualizations.discover("my_integration.visualizations")
manager = VisualizationManager(visualizations)
```

Una vista de embeddings, parámetros internos o diagnósticos de un backend debe
vivir con ese adapter. Las vistas generales pueden reutilizar los SVG clásicos
de `storm.visualization.classic`.

## Integración de RAINSTORM

La frontera recomendada es:

```text
storm
  ^
  |
rainstorm.integration.storm
  +-- RainstormDataLoader
  +-- steps de preparación sobre PipelineContext
  +-- adaptadores ModelOutput actual -> storm.ModelOutput
  +-- builders de modelos nativos
  +-- loaders/checkpoints específicos
  +-- métricas conductuales
  +-- visualizaciones de pose, ROI y comportamiento
  +-- seeders NumPy/PyTorch
  `-- compatibilidad temporal de artefactos
  ^
  |
VAME / Keypoint-MoSeq / otros backends
```

Debe permanecer en RAINSTORM:

- `StudyDataSpec` con variantes pose/ROI;
- preparación de pose, ventanas y features contextuales;
- conectores de tracking/video;
- VAME y Keypoint-MoSeq, oficiales o nativos;
- labels humanos, syllables y métricas conductuales;
- reporting, figuras y tablas de la tesis.

Puede migrarse al mecanismo de STORM sin mover la lógica concreta:

- clases step que hereden `PipelineStep` y declaren `step_type`/`version`;
- configs antiguas `{step, params}` convertidas a `StepSpec`;
- paquetes de pasos habilitados con `StepRegistry.discover()`;
- plots genéricos reescritos como `Visualization`, eliminando el tema y el
  vocabulario de RAINSTORM cuando corresponda.

Puede adaptarse casi directamente:

- builders explícitos del `ModelRegistry`;
- configuración JSON-compatible de cada corrida;
- estrategias genéricas, después de estabilizar su API;
- identificación de seed y tags;
- concepto de referencias a checkpoints y resultados.

Requiere refactorización:

- `BehaviorModel` a wrapper del contrato `Model`;
- `ModelOutput.results` a `ModelOutput.predictions`;
- `ExperimentContext` a `Dataset` construido por el loader;
- `ArtifactManager` a un adapter de `ArtifactStore`;
- `StudyEngine` a composition root que construye `Study`;
- loaders preentrenados a registros externos.

## Modelos preentrenados

El corte actual no tiene una fase `infer` ni un `PretrainedModelLoader`. Una
integración no debe afirmar que STORM soporta ese flujo de forma nativa.

Hasta agregar el contrato correspondiente, la aplicación puede recuperar un
modelo ya producido mediante `RunResult.load_model()` y llamar `predict()` fuera
de `Study`. Para checkpoints externos debe conservar su loader en la aplicación.

## Dependencias opcionales

En el paquete consumidor:

```toml
[project.optional-dependencies]
library-model = ["optional-library>=1.2"]
```

No agregue la dependencia al runtime de STORM.

## Licencias

Antes de distribuir un adapter:

1. revise la licencia de la versión concreta del backend;
2. distinga importar una dependencia de copiar su código;
3. preserve notices de cualquier fragmento incorporado;
4. documente incompatibilidades o restricciones;
5. pruebe que el extra opcional no se instala con el core.

## Checklist de integración

- [ ] el paquete depende de STORM, nunca al revés;
- [ ] el loader produce `Dataset`;
- [ ] el adapter produce `ModelOutput`;
- [ ] builders y métricas tienen nombres estables;
- [ ] las configuraciones son JSON-compatible;
- [ ] el backend recibe semillas mediante seeders;
- [ ] los artefactos se recuperan en un proceso nuevo;
- [ ] existen tests de paridad contra la biblioteca nativa;
- [ ] las dependencias pesadas son opcionales;
- [ ] licencias y notices están documentados.
