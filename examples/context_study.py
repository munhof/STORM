"""Two independently identified configurations of the same model."""
from storm.experiment_examples import tabular_graph
from storm.experiment_nodes import experiment_registry, run_study
from storm.selection import freeze_selection, evaluate_reserved

spec = tabular_graph()
next(node for node in spec['nodes'] if node['id'] == 'model')['type'] = 'model.constant'
study = {'graph': spec, 'variants': [
    {'id': 'low', 'parameters': {'model.value': 2}},
    {'id': 'high', 'parameters': {'model.value': 9}}], 'seeds': [156]}
runs = run_study(study, experiment_registry())
for run in runs:
    assert run['result'].status == 'complete'
    print(run['variant_id'], run['result'].outputs['metric']['metric'])
# The investigator explicitly freezes the selection after examining validation.
selection = freeze_selection(runs[1]['result'], 'model')
print('separate_test_action', evaluate_reserved(selection))
