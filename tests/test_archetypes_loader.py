from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ArchetypeItem, BuildArchetype, Champion, Item, Rune
from knowledge.archetypes.extraction import (
    ArchetypeItemResult,
    ArchetypeRuneResult,
    ExtractedArchetype,
)
from knowledge.archetypes.loader import upsert_champion_archetypes


def _seed_champion(session: Session, champion_id: int = 103) -> Champion:
    champion = Champion(
        champion_id=champion_id, riot_key="Ahri", display_name="Ahri",
        normalized_name="ahri", title="the Nine-Tailed Fox",
    )
    session.add(champion)
    session.flush()
    return champion


def _seed_item(session: Session, item_id: int) -> None:
    session.add(
        Item(
            item_id=item_id, name=f"Item{item_id}", description="", plaintext="",
            gold_base=0, gold_total=0, gold_sell=0, raw_data={},
        )
    )


def _seed_rune(session: Session, rune_id: int) -> None:
    session.add(
        Rune(
            rune_id=rune_id, path_name="Domination", slot=0, name=f"Rune{rune_id}",
            short_desc="", long_desc="", raw_data={},
        )
    )


def _archetype(name: str = "AD Burst", role: str = "MIDDLE") -> ExtractedArchetype:
    return ExtractedArchetype(
        name=name,
        role=role,
        weight=5.0,
        items=[ArchetypeItemResult(item_id=1, is_situational=False, build_order=1)],
        runes=[ArchetypeRuneResult(rune_id=100, is_keystone=True)],
        ratings={"engage": 3.0},
    )


def test_upsert_creates_archetype_with_children(session: Session) -> None:
    _seed_champion(session)
    _seed_item(session, 1)
    _seed_rune(session, 100)
    session.commit()

    count = upsert_champion_archetypes(session, 103, [_archetype()])
    session.commit()

    assert count == 1
    archetype = session.execute(
        select(BuildArchetype).where(BuildArchetype.champion_id == 103)
    ).scalar_one()
    assert archetype.name == "AD Burst"
    assert len(archetype.items) == 1
    assert archetype.items[0].item_id == 1
    assert archetype.items[0].build_order == 1
    assert len(archetype.runes) == 1
    assert archetype.runes[0].is_keystone is True
    assert len(archetype.tags) == 1
    assert archetype.tags[0].tag == "engage"
    assert archetype.tags[0].delta == 3.0


def test_upsert_rerun_replaces_without_orphaning_children(session: Session) -> None:
    _seed_champion(session)
    _seed_item(session, 1)
    _seed_rune(session, 100)
    session.commit()

    upsert_champion_archetypes(session, 103, [_archetype(name="AD Burst")])
    session.commit()

    upsert_champion_archetypes(session, 103, [_archetype(name="AP Burst")])
    session.commit()

    archetypes = session.execute(
        select(BuildArchetype).where(BuildArchetype.champion_id == 103)
    ).scalars().all()
    assert [a.name for a in archetypes] == ["AP Burst"]

    remaining_items = session.execute(select(ArchetypeItem)).scalars().all()
    assert len(remaining_items) == 1  # no orphaned child row left behind


def test_upsert_dedupes_colliding_names_across_roles(session: Session) -> None:
    _seed_champion(session)
    _seed_item(session, 1)
    _seed_rune(session, 100)
    session.commit()

    archetypes = [
        _archetype(name="AD Burst", role="MIDDLE"),
        _archetype(name="AD Burst", role="TOP"),
    ]
    upsert_champion_archetypes(session, 103, archetypes)
    session.commit()

    names = {
        a.name
        for a in session.execute(
            select(BuildArchetype).where(BuildArchetype.champion_id == 103)
        ).scalars()
    }
    assert names == {"AD Burst", "AD Burst (TOP)"}
