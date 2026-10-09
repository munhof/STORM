"""Bounded visual payloads; full-cohort counts and measured timing provenance."""
from collections import Counter
from itertools import combinations
import math


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def compatible_sources(a, b):
    return (bool(a['source_hashes']) and a['source_hashes'] == b['source_hashes']
            and bool(a['partition']) and a['partition'] == b['partition'])


def seconds(job):
    if getattr(job, 'started', None) and getattr(job, 'finished', None):
        value = (job.finished-job.started).total_seconds()
        return value if value >= 0 else None
    return None


def timing(profile, source_jobs):
    job, node = profile['job'], profile['node']
    result = job.result or {}
    record = result.get('nodes', {}).get(node, {})
    measured = profile['evidence'].get('output_metadata', {}).get('timing', {})
    value = {key: measured.get(key) if finite(measured.get(key)) else None
             for key in ('training_seconds', 'inference_seconds')}
    duration = record.get('duration_seconds')
    value.update(node_seconds=duration if finite(duration) else None,
                 source_node_seconds=None, source_job=None)
    graph = result.get('resolved') or result.get('requested') or {}
    nodes = {n['id']: n for n in graph.get('nodes', [])}
    edge = next((e for e in graph.get('edges', []) if e['target'] == node+'.model'), None)
    if edge:
        source = nodes.get(edge['source'].split('.')[0], {}).get('config', {}).get('source', {})
        source_job = source_jobs.get(str(source.get('job')))
        if source_job:
            old = source_job.result.get('nodes', {}).get(source.get('model_node'), {})
            duration = old.get('duration_seconds')
            value.update(source_node_seconds=duration if finite(duration) else None,
                         source_job=str(source_job.pk))
    return value


def build_dashboard(profiles, source_jobs=None):
    result = {'models': [], 'spaces': [], 'pairs': [], 'jobs': []}
    recorded_jobs = set()
    for index, profile in enumerate(profiles):
        evidence = profile['evidence']
        data, metadata = evidence.get('resolved_data', {}), evidence.get('output_metadata', {})
        labels = profile['valid']
        distribution = Counter(str(value) for value in labels.values())
        result['models'].append({'name': profile['model'], 'job': str(profile['job'].pk),
            'partition': profile['partition'], 'count': len(labels), 'states': len(distribution),
            'distribution': [{'label': k, 'count': v} for k, v in distribution.most_common()],
            'timing': timing(profile, source_jobs or {}), 'history': evidence.get('training_history', []),
            'task': metadata.get('task'), 'exploratory': metadata.get('exploratory', False)})
        job_id = str(profile['job'].pk)
        if job_id not in recorded_jobs:
            result['jobs'].append({'id': job_id, 'seconds': seconds(profile['job'])})
            recorded_jobs.add(job_id)
        ids = list(zip(data.get('sessions', []), data.get('observation_ids', [])))
        embeddings = metadata.get('embeddings')
        latent = isinstance(embeddings, list) and len(embeddings) == len(ids) and bool(embeddings)
        vectors = embeddings if latent else data.get('inputs', [])
        if len(vectors) != len(ids):
            vectors = []
        windows = bool(vectors and isinstance(vectors[0], list) and vectors[0]
                       and isinstance(vectors[0][0], list))
        if windows:
            vectors = [[value for frame in row for value in frame]
                       if isinstance(row, list) and all(isinstance(frame, list) for frame in row) else []
                       for row in vectors]
        eligible = [(identity, row) for identity, row in zip(ids, vectors)
                    if identity in labels and isinstance(row, list) and len(row) >= 2
                    and all(finite(v) for v in row)]
        width = len(eligible[0][1]) if eligible else 0
        eligible = [(identity, row) for identity, row in eligible if len(row) == width]
        stride = max(1, math.ceil(len(eligible)/2000))
        sampled = eligible[::stride]
        result['spaces'].append({'model': index, 'kind': 'Espacio latente' if latent else
                                'Coordenadas de la ventana aplanada' if windows else 'Variables de entrada',
            'axes': [f'Latente {i+1}' for i in range(width)] if latent else
                (data.get('feature_names') if len(data.get('feature_names', [])) == width else [f'Variable {i+1}' for i in range(width)]),
            'units': 'coordenadas latentes' if latent else data.get('units'), 'total': len(eligible),
            'points': [{'id': list(identity), 'values': row,
                'labels': [other['valid'].get(identity) if i == index or compatible_sources(profile, other) else None
                           for i, other in enumerate(profiles)]} for identity, row in sampled]})
    for a, b in combinations(range(len(profiles)), 2):
        first, second = profiles[a], profiles[b]
        if not compatible_sources(first, second):
            continue
        common = first['valid'].keys() & second['valid'].keys()
        counts = Counter((str(first['valid'][key]), str(second['valid'][key])) for key in common)
        # Continuous or vector outputs are not categorical contingency tables.
        if not common or max(len({key[0] for key in counts}), len({key[1] for key in counts})) > 100:
            continue
        result['pairs'].append({'a': a, 'b': b, 'count': len(common),
            'cells': [{'a': x, 'b': y, 'count': count} for (x,y), count in sorted(counts.items())]})
    return result
