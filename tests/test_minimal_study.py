from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import unittest

from storm import (
    DataRef,
    Dataset,
    FileArtifactStore,
    MetricRegistry,
    ModelOutput,
    ModelRegistry,
    RunSpec,
    Study,
    RunResult,
    StudySpec,
)


class MeanModel:
    def __init__(self, config):
        self.offset = float(config["offset"])
        self.value = 0.0

    def fit(self, inputs, targets=None):
        self.value = sum(inputs) / len(inputs) + self.offset
        return self

    def predict(self, inputs):
        return ModelOutput(predictions=[self.value] * len(inputs))


class MeanPredictionMetric:
    def evaluate(self, *, dataset, output, model):
        return sum(output.predictions) / len(output.predictions)


class MinimalStudyTest(unittest.TestCase):
    def _spec(self) -> StudySpec:
        return StudySpec(
            study_id="generic-study",
            data=DataRef(identifier="dataset-v1", fingerprint="sha256:data-v1"),
            runs=(
                RunSpec(
                    run_id="baseline",
                    model_type="mean",
                    model_config={"offset": 0.0},
                    seed=7,
                ),
                RunSpec(
                    run_id="shifted",
                    model_type="mean",
                    model_config={"offset": 2.0},
                    seed=7,
                ),
            ),
            metrics=("mean_prediction",),
        )

    def test_study_spec_round_trips_as_json_data(self):
        spec = self._spec()

        payload = json.loads(json.dumps(spec.to_dict()))

        self.assertEqual(StudySpec.from_dict(payload), spec)
        self.assertEqual(spec.runs[0].model_config, {"offset": 0.0})

    def test_training_selection_and_model_reuse_form_one_traceable_flow(self):
        from tempfile import TemporaryDirectory

        models = ModelRegistry()
        models.register("mean", MeanModel)
        metrics = MetricRegistry()
        metrics.register("mean_prediction", MeanPredictionMetric())

        with TemporaryDirectory() as directory:
            artifacts = FileArtifactStore(directory)
            study = Study(
                self._spec(),
                data_loader=lambda data_ref: Dataset(inputs=[1.0, 3.0, 5.0]),
                models=models,
                metrics=metrics,
                artifacts=artifacts,
            )

            results = study.run()
            best = results.select("mean_prediction", maximize=True)
            loaded_model = best.load_model()
            prediction = loaded_model.predict([10.0, 20.0])

            self.assertEqual(len(results), 2)
            self.assertEqual(best.run_id, "shifted")
            self.assertEqual(best.metrics, {"mean_prediction": 5.0})
            self.assertEqual(prediction.predictions, [5.0, 5.0])
            self.assertEqual(best.data_ref, self._spec().data)
            self.assertTrue(best.spec_fingerprint.startswith("sha256:"))
            self.assertTrue(best.model_artifact.digest.startswith("sha256:"))
            self.assertEqual(best.load_output().predictions, [5.0, 5.0, 5.0])

            manifest = Path(directory, best.model_artifact.uri, "manifest.json")
            metadata = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(metadata["metadata"]["study_id"], "generic-study")
            self.assertEqual(metadata["metadata"]["run_id"], "shifted")
            self.assertEqual(metadata["digest"], best.model_artifact.digest)

            recovered = RunResult.recover(
                artifacts,
                artifact_id=best.run_artifact.artifact_id,
            )
            self.assertEqual(recovered.run_id, "shifted")
            self.assertEqual(recovered.load_model().predict([0.0]).predictions, [5.0])

            repeated = study.run()
            self.assertNotEqual(
                results[0].execution_id,
                repeated[0].execution_id,
                "Each execution must remain separately addressable.",
            )

    def test_logical_ids_do_not_have_to_be_filesystem_names(self):
        from tempfile import TemporaryDirectory

        models = ModelRegistry()
        models.register("mean", MeanModel)
        spec = StudySpec(
            study_id="study/with spaces",
            data=DataRef(identifier="dataset", fingerprint="sha256:data"),
            runs=(
                RunSpec(
                    run_id="run:one",
                    model_type="mean",
                    model_config={"offset": 0.0},
                ),
            ),
        )

        with TemporaryDirectory() as directory:
            result = Study(
                spec,
                data_loader=lambda data_ref: Dataset(inputs=[1.0]),
                models=models,
                artifacts=FileArtifactStore(directory),
            ).run()[0]

            self.assertEqual(result.study_id, "study/with spaces")
            self.assertEqual(result.run_id, "run:one")

    def test_unknown_registered_components_fail_explicitly(self):
        with self.assertRaisesRegex(KeyError, "missing"):
            ModelRegistry().build("missing", {})
        with self.assertRaisesRegex(KeyError, "missing"):
            MetricRegistry().get("missing")

    def test_artifact_manifest_cannot_redirect_payload_resolution(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            artifacts = FileArtifactStore(directory)
            reference = artifacts.save(
                kind="models",
                artifact_id="safe-id",
                value={"model": "safe"},
            )
            manifest_path = Path(directory, reference.uri, "manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["uri"] = "../../outside"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "manifest"):
                artifacts.resolve(kind="models", artifact_id="safe-id")

    def test_import_has_no_scientific_or_rainstorm_dependencies(self):
        source_root = Path(__file__).resolve().parents[1] / "src"
        code = (
            "import sys, storm; "
            "forbidden={'rainstorm','numpy','pandas','sklearn','torch','tqdm'}; "
            "loaded={name.split('.')[0] for name in sys.modules}; "
            "raise SystemExit(1 if forbidden & loaded else 0)"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(source_root)

        completed = subprocess.run(
            [sys.executable, "-c", code],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_distribution_metadata_uses_lgpl_3_or_later(self):
        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        license_text = (root / "LICENSE").read_text(encoding="utf-8")

        self.assertEqual(metadata["project"]["license"], "LGPL-3.0-or-later")
        self.assertIn("GNU LESSER GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 3, 29 June 2007", license_text)


if __name__ == "__main__":
    unittest.main()
