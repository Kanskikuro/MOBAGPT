"""Idempotent upsert for build-archetype extraction output (docs/sepc.md
Component 1). `BuildArchetype` has no column to scope a delete by (unlike
`champion_tags`/`item_tags`/`rune_tags`'s `source`), so a champion's
archetypes are fully delete-then-reinserted on every run - the same "rows
are unstable across re-runs" convention `ingestion/data_dragon` already
uses for `champion_abilities`. Children (`ArchetypeItem`/`ArchetypeRune`/
`ArchetypeTag`) have no `ondelete="CASCADE"` at the DB level and bulk
`delete()` doesn't trigger ORM cascade, so they're deleted explicitly
before their parent `BuildArchetype` rows, not left to cascade.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.models import ArchetypeItem, ArchetypeRune, ArchetypeTag, BuildArchetype
from knowledge.archetypes.extraction import ExtractedArchetype
from knowledge.archetypes.naming import dedupe_name


def upsert_champion_archetypes(
    session: Session, champion_id: int, archetypes: list[ExtractedArchetype]
) -> int:
    existing_ids = (
        session.execute(select(BuildArchetype.id).where(BuildArchetype.champion_id == champion_id))
        .scalars()
        .all()
    )
    if existing_ids:
        session.execute(delete(ArchetypeItem).where(ArchetypeItem.archetype_id.in_(existing_ids)))
        session.execute(delete(ArchetypeRune).where(ArchetypeRune.archetype_id.in_(existing_ids)))
        session.execute(delete(ArchetypeTag).where(ArchetypeTag.archetype_id.in_(existing_ids)))
        session.execute(delete(BuildArchetype).where(BuildArchetype.champion_id == champion_id))
        session.flush()

    used_names: set[str] = set()
    count = 0
    for archetype in archetypes:
        name = dedupe_name(used_names, archetype.name, archetype.role)
        used_names.add(name)

        row = BuildArchetype(champion_id=champion_id, name=name, role=archetype.role)
        session.add(row)
        session.flush()  # need row.id for children below

        for item in archetype.items:
            session.add(
                ArchetypeItem(
                    archetype_id=row.id,
                    item_id=item.item_id,
                    build_order=item.build_order,
                    is_situational=item.is_situational,
                )
            )
        for rune in archetype.runes:
            session.add(
                ArchetypeRune(
                    archetype_id=row.id, rune_id=rune.rune_id, is_keystone=rune.is_keystone
                )
            )
        for rating_name, delta in archetype.ratings.items():
            session.add(ArchetypeTag(archetype_id=row.id, tag=rating_name, delta=delta))

        count += 1

    return count
