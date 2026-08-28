from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class ArtifactRef:
    """Stable reference to one persisted artifact and its content digest."""

    artifact_id: str
    kind: str
    digest: str
    uri: str

    def to_dict(self) -> dict[str, str]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.kind,
            "digest": self.digest,
            "uri": self.uri,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, str]) -> "ArtifactRef":
        return cls(
            artifact_id=data["artifact_id"],
            kind=data["kind"],
            digest=data["digest"],
            uri=data["uri"],
        )


class ArtifactStore(Protocol):
    """Persistence boundary implemented by local or remote artifact backends."""

    def save(
        self,
        *,
        kind: str,
        artifact_id: str,
        value: Any,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactRef: ...

    def load(self, reference: ArtifactRef) -> Any: ...

    def resolve(self, *, kind: str, artifact_id: str) -> ArtifactRef: ...

