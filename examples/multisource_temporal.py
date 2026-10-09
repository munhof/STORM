"""A 2 Hz reference and a 1 Hz source, within one training session."""
from storm.experiment_nodes import experiment_registry
from storm.experiments import ExperimentExecutor
axis = {'clock': 'camera', 'unit': 's', 'origin': 'session-start', 'scope': 'session'}
def source(name, times, values):
    return {'id': name, 'type': 'data.inline', 'config': {'inputs': values,
        'ids': [f'{name}-{i}' for i in range(len(times))], 'sessions': ['s1'] * len(times),
        'partitions': ['train'] * len(times), 'times': times, 'time_axis': axis}}
spec = {'version': '1', 'nodes': [source('pose', [0, .5, 1, 1.5, 2], [0, 0, 0, 0, 0]),
    source('sensor', [0, 1, 2], [10, 20, 30]),
    {'id': 'aligned', 'type': 'join.align', 'config': {'method': 'linear', 'tolerance': 1.}}],
    'edges': [{'source': 'pose.train', 'target': 'aligned.reference'},
              {'source': 'sensor.train', 'target': 'aligned.source'}]}
result = ExperimentExecutor(experiment_registry()).run(spec)
assert result.status == 'complete', result.records
context = result.outputs['aligned']['context']
assert context.state['aligned'] == [10, 15, 20, 25, 30]
print(context.state)
