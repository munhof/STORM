from storm_studio.preview_sampling import sample_pipeline_preview_indices


def test_pipeline_preview_samples_bounded_contiguous_training_and_evaluation_blocks():
    count = 1_000
    sessions = ["train-session"] * count + ["test-session"] * count
    segments = ["train-session:0"] * count + ["test-session:0"] * count
    frames = list(range(count)) + list(range(count))
    reserved = [False] * (2 * count)
    reserved[300:340] = [True] * 40
    training = list(range(count))
    fitting = [index for index in training if not reserved[index]]
    evaluation = list(range(300, 340)) + list(range(count, 2 * count))
    steps = [{"type": "pose.temporal_windows", "config": {"offsets": [-2, 0, 2]}}]

    sampled_training, sampled_evaluation = sample_pipeline_preview_indices(
        training,
        evaluation,
        fitting,
        sessions=sessions,
        segments=segments,
        frames=frames,
        reserved_evaluation=reserved,
        steps=steps,
        limit=256,
    )

    assert len(sampled_training) <= 128
    assert len(sampled_evaluation) <= 128
    assert sampled_training and sampled_evaluation
    assert set(sampled_training) <= set(fitting)
    assert set(sampled_evaluation) <= set(evaluation)
    assert not (set(sampled_training) & set(sampled_evaluation))
    assert set(sampled_evaluation) & set(range(count, 2 * count))

    for sampled in (sampled_training, sampled_evaluation):
        assert all(
            sessions[left] == sessions[right]
            and segments[left] == segments[right]
            and frames[right] == frames[left] + 1
            for left, right in zip(sampled, sampled[1:])
        )
