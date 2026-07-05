from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Champion, ChampionAbility, ChampionTag, Item, ItemTag, Rune
from ingestion.data_dragon import client
from ingestion.data_dragon.source import DataDragonSource

CHAMPION_FULL = {
    "id": "Ahri",
    "key": "103",
    "name": "Ahri",
    "title": "the Nine-Tailed Fox",
    "tags": ["Mage", "Assassin"],
    "stats": {
        "hp": 590, "hpperlevel": 92, "mp": 418, "mpperlevel": 25,
        "movespeed": 330, "armor": 21, "armorperlevel": 4.7,
        "spellblock": 30, "spellblockperlevel": 1.3, "attackrange": 550,
        "hpregen": 2.5, "hpregenperlevel": 0.6, "mpregen": 8,
        "mpregenperlevel": 0.8, "crit": 0, "critperlevel": 0,
        "attackdamage": 53, "attackdamageperlevel": 3,
        "attackspeedperlevel": 2, "attackspeed": 0.668,
    },
    "info": {"attack": 3, "defense": 4, "magic": 8, "difficulty": 5},
    "passive": {
        "name": "Essence Theft",
        "description": "Ahri gains stacks on ability hit.",
    },
    "spells": [
        {"id": "OrbOfDeception", "name": "Orb of Deception",
         "description": "Ahri sends out an orb.",
         "cooldown": [7, 6.5, 6, 5.5, 5], "cost": [65, 65, 65, 65, 65],
         "range": [880]},
        {"id": "Fox-Fire", "name": "Fox-Fire",
         "description": "Ahri summons three fox-fires.",
         "cooldown": [9], "cost": [45], "range": [700]},
        {"id": "Charm", "name": "Charm",
         "description": "Ahri blows a kiss.",
         "cooldown": [12], "cost": [60], "range": [975]},
        {"id": "SpiritRush", "name": "Spirit Rush",
         "description": "Ahri dashes forward.",
         "cooldown": [110, 90, 70], "cost": [100], "range": [400]},
    ],
}

ITEM_DATA = {
    "name": "Boots",
    "description": "Slightly increases Movement Speed.",
    "plaintext": "Slightly increases Movement Speed",
    "gold": {"base": 300, "total": 300, "sell": 210, "purchasable": True},
    "tags": ["Boots"],
    "maps": {"11": True, "12": True},
    "stats": {"FlatMovementSpeedMod": 25},
    "depth": 1,
    "into": ["1001", "1002"],
}

ARAM_ONLY_ITEM_DATA = {
    "name": "Suspicious ARAM-Only Trinket",
    "description": "Not available on Summoner's Rift.",
    "plaintext": "",
    "gold": {"base": 0, "total": 0, "sell": 0, "purchasable": True},
    "tags": [],
    "maps": {"11": False, "12": True},
}

RUNE_TREE = {
    "id": 8100,
    "key": "Domination",
    "name": "Domination",
    "slots": [
        {
            "runes": [
                {
                    "id": 8112,
                    "key": "Electrocute",
                    "name": "Electrocute",
                    "shortDesc": "Deal bonus damage.",
                    "longDesc": "Hitting a champion with 3 attacks or abilities...",
                }
            ]
        }
    ],
}


def fake_data() -> dict:
    return {
        "champions": [CHAMPION_FULL],
        "items": {"1001": ITEM_DATA},
        "rune_trees": [RUNE_TREE],
    }


def test_fetch_combines_client_calls(monkeypatch) -> None:
    monkeypatch.setattr(client, "fetch_champion_list", lambda patch: {"Ahri": {}})
    monkeypatch.setattr(
        client, "fetch_champion_full", lambda patch, riot_key: CHAMPION_FULL
    )
    monkeypatch.setattr(client, "fetch_items", lambda patch: {"1001": ITEM_DATA})
    monkeypatch.setattr(client, "fetch_rune_trees", lambda patch: [RUNE_TREE])

    data = DataDragonSource().fetch("14.14.1")

    assert data["champions"] == [CHAMPION_FULL]
    assert data["items"] == {"1001": ITEM_DATA}
    assert data["rune_trees"] == [RUNE_TREE]


def test_load_is_idempotent(session: Session) -> None:
    source = DataDragonSource()
    data = fake_data()

    counts_first = source.load(session, "14.14.1", data)
    session.commit()
    counts_second = source.load(session, "14.14.1", data)
    session.commit()

    assert counts_first == counts_second == {"champions": 1, "items": 1, "runes": 1}

    assert session.execute(select(Champion)).scalars().all().__len__() == 1
    assert session.execute(select(Item)).scalars().all().__len__() == 1
    assert session.execute(select(Rune)).scalars().all().__len__() == 1

    ahri = session.execute(
        select(Champion).where(Champion.riot_key == "Ahri")
    ).scalar_one()
    assert len(ahri.abilities) == 5  # passive + Q/W/E/R, not duplicated
    assert {t.tag for t in ahri.tags} == {"Mage", "Assassin"}

    boots = session.execute(select(Item).where(Item.item_id == 1001)).scalar_one()
    assert {t.tag for t in boots.tags} == {"Boots"}
    assert boots.stats == {"FlatMovementSpeedMod": 25}
    assert boots.depth == 1
    assert boots.builds_from == []
    assert boots.builds_into == ["1001", "1002"]

    rune = session.execute(select(Rune).where(Rune.rune_id == 8112)).scalar_one()
    assert rune.path_name == "Domination"
    assert rune.name == "Electrocute"


def test_load_skips_non_summoners_rift_items(session: Session) -> None:
    source = DataDragonSource()
    data = {
        "champions": [],
        "items": {"1001": ITEM_DATA, "9999": ARAM_ONLY_ITEM_DATA},
        "rune_trees": [],
    }

    counts = source.load(session, "14.14.1", data)

    assert counts["items"] == 1
    names = {item.name for item in session.execute(select(Item)).scalars().all()}
    assert names == {"Boots"}


def test_load_champion_tags_not_duplicated_across_sources(session: Session) -> None:
    source = DataDragonSource()
    data = fake_data()
    source.load(session, "14.14.1", data)
    session.commit()

    session.add(ChampionTag(champion_id=103, tag="engage", source="llm"))
    session.commit()

    source.load(session, "14.14.1", data)
    session.commit()

    tags = session.execute(
        select(ChampionTag).where(ChampionTag.champion_id == 103)
    ).scalars().all()
    sources_seen = {(t.tag, t.source) for t in tags}
    assert sources_seen == {("Mage", "data_dragon"), ("Assassin", "data_dragon"), ("engage", "llm")}
