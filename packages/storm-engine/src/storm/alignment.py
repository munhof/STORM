"""Explicit observation joins, without positional assumptions or scientific libraries."""
from bisect import bisect_left
from dataclasses import dataclass
from math import isfinite
from numbers import Real


@dataclass(frozen=True)
class TimeAxis:
    clock: str
    unit: str
    origin: str
    scope: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not v for v in (self.clock, self.unit, self.origin, self.scope)):
            raise ValueError('Declare clock, unit, origin and temporal scope')


@dataclass(frozen=True)
class ObservationSeries:
    ids: tuple
    sessions: tuple
    partitions: tuple
    times: tuple
    values: object
    axis: TimeAxis

    def validate(self):
        size = len(self.ids)
        if any(len(v) != size for v in (self.sessions, self.partitions, self.times, self.values)):
            raise ValueError('Observation identity, scope, time and values must align')
        identities = list(zip(self.sessions, self.partitions, self.ids))
        if len(set(identities)) != size:
            raise ValueError('Duplicate observation identity within session and partition')
        if any(not isinstance(t, Real) or isinstance(t, bool) or not isfinite(t) for t in self.times):
            raise ValueError('Timestamps must be finite numbers in the declared unit')
        if any(p not in ('train', 'validation', 'test') for p in self.partitions):
            raise ValueError('Declare train, validation or test for every observation')


@dataclass
class AlignmentResult:
    values: list
    valid: list
    parents: list
    reference_ids: tuple


def align_identity(reference, source):
    reference.validate()
    source.validate()
    indexed = {(s, p, i): n for n, (s, p, i) in
               enumerate(zip(source.sessions, source.partitions, source.ids))}
    values, valid, parents = [], [], []
    for key in zip(reference.sessions, reference.partitions, reference.ids):
        n = indexed.get(key)
        valid.append(n is not None)
        values.append(source.values[n] if n is not None else None)
        parents.append((source.ids[n],) if n is not None else ())
    return AlignmentResult(values, valid, parents, reference.ids)


def _linear(left, right, fraction):
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)) and len(left) == len(right):
        return [_linear(a, b, fraction) for a, b in zip(left, right)]
    if any(not isinstance(v, Real) or isinstance(v, bool) or not isfinite(v) for v in (left, right)):
        raise ValueError('Linear interpolation requires finite numeric values of matching dimensions')
    return left + fraction * (right - left)


def align_temporal(reference, source, *, method='exact', tolerance=None):
    reference.validate()
    source.validate()
    if reference.axis != source.axis:
        raise ValueError('Clock, unit, origin or scope mismatch; declare an explicit clock transformation')
    if method not in ('exact', 'previous', 'nearest', 'linear'):
        raise ValueError('Unknown temporal alignment method')
    if method != 'exact' and (not isinstance(tolerance, Real) or isinstance(tolerance, bool)
                             or not isfinite(tolerance) or tolerance < 0):
        raise ValueError('Non-exact alignment requires explicit finite nonnegative tolerance')
    groups = {}
    for index, key in enumerate(zip(source.sessions, source.partitions)):
        groups.setdefault(key, []).append(index)
    for key, indices in groups.items():
        indices.sort(key=lambda i: source.times[i])
        times = [source.times[i] for i in indices]
        if len(set(times)) != len(times):
            raise ValueError(f'Duplicate ambiguous timestamps in {key}')
        groups[key] = (times, indices)
    values, valid, parents = [], [], []
    for session, partition, time in zip(reference.sessions, reference.partitions, reference.times):
        times, indices = groups.get((session, partition), ([], []))
        value, lineage = None, ()
        if times and times[0] <= time <= times[-1]:
            right = bisect_left(times, time)
            if times[right] == time:
                index = indices[right]
                value, lineage = source.values[index], (source.ids[index],)
            elif method != 'exact':
                left = right - 1
                if method == 'linear':
                    if max(time - times[left], times[right] - time) <= tolerance:
                        a, b = indices[left], indices[right]
                        value = _linear(source.values[a], source.values[b],
                                        (time - times[left]) / (times[right] - times[left]))
                        lineage = (source.ids[a], source.ids[b])
                else:
                    chosen = (left if method == 'previous' or time-times[left] <= times[right]-time else right)
                    if abs(times[chosen] - time) <= tolerance:
                        index = indices[chosen]
                        value, lineage = source.values[index], (source.ids[index],)
        values.append(value)
        valid.append(bool(lineage))
        parents.append(lineage)
    return AlignmentResult(values, valid, parents, reference.ids)
