"""Deterministic archetype naming (docs/sepc.md Component 1) - user's
explicit choice over an LLM call per cluster: free, instant, and the name
is traceable to the archetype's own computed profile rather than a model's
guess.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.archetype_rules import (
    AD_STAT_KEYS,
    AP_STAT_KEYS,
    ARCHETYPE_NAME_BY_TAG,
    DEFAULT_ARCHETYPE_NAME,
)
from config.settings import ARCHETYPES
from db.models import Item


def damage_label(session: Session, item_ids: list[int]) -> str | None:
    """AD/AP lean of a set of items, from their Item.stats keys - the only
    place damage type is computed anywhere in this project;
    config.taxonomy.SEMANTIC_TAGS has no AD/AP tag of its own. None for an
    empty item set or a tied/neither-scoring one (e.g. a pure-tank build)."""

    if not item_ids:
        return None

    ad_score = 0
    ap_score = 0
    items = session.execute(select(Item).where(Item.item_id.in_(item_ids))).scalars()
    for item in items:
        for stat_key in item.stats:
            if stat_key in AD_STAT_KEYS:
                ad_score += 1
            elif stat_key in AP_STAT_KEYS:
                ap_score += 1

    if ad_score == ap_score:
        return None
    return "AD" if ad_score > ap_score else "AP"


def name_archetype(tag_fractions: dict[str, float], damage: str | None) -> str:
    damage_str = damage or ""
    eligible = {
        tag: fraction
        for tag, fraction in tag_fractions.items()
        if tag in ARCHETYPE_NAME_BY_TAG and fraction >= ARCHETYPES.name_tag_min_presence
    }

    if not eligible:
        return DEFAULT_ARCHETYPE_NAME.format(damage=damage_str).strip()

    dominant_tag = max(eligible, key=eligible.get)
    return ARCHETYPE_NAME_BY_TAG[dominant_tag].format(damage=damage_str).strip()


def dedupe_name(existing_names: set[str], base_name: str, role: str) -> str:
    """BuildArchetype has UniqueConstraint(champion_id, name) - a same-
    champion collision (e.g. two roles both scoring the same name) is
    disambiguated by appending the role, then a numeric suffix."""

    if base_name not in existing_names:
        return base_name

    with_role = f"{base_name} ({role})"
    if with_role not in existing_names:
        return with_role

    suffix = 2
    while True:
        candidate = f"{with_role} {suffix}"
        if candidate not in existing_names:
            return candidate
        suffix += 1
