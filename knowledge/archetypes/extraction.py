"""Orchestrates build-archetype extraction per champion+role (docs/sepc.md
Component 1): gather observed builds -> tag-profile vectors -> cluster ->
summarize each surviving cluster into representative items/runes, a name,
and rating deltas. Reuses already-computed statistics (build_path_
statistics, rune_statistics) for purchase order / keystone identification
rather than re-deriving them from raw timelines.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import ARCHETYPES
from db.models import BuildPathStatistics, RuneStatistics
from knowledge.archetypes.builds import ObservedBuild, gather_observed_builds
from knowledge.archetypes.clustering import cluster_builds
from knowledge.archetypes.deltas import rating_deltas
from knowledge.archetypes.naming import damage_label, name_archetype
from knowledge.archetypes.profile import tag_fraction_dict, tag_profile_vector


@dataclass
class ArchetypeItemResult:
    item_id: int
    is_situational: bool
    build_order: int | None


@dataclass
class ArchetypeRuneResult:
    rune_id: int
    is_keystone: bool


@dataclass
class ExtractedArchetype:
    name: str
    role: str
    weight: float
    items: list[ArchetypeItemResult]
    runes: list[ArchetypeRuneResult]
    ratings: dict[str, float]


def extract_champion_role_archetypes(
    session: Session, champion_id: int, role: str, patch_id: int
) -> list[ExtractedArchetype]:
    builds = gather_observed_builds(session, champion_id, role, patch_id)
    if sum(build.weight for build in builds) < ARCHETYPES.min_builds_per_champion_role:
        return []

    vectors = [tag_profile_vector(session, build) for build in builds]
    labels = cluster_builds(vectors, ARCHETYPES.distance_threshold)

    clusters: dict[int, list[tuple[ObservedBuild, list[float]]]] = {}
    for build, vector, label in zip(builds, vectors, labels):
        clusters.setdefault(label, []).append((build, vector))

    archetypes = []
    for members in clusters.values():
        weight = sum(build.weight for build, _ in members)
        if weight < ARCHETYPES.min_cluster_weight:
            continue
        archetypes.append(
            _summarize_cluster(session, champion_id, role, patch_id, members, weight)
        )

    archetypes.sort(key=lambda archetype: archetype.weight, reverse=True)
    return archetypes[: ARCHETYPES.max_archetypes_per_champion_role]


def _summarize_cluster(
    session: Session,
    champion_id: int,
    role: str,
    patch_id: int,
    members: list[tuple[ObservedBuild, list[float]]],
    weight: float,
) -> ExtractedArchetype:
    builds = [build for build, _ in members]
    weights = [build.weight for build in builds]

    items = _representative_ids(
        [build.item_ids for build in builds],
        weights,
        ARCHETYPES.core_item_frequency,
        ARCHETYPES.situational_item_frequency,
    )
    runes = _representative_ids(
        [[*build.primary_runes, *build.secondary_runes] for build in builds],
        weights,
        ARCHETYPES.core_rune_frequency,
        ARCHETYPES.core_rune_frequency,  # runes have no situational tier
    )

    item_ids = [item_id for item_id, _ in items]
    build_orders = _build_orders(session, champion_id, role, patch_id, item_ids)
    item_results = [
        ArchetypeItemResult(
            item_id=item_id, is_situational=situational, build_order=build_orders.get(item_id)
        )
        for item_id, situational in items
    ]

    rune_ids = [rune_id for rune_id, _ in runes]
    keystone_ids = _keystone_ids(session, champion_id, role, patch_id, rune_ids)
    rune_results = [
        ArchetypeRuneResult(rune_id=rune_id, is_keystone=rune_id in keystone_ids)
        for rune_id in rune_ids
    ]

    vector_length = len(members[0][1])
    avg_vector = [
        sum(vector[i] * build.weight for build, vector in members) / weight
        for i in range(vector_length)
    ]
    tag_fractions = tag_fraction_dict(avg_vector)

    damage = damage_label(session, item_ids)
    name = name_archetype(tag_fractions, damage)
    ratings = rating_deltas(tag_fractions)

    return ExtractedArchetype(
        name=name, role=role, weight=weight, items=item_results, runes=rune_results, ratings=ratings
    )


def _representative_ids(
    id_lists: list[list[int]],
    weights: list[float],
    core_frequency: float,
    situational_frequency: float,
) -> list[tuple[int, bool]]:
    """Weighted presence-fraction per distinct id across a cluster's builds
    (a build voting for the same id twice - e.g. a rare duplicate item -
    doesn't count twice). Returns (id, is_situational) for ids clearing
    `situational_frequency`; below `core_frequency` they're situational."""

    total_weight = sum(weights)
    id_weight: dict[int, float] = {}
    for ids, weight in zip(id_lists, weights):
        for entity_id in set(ids):
            id_weight[entity_id] = id_weight.get(entity_id, 0.0) + weight

    results = []
    for entity_id, entity_w in id_weight.items():
        frequency = entity_w / total_weight
        if frequency >= core_frequency:
            results.append((entity_id, False))
        elif frequency >= situational_frequency:
            results.append((entity_id, True))

    return results


def _build_orders(
    session: Session, champion_id: int, role: str, patch_id: int, item_ids: list[int]
) -> dict[int, int]:
    if not item_ids:
        return {}

    rows = session.execute(
        select(BuildPathStatistics).where(
            BuildPathStatistics.patch_id == patch_id,
            BuildPathStatistics.champion_id == champion_id,
            BuildPathStatistics.role == role,
            BuildPathStatistics.item_id.in_(item_ids),
        )
    ).scalars()

    best: dict[int, tuple[float, int]] = {}
    for row in rows:
        current = best.get(row.item_id)
        if current is None or row.pick_rate > current[0]:
            best[row.item_id] = (row.pick_rate, row.purchase_order)

    return {item_id: order for item_id, (_, order) in best.items()}


def _keystone_ids(
    session: Session, champion_id: int, role: str, patch_id: int, rune_ids: list[int]
) -> set[int]:
    if not rune_ids:
        return set()

    rows = session.execute(
        select(RuneStatistics.rune_id).where(
            RuneStatistics.patch_id == patch_id,
            RuneStatistics.champion_id == champion_id,
            RuneStatistics.role == role,
            RuneStatistics.rune_id.in_(rune_ids),
            RuneStatistics.is_keystone.is_(True),
        )
    ).scalars()
    return set(rows)
