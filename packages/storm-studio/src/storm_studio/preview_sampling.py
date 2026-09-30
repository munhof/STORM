"""Small, deterministic samples for dataset inventory previews."""

PREVIEW_LIMIT = 256
PREVIEW_STRATEGY = 'balanced_sessions_segments_v1'


def sample_preview_indices(sessions, segments, limit=PREVIEW_LIMIT):
    """Choose representative rows while covering sessions and video segments."""
    if len(sessions) != len(segments):
        raise ValueError('Las sesiones y segmentos deben estar alineados.')
    if limit < 0:
        raise ValueError('El límite de la muestra no puede ser negativo.')

    row_count = len(sessions)
    if row_count <= limit:
        return list(range(row_count))
    if limit == 0:
        return []

    session_counts = {}
    segment_counts = {}
    first_segment_by_session = {}
    for session, segment in zip(sessions, segments):
        session_counts[session] = session_counts.get(session, 0) + 1
        key = (session, segment)
        segment_counts[key] = segment_counts.get(key, 0) + 1
        first_segment_by_session.setdefault(session, key)

    session_targets = {session: count // 2
                       for session, count in session_counts.items()}
    segment_targets = {key: count // 2 for key, count in segment_counts.items()}
    session_seen = dict.fromkeys(session_counts, 0)
    segment_seen = dict.fromkeys(segment_counts, 0)
    session_representatives = {}
    segment_representatives = {}
    for index, (session, segment) in enumerate(zip(sessions, segments)):
        session_seen[session] += 1
        key = (session, segment)
        segment_seen[key] += 1
        if session_seen[session] - 1 == session_targets[session]:
            session_representatives[session] = index
        if segment_seen[key] - 1 == segment_targets[key]:
            segment_representatives[key] = index

    if len(session_counts) > limit:
        return sorted(_spread(list(session_representatives.values()), limit))

    selected = {segment_representatives[key]
                for key in first_segment_by_session.values()}

    segment_representatives = list(segment_representatives.values())
    remaining_segments = [index for index in segment_representatives
                          if index not in selected]
    selected.update(_spread(remaining_segments, limit - len(selected)))

    remaining = limit - len(selected)
    if remaining:
        if remaining == 1:
            candidates = [row_count // 2]
        else:
            candidates = [
                round(index * (row_count - 1) / (remaining - 1))
                for index in range(remaining)
            ]
        for index in candidates:
            if len(selected) >= limit:
                break
            selected.add(index)

    if len(selected) < limit:
        for index in range(row_count):
            selected.add(index)
            if len(selected) >= limit:
                break

    return sorted(selected)


def _spread(values, count):
    values = list(values)
    if count <= 0 or not values:
        return []
    if len(values) <= count:
        return values
    if count == 1:
        return [values[len(values) // 2]]
    return [values[round(index * (len(values) - 1) / (count - 1))]
            for index in range(count)]
