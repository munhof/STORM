"""Small, deterministic samples for dataset inventory previews."""

PREVIEW_LIMIT = 256
PREVIEW_STRATEGY = 'balanced_sessions_segments_v1'
PIPELINE_PREVIEW_STRATEGY = 'largest_contiguous_partition_block_v1'


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


def sample_pipeline_preview_indices(training_indices, evaluation_indices,
                                    fit_indices, *, sessions, segments, frames,
                                    reserved_evaluation, steps,
                                    limit=PREVIEW_LIMIT):
    """Choose bounded contiguous source blocks for a preparation preview.

    Training preview rows come only from unreserved fit observations. Evaluation
    prefers held-out rows, falling back to reserved rows when no held-out rows
    exist. Each selected block stays within one session and segment and follows
    consecutive source frames so temporal steps can build valid windows.
    """
    if type(limit) is not int or limit < 0:
        raise ValueError('El límite de la muestra debe ser un entero no negativo.')
    row_count = len(sessions)
    if any(len(values) != row_count for values in (
            segments, frames, reserved_evaluation)):
        raise ValueError('Los metadatos de continuidad deben alinearse con las observaciones.')
    index_sets = (training_indices, evaluation_indices, fit_indices)
    if any(type(index) is not int or index < 0 or index >= row_count
           for indices in index_sets for index in indices):
        raise ValueError('Un índice de partición está fuera de las observaciones.')
    if any(type(value) is not bool for value in reserved_evaluation):
        raise ValueError('Las reservas de evaluación deben ser booleanas.')

    span = 1
    for step in steps:
        if step.get('type') != 'pose.temporal_windows':
            continue
        offsets = step.get('config', {}).get('offsets', [])
        if offsets:
            if any(type(offset) is not int for offset in offsets):
                raise ValueError('Los offsets temporales deben ser enteros.')
            span = max(span, max(offsets) - min(offsets) + 1)

    training = sorted(set(fit_indices) or set(training_indices))
    training = [index for index in training if not reserved_evaluation[index]]
    held_out = [index for index in evaluation_indices
                if not reserved_evaluation[index]]
    evaluation = sorted(set(held_out or evaluation_indices))
    if len(training_indices) + len(evaluation_indices) <= limit:
        return sorted(training_indices), sorted(evaluation_indices)
    if not limit or not training and not evaluation:
        return [], []

    if training and evaluation:
        training_limit = limit // 2
        evaluation_limit = limit - training_limit
    elif training:
        training_limit, evaluation_limit = limit, 0
    else:
        training_limit, evaluation_limit = 0, limit

    training_sample = _largest_contiguous_block(
        training, training_limit, sessions, segments, frames, span)
    evaluation_sample = _largest_contiguous_block(
        evaluation, evaluation_limit, sessions, segments, frames, span)
    return training_sample, evaluation_sample


def _largest_contiguous_block(indices, limit, sessions, segments, frames, span):
    if limit <= 0 or not indices:
        return []

    runs = []
    run = []
    for index in indices:
        if index < 0 or index >= len(sessions):
            raise ValueError('Un índice de muestra está fuera de las observaciones.')
        if run:
            previous = run[-1]
            contiguous = (
                index == previous + 1
                and sessions[index] == sessions[previous]
                and segments[index] == segments[previous]
                and frames[index] == frames[previous] + 1
            )
            if not contiguous:
                runs.append(run)
                run = []
        run.append(index)
    if run:
        runs.append(run)

    eligible_runs = [candidate for candidate in runs if len(candidate) >= span]
    if not eligible_runs:
        return []
    selected_run = max(eligible_runs, key=len)
    sample_size = min(limit, len(selected_run))
    start = (len(selected_run) - sample_size) // 2
    return selected_run[start:start + sample_size]
