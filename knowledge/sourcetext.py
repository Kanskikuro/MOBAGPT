"""Assembles the prompt input text for the knowledge/ LLM tag+rating
pipeline from data already sitting in the knowledge DB (ingestion/data_dragon
and ingestion/wiki). No network access here - purely DB reads, mirroring how
ingestion/wiki's `fetch()` steps only do external I/O and leave DB reads to
callers with a session.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Champion, ChampionAbilityDetail, Item, Rune


def champion_source_text(session: Session, champion: Champion) -> str:
    lines = [f"Champion: {champion.display_name}"]
    if champion.title:
        lines.append(f"Title: {champion.title}")

    coarse_tags = sorted(t.tag for t in champion.tags if t.source == "data_dragon")
    if coarse_tags:
        lines.append(f"Riot tags: {', '.join(coarse_tags)}")

    if champion.stats is not None:
        s = champion.stats
        ratings = []
        if s.attack_rating is not None:
            ratings.append(f"attack={s.attack_rating}")
        if s.defense_rating is not None:
            ratings.append(f"defense={s.defense_rating}")
        if s.magic_rating is not None:
            ratings.append(f"magic={s.magic_rating}")
        if s.difficulty_rating is not None:
            ratings.append(f"difficulty={s.difficulty_rating}")
        if ratings:
            lines.append(f"Riot 1-10 ratings: {', '.join(ratings)}")
        lines.append(
            f"Base stats: hp={s.hp}, armor={s.armor}, spell_block={s.spell_block}, "
            f"attack_range={s.attack_range}, move_speed={s.move_speed}"
        )

    details_by_slot = {
        d.slot: d
        for d in session.execute(
            select(ChampionAbilityDetail).where(
                ChampionAbilityDetail.champion_id == champion.champion_id
            )
        ).scalars()
    }

    for ability in sorted(champion.abilities, key=lambda a: _slot_order(a.slot)):
        lines.append(f"\n[{ability.slot}] {ability.name}: {_strip_html(ability.description)}")
        detail = details_by_slot.get(ability.slot)
        if detail is not None and detail.notes:
            lines.append(f"Details: {_strip_html(detail.notes)}")

    return "\n".join(lines)


def item_source_text(item: Item) -> str:
    lines = [f"Item: {item.name}", f"Gold cost: {item.gold_total}"]

    coarse_tags = sorted(t.tag for t in item.tags if t.source == "data_dragon")
    if coarse_tags:
        lines.append(f"Riot tags: {', '.join(coarse_tags)}")

    if item.stats:
        stat_str = ", ".join(f"{k}={v}" for k, v in sorted(item.stats.items()))
        lines.append(f"Stats: {stat_str}")

    if item.plaintext:
        lines.append(f"Summary: {item.plaintext}")
    lines.append(f"Description: {_strip_html(item.description)}")

    return "\n".join(lines)


def rune_source_text(rune: Rune) -> str:
    return (
        f"Rune: {rune.name} ({rune.path_name} tree)\n"
        f"Short: {_strip_html(rune.short_desc)}\n"
        f"Long: {_strip_html(rune.long_desc)}"
    )


_SLOT_ORDER = {"passive": 0, "Q": 1, "W": 2, "E": 3, "R": 4}


def _slot_order(slot: str) -> tuple[int, str]:
    return (_SLOT_ORDER.get(slot, 99), slot)


def _strip_html(text: str) -> str:
    """Data Dragon description fields carry inline markup (<br>, <status>,
    etc.). The LLM handles minor markup fine, but bare tags read as noise, so
    do a cheap strip rather than a real parser - this is prompt input, not a
    stored value that needs full fidelity."""
    return re.sub(r"<[^>]+>", " ", text or "").strip()
