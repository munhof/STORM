"""Small, reproducible examples shared by the Python tutorial and Studio."""

def tabular_graph():
    return {'version': '1', 'nodes': [
        {'id': 'data', 'type': 'data.inline', 'config': {'inputs': [1., 3., 100., 999.],
          'targets': [2., 4., 7., 900.], 'ids': ['a', 'b', 'c', 'd'],
          'sessions': ['s1', 's1', 's2', 's3'],
          'partitions': ['train', 'train', 'validation', 'test']}},
        {'id': 'center', 'type': 'transform.center'},
        {'id': 'train_inputs', 'type': 'adapter.select', 'config': {'data': 'data', 'targets': 'targets'}},
        {'id': 'valid_inputs', 'type': 'adapter.select', 'config': {'data': 'data', 'targets': 'targets'}},
        {'id': 'model', 'type': 'model.mean_regressor'},
        {'id': 'metric', 'type': 'metric.mse'}], 'edges': [
        {'source': 'data.train', 'target': 'center.train'},
        {'source': 'data.validation', 'target': 'center.validation'},
        {'source': 'center.train', 'target': 'train_inputs.context'},
        {'source': 'center.validation', 'target': 'valid_inputs.context'},
        {'source': 'train_inputs.context', 'target': 'model.train'},
        {'source': 'valid_inputs.context', 'target': 'model.validation'},
        {'source': 'model.predictions', 'target': 'metric.context'}]}
