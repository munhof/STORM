from __future__ import annotations

from typing import Any

from storm.metrics.registry import MetricRegistry


class Accuracy:
    """Fraction of predictions equal to their supervised targets."""

    def evaluate(self, *, dataset: Any, output: Any, model: Any) -> float:
        targets, predictions = _paired_values(dataset, output)
        return sum(
            predicted == target
            for predicted, target in zip(predictions, targets, strict=True)
        ) / len(targets)


class MeanAbsoluteError:
    """Arithmetic mean of absolute prediction errors."""

    def evaluate(self, *, dataset: Any, output: Any, model: Any) -> float:
        targets, predictions = _paired_numbers(dataset, output)
        return sum(
            abs(predicted - target)
            for predicted, target in zip(predictions, targets, strict=True)
        ) / len(targets)


class MeanSquaredError:
    """Arithmetic mean of squared prediction errors."""

    def evaluate(self, *, dataset: Any, output: Any, model: Any) -> float:
        targets, predictions = _paired_numbers(dataset, output)
        return sum(
            (predicted - target) ** 2
            for predicted, target in zip(predictions, targets, strict=True)
        ) / len(targets)


def register_classic_metrics(registry: MetricRegistry) -> None:
    """Register the dependency-free classic metric catalog."""
    registry.register("accuracy", Accuracy())
    registry.register("mae", MeanAbsoluteError())
    registry.register("mse", MeanSquaredError())


def _paired_values(dataset: Any, output: Any) -> tuple[list[Any], list[Any]]:
    if dataset.targets is None:
        raise ValueError("This metric requires dataset targets.")
    targets = list(dataset.targets)
    predictions = list(output.predictions)
    if not targets:
        raise ValueError("This metric requires at least one target.")
    if len(targets) != len(predictions):
        raise ValueError("Predictions and targets must have the same length.")
    return targets, predictions


def _paired_numbers(dataset: Any, output: Any) -> tuple[list[float], list[float]]:
    targets, predictions = _paired_values(dataset, output)
    try:
        return (
            [float(value) for value in targets],
            [float(value) for value in predictions],
        )
    except (TypeError, ValueError) as error:
        raise TypeError("This metric requires numeric predictions and targets.") from error
