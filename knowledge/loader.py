"""Idempotent upsert helpers for the knowledge/ tag+rating pipeline.

Tag tables (`champion_tags`/`item_tags`/`rune_tags`) key their uniqueness on
`(entity_id, tag, source)`, so `'llm'` and `'override'` rows coexist; each
upsert here deletes-then-reinserts only the rows for its own `(entity_id,
source)`, the exact convention already used by
`ingestion/data_dragon/source.py` for `source='data_dragon'` rows, so other
sources' rows are never touched.

`champion_ratings` keys uniqueness on `(champion_id, rating_name)` alone -
no `source` in the constraint - so at most one row can exist per rating
name. `upsert_champion_ratings` deletes-then-reinserts by `(champion_id,
rating_name)` regardless of the row's previous source, meaning an
`'override'` write here genuinely replaces an `'llm'` row rather than
coexisting with it. This asymmetry versus the tag tables is deliberate, per
the schema as designed.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.models import ChampionRating, ChampionTag, ItemTag, RuneTag

# ORM-enabled `delete()` (not `Table.__table__.delete()`) so the session's
# identity map stays in sync with rows removed here - this pipeline runs
# many delete-then-reinsert cycles inside one long-lived session
# (hundreds of champions/items/runes per `knowledge.run` invocation), where
# a Core-level delete leaving stale identity-map entries behind would risk
# a later query in the same session returning an expired/stale object.


def upsert_champion_tags(
    session: Session, champion_id: int, tags: list[str], source: str
) -> None:
    session.execute(
        delete(ChampionTag).where(
            ChampionTag.champion_id == champion_id, ChampionTag.source == source
        )
    )
    for tag in tags:
        session.add(ChampionTag(champion_id=champion_id, tag=tag, source=source))


def upsert_champion_ratings(
    session: Session, champion_id: int, ratings: dict[str, float], source: str
) -> None:
    for rating_name, value in ratings.items():
        session.execute(
            delete(ChampionRating).where(
                ChampionRating.champion_id == champion_id,
                ChampionRating.rating_name == rating_name,
            )
        )
        session.add(
            ChampionRating(
                champion_id=champion_id, rating_name=rating_name, value=value, source=source
            )
        )


def upsert_item_tags(session: Session, item_id: int, tags: list[str], source: str) -> None:
    session.execute(
        delete(ItemTag).where(ItemTag.item_id == item_id, ItemTag.source == source)
    )
    for tag in tags:
        session.add(ItemTag(item_id=item_id, tag=tag, source=source))


def upsert_rune_tags(session: Session, rune_id: int, tags: list[str], source: str) -> None:
    session.execute(
        delete(RuneTag).where(RuneTag.rune_id == rune_id, RuneTag.source == source)
    )
    for tag in tags:
        session.add(RuneTag(rune_id=rune_id, tag=tag, source=source))


def champion_has_source(session: Session, champion_id: int, source: str) -> bool:
    row = session.execute(
        select(ChampionTag.id).where(
            ChampionTag.champion_id == champion_id, ChampionTag.source == source
        )
    ).first()
    return row is not None


def item_has_source(session: Session, item_id: int, source: str) -> bool:
    row = session.execute(
        select(ItemTag.id).where(ItemTag.item_id == item_id, ItemTag.source == source)
    ).first()
    return row is not None


def rune_has_source(session: Session, rune_id: int, source: str) -> bool:
    row = session.execute(
        select(RuneTag.id).where(RuneTag.rune_id == rune_id, RuneTag.source == source)
    ).first()
    return row is not None
