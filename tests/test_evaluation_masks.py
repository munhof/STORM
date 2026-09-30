import pytest
from storm.suite import execute, validate_data


def spec(data):
    return {'model':'constant','config':{'value':0},'metrics':['mae'],
            'data':data}


def test_excluded_benchmark_rows_remain_visible_but_do_not_affect_metrics(tmp_path):
    result = execute(spec({'inputs':[1,99,3,4], 'targets':[1,999,3,4],
        'train':[3], 'test':[0,1,2], 'evaluation_mask':[True,False,True,True]}), tmp_path,'masked')
    assert result['predictions'] == [0,0,0]
    assert result['indices'] == [0,1,2]
    assert result['metric_indices'] == [0,2]
    assert result['metrics']['mae'] == 2
    assert result['evaluation_mask'] == [True,False,True]


def test_no_valid_evaluation_labels_disables_ranking_metrics(tmp_path):
    result = execute(spec({'inputs':[0,1,2], 'targets':[0,1,None], 'train':[0],
        'test':[1,2], 'evaluation_mask':[True,False,False]}), tmp_path,'unlabelled')
    assert result['metrics'] == {}
    assert result['metric_indices'] == []
    assert result['evaluation_status'] == 'inspection_only_no_valid_labels'


def test_protected_evaluation_rows_cannot_enter_training(tmp_path):
    with pytest.raises(ValueError, match='reserved'):
        execute(spec({'inputs':[1,2], 'targets':[1,2], 'train':[1], 'test':[0],
                      'reserved_evaluation':[False,True]}), tmp_path,'leak')


@pytest.mark.parametrize('field,value', [
    ('evaluation_mask',[True]), ('reserved_evaluation',[False]),
    ('evaluation_mask',[True,1]), ('reserved_evaluation',[False,None]),
])
def test_masks_must_be_boolean_and_observation_aligned(field,value):
    with pytest.raises(ValueError, match='mask'):
        validate_data({'inputs':[1,2], 'targets':[1,2], 'train':[0], 'test':[1],
                       field:value})


def test_masked_target_must_be_missing_only_when_explicitly_excluded():
    data={'inputs':[1,2], 'targets':[1,None], 'train':[0], 'test':[1],
          'evaluation_mask':[True,False]}
    assert validate_data(data)[1] == [1,None]
    data['evaluation_mask']=[True,True]
    with pytest.raises(ValueError, match='masked'):
        validate_data(data)


def test_inference_can_load_training_rows_with_masked_targets():
    data={'inputs':[1,2,3], 'targets':[None,1,None], 'train':[0,2], 'test':[1],
          'evaluation_mask':[False,True,False], 'groups':['merge_2']*3}
    assert validate_data(data, require_train=False)[1] == [None,1,None]
    with pytest.raises(ValueError, match='Training targets'):
        validate_data(data)


def test_training_still_rejects_groups_split_across_partitions():
    data={'inputs':[1,2], 'targets':[0,1], 'train':[0], 'test':[1],
          'groups':['merge_2','merge_2']}
    with pytest.raises(ValueError, match='Groups overlap'):
        validate_data(data)
