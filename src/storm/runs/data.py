from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Dataset:
    """Runtime data resolved from a serializable DataRef by an application."""

    inputs: Any
    targets: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

