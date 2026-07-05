from knowledge.archetypes.clustering import cluster_builds


def test_cluster_builds_separates_two_distant_groups() -> None:
    vectors = [
        [0.0, 0.0], [0.05, 0.0], [0.0, 0.05],
        [1.0, 1.0], [0.95, 1.0], [1.0, 0.95],
    ]
    labels = cluster_builds(vectors, distance_threshold=0.5)

    assert len(set(labels[:3])) == 1
    assert len(set(labels[3:])) == 1
    assert labels[0] != labels[3]


def test_cluster_builds_empty_input_returns_empty() -> None:
    assert cluster_builds([], distance_threshold=0.5) == []


def test_cluster_builds_single_input_is_its_own_cluster() -> None:
    assert cluster_builds([[0.1, 0.2]], distance_threshold=0.5) == [0]


def test_cluster_builds_large_threshold_collapses_to_one_cluster() -> None:
    vectors = [[0.0, 0.0], [1.0, 1.0], [0.5, 0.5]]
    labels = cluster_builds(vectors, distance_threshold=100.0)
    assert len(set(labels)) == 1
