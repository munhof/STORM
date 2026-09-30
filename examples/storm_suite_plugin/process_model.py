"""Inference-only adapter calling an isolated Python protocol fixture."""
import json
import subprocess
import sys
from storm.models import ModelOutput


class ProcessModel:
    def __init__(self, config):
        self.executable = sys.executable

    def predict(self, inputs):
        response = subprocess.run(
            [self.executable, '-c',
             'import json,sys; x=json.load(sys.stdin); print(json.dumps({"predictions":[v*2 for v in x]}))'],
            input=json.dumps(list(inputs)), text=True, capture_output=True, check=True, timeout=10)
        return ModelOutput(json.loads(response.stdout)['predictions'])
