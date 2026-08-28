from __future__ import annotations

from html import escape
import math
from typing import Iterable


def svg_document(
    body: Iterable[str],
    *,
    width: int,
    height: int,
    title: str,
) -> str:
    content = "".join(body)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}" role="img">'
        f"<title>{escape(title)}</title>"
        '<rect width="100%" height="100%" fill="white"/>'
        f"{content}</svg>"
    )


def finite_numbers(values: Iterable[object], *, label: str) -> list[float]:
    try:
        numbers = [float(value) for value in values]
    except (TypeError, ValueError) as error:
        raise TypeError(f"{label} must contain numeric values.") from error
    if not numbers:
        raise ValueError(f"{label} must not be empty.")
    if any(not math.isfinite(value) for value in numbers):
        raise ValueError(f"{label} must contain only finite values.")
    return numbers


def points(values: Iterable[object], *, label: str) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    try:
        for value in values:
            x, y = value  # type: ignore[misc]
            point = (float(x), float(y))
            if not all(math.isfinite(coordinate) for coordinate in point):
                raise ValueError
            result.append(point)
    except (TypeError, ValueError) as error:
        raise TypeError(f"{label} must contain finite (x, y) pairs.") from error
    if not result:
        raise ValueError(f"{label} must not be empty.")
    return result


def project(value: float, low: float, high: float, start: float, end: float) -> float:
    if high == low:
        return (start + end) / 2
    return start + ((value - low) / (high - low)) * (end - start)
