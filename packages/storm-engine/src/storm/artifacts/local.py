from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import pickle
import re
import tempfile
from typing import Any, Mapping

from storm.artifacts.base import ArtifactRef
from storm.config import json_compatible


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class FileArtifactStore:
    """Filesystem artifact store for trusted Python objects.

    Payloads use pickle so arbitrary model adapters can be persisted. Loading a
    pickle can execute code; callers must therefore only load artifacts from a
    trusted store.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        *,
        kind: str,
        artifact_id: str,
        value: Any,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef:
        location = self._location(kind=kind, artifact_id=artifact_id)
        location.mkdir(parents=True, exist_ok=True)
        payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        reference = ArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            digest=digest,
            uri=f"{kind}/{artifact_id}",
        )
        manifest = {
            "schema_version": 1,
            **reference.to_dict(),
            "serializer": "pickle",
            "metadata": json_compatible(dict(metadata or {})),
        }
        self._atomic_write(location / "payload.pkl", payload)
        self._atomic_write(
            location / "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
        )
        return reference

    def load(self, reference: ArtifactRef) -> Any:
        resolved = self.resolve(kind=reference.kind, artifact_id=reference.artifact_id)
        if resolved != reference:
            raise ValueError(
                f"Artifact reference for '{reference.artifact_id}' does not match its manifest."
            )
        payload_path = self.root / resolved.uri / "payload.pkl"
        payload = payload_path.read_bytes()
        actual_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        if actual_digest != resolved.digest:
            raise ValueError(f"Artifact '{reference.artifact_id}' failed digest verification.")
        return pickle.loads(payload)

    def resolve(self, *, kind: str, artifact_id: str) -> ArtifactRef:
        location = self._location(kind=kind, artifact_id=artifact_id)
        manifest_path = location / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"Artifact '{artifact_id}' of kind '{kind}' was not found in {self.root}."
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        reference = ArtifactRef.from_dict(manifest)
        expected_uri = f"{kind}/{artifact_id}"
        if (
            reference.kind != kind
            or reference.artifact_id != artifact_id
            or reference.uri != expected_uri
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", reference.digest)
            or manifest.get("schema_version") != 1
            or manifest.get("serializer") != "pickle"
        ):
            raise ValueError(f"Artifact manifest is invalid for '{artifact_id}'.")
        return reference

    def _location(self, *, kind: str, artifact_id: str) -> Path:
        for label, component in (("kind", kind), ("artifact_id", artifact_id)):
            if not _SAFE_COMPONENT.fullmatch(component):
                raise ValueError(f"Invalid artifact {label}: {component!r}.")
        return self.root / kind / artifact_id

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = handle.name
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None and os.path.exists(temporary_path):
                os.unlink(temporary_path)
