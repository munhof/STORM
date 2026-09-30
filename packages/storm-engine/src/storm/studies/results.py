from __future__ import annotations

from collections.abc import Iterator, Sequence

from storm.runs import RunResult


class StudyResults(Sequence[RunResult]):
    """Comparable result handles returned by one study execution."""

    def __init__(self, results: Sequence[RunResult]):
        self._results = tuple(results)

    def __getitem__(self, index):
        return self._results[index]

    def __len__(self) -> int:
        return len(self._results)

    def __iter__(self) -> Iterator[RunResult]:
        return iter(self._results)

    def select(self, metric: str, *, maximize: bool = True) -> RunResult:
        candidates = [result for result in self._results if metric in result.metrics]
        if not candidates:
            raise KeyError(f"No result contains metric '{metric}'.")
        return (max if maximize else min)(
            candidates,
            key=lambda result: result.metrics[metric],
        )

