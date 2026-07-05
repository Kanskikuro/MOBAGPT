"""Archetype rating deltas (docs/sepc.md Component 1) - the archetype's
*actual* functional profile that Phase 2's Model v0 reads ("candidate
features: ... numeric ratings after applying the archetype's deltas"), as
opposed to knowledge.archetypes.naming's name, which is cosmetic labeling
by comparison. Phase 2 combines `champion_ratings` + this delta at feature-
extraction time - computing that sum isn't this module's job.
"""

from __future__ import annotations

from config.archetype_rules import RATING_TAG_MAP
from config.settings import ARCHETYPES
from config.taxonomy import RATING_NAMES


def rating_deltas(tag_fractions: dict[str, float]) -> dict[str, float]:
    deltas: dict[str, float] = {}

    for rating_name in RATING_NAMES:
        total = 0.0
        for mapped_tag in RATING_TAG_MAP[rating_name]:
            negative = mapped_tag.startswith("-")
            tag = mapped_tag.removeprefix("-")
            fraction = tag_fractions.get(tag, 0.0)
            total += -fraction if negative else fraction

        delta = ARCHETYPES.archetype_delta_scale * total
        delta = max(-ARCHETYPES.archetype_delta_max, min(ARCHETYPES.archetype_delta_max, delta))
        deltas[rating_name] = delta

    return deltas
