import pytest
from storm.learning import temporal_window_indices


def windows(**overrides):
    values = dict(frames=list(range(9)), sessions=['a']*9, segments=['s']*9,
                  partitions=['train']*9, reserved=[False]*9,
                  offsets=(-1,0,1), purpose='train')
    values.update(overrides)
    return temporal_window_indices(**values)


def test_training_windows_never_touch_reserved_observations():
    assert windows(reserved=[False]*4+[True]+[False]*4) == [(0,1,2),(1,2,3),(5,6,7),(6,7,8)]


def test_inference_can_inspect_reserved_observations():
    assert len(windows(reserved=[True]*9, purpose='inference')) == 7


@pytest.mark.parametrize('field,value', [
    ('sessions',['a']*4+['b']*5),
    ('segments',['s']*4+['t']*5),
    ('partitions',['train']*4+['test']*5),
    ('frames',[0,1,2,3,10,11,12,13,14]),
])
def test_inference_does_not_cross_any_boundary(field,value):
    result = windows(**{field:value}, purpose='inference')
    assert all(not (min(w)<4<=max(w)) for w in result)


def test_sparse_window_does_not_jump_over_a_reserved_frame():
    assert windows(offsets=(-3,0,3), reserved=[False]*4+[True]+[False]*4) == []


@pytest.mark.parametrize('purpose', ['train','fit','calibrate','select'])
def test_fitting_and_selection_exclude_test_partition(purpose):
    assert windows(partitions=['test']*9, purpose=purpose) == []


def test_unknown_boundaries_and_invalid_purpose_fail_closed():
    with pytest.raises(ValueError):
        windows(segments=[None]*9)
    with pytest.raises(ValueError):
        windows(purpose='typo')
    with pytest.raises(ValueError):
        windows(reserved=[False])
