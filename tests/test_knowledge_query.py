from sqlalchemy.orm import Session

from db.models import Champion, ChampionRating, ChampionTag, Item, ItemTag
from knowledge import query


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


def test_effective_champion_tags_no_rows(session: Session) -> None:
    champion = _make_champion(session)
    assert query.effective_champion_tags(session, champion.champion_id) == []


def test_effective_champion_tags_falls_back_to_llm(session: Session) -> None:
    champion = _make_champion(session)
    session.add(ChampionTag(champion_id=champion.champion_id, tag="burst", source="llm"))
    session.commit()

    assert query.effective_champion_tags(session, champion.champion_id) == ["burst"]


def test_effective_champion_tags_override_wins_over_llm(session: Session) -> None:
    champion = _make_champion(session)
    session.add(ChampionTag(champion_id=champion.champion_id, tag="burst", source="llm"))
    session.add(ChampionTag(champion_id=champion.champion_id, tag="tank", source="override"))
    session.commit()

    assert query.effective_champion_tags(session, champion.champion_id) == ["tank"]


def test_effective_champion_tags_ignores_data_dragon_coarse_tags(session: Session) -> None:
    champion = _make_champion(session)
    session.add(ChampionTag(champion_id=champion.champion_id, tag="Mage", source="data_dragon"))
    session.commit()

    assert query.effective_champion_tags(session, champion.champion_id) == []


def test_effective_champion_ratings_returns_whatever_row_exists(session: Session) -> None:
    champion = _make_champion(session)
    session.add(
        ChampionRating(champion_id=champion.champion_id, rating_name="engage", value=8.0, source="override")
    )
    session.add(
        ChampionRating(champion_id=champion.champion_id, rating_name="peel", value=3.0, source="llm")
    )
    session.commit()

    ratings = query.effective_champion_ratings(session, champion.champion_id)

    assert ratings == {"engage": 8.0, "peel": 3.0}


def test_effective_item_tags_override_wins(session: Session) -> None:
    item = _make_item(session)
    session.add(ItemTag(item_id=item.item_id, tag="sustained_dps", source="llm"))
    session.add(ItemTag(item_id=item.item_id, tag="scaling", source="override"))
    session.commit()

    assert query.effective_item_tags(session, item.item_id) == ["scaling"]
