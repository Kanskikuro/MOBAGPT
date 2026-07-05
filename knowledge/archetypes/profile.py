"""Tag-fraction profile vectors for build-archetype clustering
(docs/sepc.md Component 1): the fraction of a build's items+runes carrying
each config.taxonomy.SEMANTIC_TAGS entry. Requires knowledge.run to have
already populated item_tags/rune_tags - an untagged item/rune just
contributes nothing, so this degrades gracefully rather than crashing.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from config.taxonomy import SEMANTIC_TAGS
from knowledge.archetypes.builds import ObservedBuild
from knowledge.query import effective_item_tags, effective_rune_tags

_SORTED_TAGS = sorted(SEMANTIC_TAGS)
_TAG_INDEX = {tag: i for i, tag in enumerate(_SORTED_TAGS)}


def tag_profile_vector(session: Session, build: ObservedBuild) -> list[float]:
    counts = [0.0] * len(_SORTED_TAGS)
    total = 0

    for item_id in build.item_ids:
        total += 1
        for tag in effective_item_tags(session, item_id):
            if tag in _TAG_INDEX:
                counts[_TAG_INDEX[tag]] += 1

    for rune_id in (*build.primary_runes, *build.secondary_runes):
        total += 1
        for tag in effective_rune_tags(session, rune_id):
            if tag in _TAG_INDEX:
                counts[_TAG_INDEX[tag]] += 1

    if total == 0:
        return counts

    return [count / total for count in counts]


def tag_fraction_dict(vector: list[float]) -> dict[str, float]:
    return dict(zip(_SORTED_TAGS, vector))
