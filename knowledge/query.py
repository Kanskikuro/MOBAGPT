"""Override-wins resolution for the knowledge/ tag+rating pipeline's output
(docs/sepc.md Component 1: "a manually reviewed override file that always
wins"). Reused by the future build-archetype-extraction pipeline and any
other downstream consumer of a champion/item/rune's semantic profile, so
this rule lives in one place instead of every caller re-deriving it.

Tags: `'override'` and `'llm'` rows can coexist (see knowledge/loader.py),
so this picks override rows when present, else falls back to llm rows.
Ratings: knowledge/loader.py's `upsert_champion_ratings` already enforces at
most one row per `(champion_id, rating_name)`, overwriting on override, so
whatever row exists already *is* the effective value - no source
resolution needed here.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ChampionRating, ChampionTag, ItemTag, RuneTag


def effective_champion_tags(session: Session, champion_id: int) -> list[str]:
    return _effective_tags(session, ChampionTag, ChampionTag.champion_id, champion_id)


def effective_item_tags(session: Session, item_id: int) -> list[str]:
    return _effective_tags(session, ItemTag, ItemTag.item_id, item_id)


def effective_rune_tags(session: Session, rune_id: int) -> list[str]:
    return _effective_tags(session, RuneTag, RuneTag.rune_id, rune_id)


def effective_champion_ratings(session: Session, champion_id: int) -> dict[str, float]:
    rows = session.execute(
        select(ChampionRating).where(ChampionRating.champion_id == champion_id)
    ).scalars()
    return {row.rating_name: row.value for row in rows}


def _effective_tags(session: Session, model, id_column, entity_id: int) -> list[str]:
    override_rows = (
        session.execute(
            select(model.tag).where(id_column == entity_id, model.source == "override")
        )
        .scalars()
        .all()
    )
    if override_rows:
        return list(override_rows)

    llm_rows = (
        session.execute(select(model.tag).where(id_column == entity_id, model.source == "llm"))
        .scalars()
        .all()
    )
    return list(llm_rows)
