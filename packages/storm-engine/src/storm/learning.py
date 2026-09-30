"""Neutral review and observation contracts; no application dependencies."""
from dataclasses import dataclass
from math import isfinite
import random
from typing import Protocol, Any


@dataclass(frozen=True)
class ObservationAlignment:
    source_ids: tuple[str, ...]
    output_indices: tuple[int, ...]
    intervals: tuple[tuple[float, float], ...] = ()

    def __post_init__(self):
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError('Source identities must be unique')
        if len(self.source_ids) != len(self.output_indices):
            raise ValueError('Every output requires an observation identity')
        if self.intervals and len(self.intervals) != len(self.source_ids):
            raise ValueError('Intervals must match observations')
        if any(not isfinite(a) or not isfinite(b) or b < a for a, b in self.intervals):
            raise ValueError('Invalid observation interval')
        if any(type(i) is not int or i < 0 for i in self.output_indices) or len(set(self.output_indices)) != len(self.output_indices):
            raise ValueError('Output indices must be distinct nonnegative integers')


class QueryStrategy(Protocol):
    def select(self, candidates: list[int], count: int, seed: int) -> list[int]: ...


class IncrementalModel(Protocol):
    def partial_fit(self, inputs: Any, targets: Any = None) -> Any: ...


class CheckpointModel(Protocol):
    def fit_with_checkpoints(self, inputs: Any, targets: Any, checkpoint: Any) -> Any: ...
    def save_checkpoint(self) -> Any: ...
    def load_checkpoint(self, checkpoint: Any) -> None: ...


class AssistantProvider(Protocol):
    def propose(self, context: dict) -> dict: ...


class LocalAssistant:
    """Deterministic mock provider, never calls a remote service."""
    def propose(self, context):
        return {'provider': 'local-mock', 'status': 'proposed',
                'text': 'Check partition independence and declared model capabilities.',
                'context': context}


def select_samples(candidates, count=3, seed=0, strategy='random', scores=None):
    if type(count) is not int or count < 1 or len(set(candidates)) != len(candidates):
        raise ValueError('Invalid selection pool or count')
    if strategy == 'random':
        return random.Random(seed).sample(list(candidates), min(count, len(candidates)))
    if strategy == 'uncertainty':
        if scores is None or any(i not in scores or not isfinite(scores[i]) or not 0 <= scores[i] <= 1 for i in candidates):
            raise ValueError('Uncertainty requires declared confidence scores for the whole pool')
        return sorted(candidates, key=lambda i: (scores[i], i))[:count]
    raise ValueError('Unknown query strategy')


def temporal_window_indices(*, frames, sessions, segments, partitions, reserved,
                            offsets, purpose):
    """Return complete windows without crossing source or reservation boundaries.

    Frames are original integer positions, and segment IDs must be explicitly
    reviewed by the caller. Every position in the temporal span is checked,
    including positions skipped by sparse offsets. No padding is synthesized.
    Reserved evaluation observations remain available for inference only.
    """
    permitted = {'train': {'train'}, 'fit': {'train'},
                 'calibrate': {'calibration'}, 'select': {'train', 'validation'},
                 'inference': {'train', 'validation', 'test', 'calibration', 'benchmark'}}
    if purpose not in permitted:
        raise ValueError('Unknown window purpose')
    n = len(frames)
    if any(len(values) != n for values in (sessions, segments, partitions, reserved)):
        raise ValueError('Observation metadata lengths disagree')
    if any(not isinstance(f, int) or isinstance(f, bool) or f < 0 for f in frames):
        raise ValueError('Frame positions must be nonnegative integers')
    if any(not isinstance(s, str) or not s for s in (*sessions, *segments)):
        raise ValueError('Known session and segment identities are required')
    if any(type(value) is not bool for value in reserved):
        raise ValueError('Explicit reservation flags are required')
    if any(p not in permitted['inference'] for p in partitions):
        raise ValueError('Unknown partition')
    if len(set(zip(sessions, segments, frames))) != n:
        raise ValueError('Duplicate source observations')
    offsets = tuple(offsets)
    if (not offsets or any(type(o) is not int for o in offsets)
            or tuple(sorted(set(offsets))) != offsets or 0 not in offsets):
        raise ValueError('Offsets must be sorted unique integers including zero')
    result = []
    for center in range(n):
        start, stop = center + offsets[0], center + offsets[-1]
        if start < 0 or stop >= n or partitions[center] not in permitted[purpose]:
            continue
        if any(sessions[i] != sessions[center] or segments[i] != segments[center]
               or partitions[i] != partitions[center]
               or frames[i] != frames[center] + i - center
               or (reserved[i] and purpose != 'inference')
               for i in range(start, stop + 1)):
            continue
        result.append(tuple(center + offset for offset in offsets))
    return result
