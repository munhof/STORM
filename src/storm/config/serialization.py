from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def json_compatible(value: Any) -> JsonValue:
    """Return a data-only representation suitable for strict JSON encoding."""
    if is_dataclass(value) and not isinstance(value, type):
        return json_compatible(asdict(value))
    if isinstance(value, Enum):
        return json_compatible(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_compatible(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Value {value!r} is not JSON-compatible.")


def canonical_json(value: Any) -> str:
    """Serialize data deterministically for identifiers and fingerprints."""
    return json.dumps(
        json_compatible(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def fingerprint(value: Any) -> str:
    """Build a stable SHA-256 fingerprint from JSON-compatible data."""
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"

