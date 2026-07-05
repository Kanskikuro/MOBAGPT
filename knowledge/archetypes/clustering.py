"""scipy hierarchical clustering wrapper for build-archetype extraction
(docs/sepc.md Component 1). A distance-threshold stopping rule
(config.settings.ArchetypeSettings.distance_threshold) rather than a fixed
k, since guessing a per-champion cluster count upfront doesn't make sense -
hierarchical clustering merges until builds stop looking similar, whatever
that produces.
"""

from __future__ import annotations

from scipy.cluster.hierarchy import fcluster, linkage


def cluster_builds(vectors: list[list[float]], distance_threshold: float) -> list[int]:
    """Returns a 0-based cluster label per input vector, in input order.
    scipy's `linkage` needs at least 2 observations - a single build
    trivially forms its own cluster."""

    if len(vectors) == 0:
        return []
    if len(vectors) == 1:
        return [0]

    z = linkage(vectors, method="average")
    labels = fcluster(z, t=distance_threshold, criterion="distance")
    return [int(label) - 1 for label in labels]  # fcluster labels are 1-based
