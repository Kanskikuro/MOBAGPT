from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Champion, ChampionRating, ChampionTag, Item, ItemTag, Rune, RuneTag
from knowledge import loader


def _make_champion(session: Session, champion_id: int = 103) -> Champion:
    champion = Champion(
        champion_id=champion_id,
        riot_key="Ahri",
        display_name="Ahri",
        normalized_name="ahri",
        title="the Nine-Tailed Fox",
    )
    session.add(champion)
    session.flush()
    return champion


def _make_item(session: Session, item_id: int = 3124) -> Item:
    item = Item(
        item_id=item_id,
        name="Guinsoo's Rageblade",
        description="On-hit stacking item.",
        plaintext="Stack on-hit effects",
        gold_base=800,
        gold_total=3200,
        gold_sell=2240,
        raw_data={},
    )
    session.add(item)
    session.flush()
    return item


def _make_rune(session: Session, rune_id: int = 8112) -> Rune:
    rune = Rune(
        rune_id=rune_id,
        path_name="Domination",
        slot=0,
        name="Electrocute",
        short_desc="Burst damage over 3 hits.",
        long_desc="Hit an enemy champion with 3 separate attacks or abilities.",
        raw_data={},
    )
    session.add(rune)
    session.flush()
    return rune


def test_upsert_champion_tags_rerun_replaces_only_its_own_source(session: Session) -> None:
    champion = _make_champion(session)
    session.add(ChampionTag(champion_id=champion.champion_id, tag="Mage", source="data_dragon"))
    session.flush()

    loader.upsert_champion_tags(session, champion.champion_id, ["burst", "engage"], "llm")
    session.flush()
    loader.upsert_champion_tags(session, champion.champion_id, ["mobility"], "llm")
    session.commit()

    rows = session.execute(
        select(ChampionTag).where(ChampionTag.champion_id == champion.champion_id)
    ).scalars().all()
    by_source = {(r.tag, r.source) for r in rows}

    assert by_source == {("Mage", "data_dragon"), ("mobility", "llm")}


def test_upsert_champion_ratings_override_replaces_llm_row(session: Session) -> None:
    champion = _make_champion(session)

    loader.upsert_champion_ratings(session, champion.champion_id, {"engage": 5.0}, "llm")
    session.flush()
    loader.upsert_champion_ratings(session, champion.champion_id, {"engage": 8.0}, "override")
    session.commit()

    rows = session.execute(
        select(ChampionRating).where(ChampionRating.champion_id == champion.champion_id)
    ).scalars().all()

    assert len(rows) == 1
    assert rows[0].value == 8.0
    assert rows[0].source == "override"


def test_upsert_item_tags_scoped_by_source(session: Session) -> None:
    item = _make_item(session)
    session.add(ItemTag(item_id=item.item_id, tag="On-Hit", source="data_dragon"))
    session.flush()

    loader.upsert_item_tags(session, item.item_id, ["sustained_dps"], "llm")
    session.commit()

    rows = session.execute(
        select(ItemTag).where(ItemTag.item_id == item.item_id)
    ).scalars().all()
    by_source = {(r.tag, r.source) for r in rows}

    assert by_source == {("On-Hit", "data_dragon"), ("sustained_dps", "llm")}


def test_upsert_rune_tags_idempotent(session: Session) -> None:
    rune = _make_rune(session)

    loader.upsert_rune_tags(session, rune.rune_id, ["burst", "execute"], "llm")
    session.flush()
    loader.upsert_rune_tags(session, rune.rune_id, ["burst", "execute"], "llm")
    session.commit()

    rows = session.execute(
        select(RuneTag).where(RuneTag.rune_id == rune.rune_id)
    ).scalars().all()

    assert sorted(r.tag for r in rows) == ["burst", "execute"]


def test_has_source_helpers(session: Session) -> None:
    champion = _make_champion(session)
    item = _make_item(session)
    rune = _make_rune(session)

    assert loader.champion_has_source(session, champion.champion_id, "llm") is False
    assert loader.item_has_source(session, item.item_id, "llm") is False
    assert loader.rune_has_source(session, rune.rune_id, "llm") is False

    loader.upsert_champion_tags(session, champion.champion_id, ["burst"], "llm")
    loader.upsert_item_tags(session, item.item_id, ["sustained_dps"], "llm")
    loader.upsert_rune_tags(session, rune.rune_id, ["burst"], "llm")
    session.commit()

    assert loader.champion_has_source(session, champion.champion_id, "llm") is True
    assert loader.item_has_source(session, item.item_id, "llm") is True
    assert loader.rune_has_source(session, rune.rune_id, "llm") is True
