from types import SimpleNamespace
from storm_studio.services import compare_group_stability, compare_reasons


def run(mask, targets):
    metric_indices=[i for i,valid in zip([0,1,2],mask) if valid]
    return SimpleNamespace(result={'data_fingerprint':'dataset', 'partition':'test',
        'indices':[0,1,2], 'metric_indices':metric_indices,
        'resolved_data':{'targets':targets}, 'metrics':{'mae':1},
        'metric_definitions':[{'name':'mae','version':'1','direction':'minimize'}],
        'model_version':'1', 'output_metadata':{'semantics':'binary label',
        'training_population':'documented independent training cohort'}})


def test_comparison_targets_use_only_evaluable_observations():
    assert compare_reasons([run([True,False,True],[0,None,1]),
                            run([True,False,True],[0,9,1])]) == []


def test_comparison_blocks_runs_with_different_evaluation_masks():
    reasons=compare_reasons([run([True,False,True],[0,None,1]),
                             run([True,True,False],[0,9,None])])
    assert 'evaluated metric observations differ' in reasons


def test_comparison_handles_inference_results_without_reference_targets():
    first = run([True, False, True], [])
    second = run([True, False, True], [])

    assert 'evaluation targets are missing' in compare_reasons([first, second])


def test_comparison_blocks_different_target_taxonomies_with_same_numeric_codes():
    first = run([True, False, True], [0, None, 1])
    second = run([True, False, True], [0, 9, 1])
    first.result['resolved_data']['taxonomy'] = ['approach', 'rest']
    second.result['resolved_data']['taxonomy'] = ['object', 'other']

    assert 'target taxonomies differ' in compare_reasons([first, second])


def test_comparison_blocks_supervised_outputs_with_different_declared_meaning():
    first = run([True, False, True], [0, None, 1])
    second = run([True, False, True], [0, 9, 1])
    first.result['output_metadata'] = {
        'task': 'binary_classification', 'output_meaning': 'object approach',
    }
    second.result['output_metadata'] = {
        'task': 'binary_classification', 'output_meaning': 'object exploration',
    }

    assert 'output task or category meanings differ' in compare_reasons([first, second])


def test_comparison_requires_binary_mapping_to_two_target_categories():
    first = run([True, False, True], [0, None, 1])
    second = run([True, False, True], [0, 9, 1])
    taxonomy = [f'behavior_{index}' for index in range(12)]
    mapping = {'0': 'behavior_0', '1': 'behavior_1'}
    for result in (first.result, second.result):
        result['resolved_data']['taxonomy'] = taxonomy
        result['output_metadata'] = {
            'task': 'binary_classification', 'output_meaning': 'behavior_1',
            'negative_output_meaning': 'behavior_0',
            'category_mapping': mapping, 'category_mapping_version': '1',
        }

    assert 'binary outputs need a mapping to the two-category evaluation taxonomy' in compare_reasons([
        first, second,
    ])


def test_binary_comparison_allows_matching_versioned_taxonomy_mapping():
    first = run([True, False, True], [0, None, 1])
    second = run([True, False, True], [0, 9, 1])
    taxonomy = ['rest', 'approach']
    metadata = {
        'task': 'binary_classification', 'output_meaning': 'approach',
        'negative_output_meaning': 'rest',
        'category_mapping': {'0': 'rest', '1': 'approach'},
        'category_mapping_version': 'benchmark-v2',
        'training_population': 'documented independent training cohort',
    }
    for result in (first.result, second.result):
        result['resolved_data']['taxonomy'] = taxonomy
        result['output_metadata'] = metadata

    assert compare_reasons([first, second]) == []


def test_comparison_blocks_runs_with_unknown_training_population():
    first = run([True, False, True], [0, None, 1])
    second = run([True, False, True], [0, 9, 1])
    first.operation = second.operation = 'infer'
    first.result['output_metadata']['training_population'] = 'unknown'

    assert ('training population is unknown; overlap with the evaluation benchmark cannot be ruled out'
            in compare_reasons([first, second]))


def test_known_split_training_does_not_need_imported_population_metadata():
    first = run([True, False, True], [0, None, 1])
    second = run([True, False, True], [0, 9, 1])
    first.operation = second.operation = 'train'
    first.result['output_metadata'].pop('training_population')
    second.result['output_metadata'].pop('training_population')

    assert compare_reasons([first, second]) == []


def test_group_stability_uses_permutation_invariant_ari_for_same_data():
    first = SimpleNamespace(result={
        'capabilities': ['group'], 'data_fingerprint': 'same-data',
        'indices': [0, 1, 2, 3, 4, 5],
        'predictions': [0, 0, 0, 1, 1, 1],
        'prediction_mask': [True] * 6,
        'output_metadata': {'semantics': 'model-local VAME state IDs'},
    })
    second = SimpleNamespace(result={
        'capabilities': ['group'], 'data_fingerprint': 'same-data',
        'indices': [0, 1, 2, 3, 4, 5],
        'predictions': [8, 8, 8, 3, 3, 3],
        'prediction_mask': [True] * 6,
        'output_metadata': {'semantics': 'model-local VAME state IDs'},
    })

    result = compare_group_stability([first, second])

    assert result['metric'] == 'ARI'
    assert result['value'] == 1.0
    assert result['observations'] == 6
    assert 'No mide concordancia humana' in result['interpretation']


def test_group_stability_does_not_pool_session_local_discretizers():
    first, second = run([True] * 3, [0, 0, 1]), run([True] * 3, [0, 0, 1])
    for job in (first, second):
        job.result.update(capabilities=['group'], predictions=[0, 0, 1],
                          prediction_mask=[True] * 3)
        job.result['resolved_data']['sessions'] = ['a', 'a', 'b']
        job.result['output_metadata']['discretizer_scope'] = 'session_local'
    assert compare_group_stability([first, second]) is None
    assert 'session-local state IDs cannot be pooled across sessions' in compare_reasons([first, second])
    for job in (first, second):
        job.result['output_metadata']['discretizer_scope'] = 'shared_training_model'
    assert compare_group_stability([first, second])['value'] == 1.0
