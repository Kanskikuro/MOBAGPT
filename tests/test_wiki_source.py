import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Champion, ChampionAbilityDetail, Item, ItemWikiDetail, Patch
from ingestion.wiki import champions as wiki_champions
from ingestion.wiki import items as wiki_items
from ingestion.wiki.client import WikiPageNotFoundError
from ingestion.wiki.source import WikiSource
from ingestion.wiki.wikitext import ParsedWikiTemplate


def _fake_abilities(champion_name: str) -> wiki_champions.ChampionAbilities:
    return wiki_champions.ChampionAbilities(
        champion_display_name=champion_name,
        abilities={
            "Q": ParsedWikiTemplate(
                fields={"champion": champion_name, "skill": "Q", "cooldown": "7"},
                notes="Some mechanical note.",
                raw_wikitext="{{Ability data|...}}",
                tips="A build-specific combo tip.",
                unknown_templates={"fd"},
            ),
        },
        wiki_titles={"Q": f"Template:Data {champion_name}/Some Ability"},
    )


def _fake_item_wiki_data(item_name: str) -> wiki_items.ItemWikiData:
    return wiki_items.ItemWikiData(
        parsed=ParsedWikiTemplate(
            fields={"goldvalue": "3400"},
            notes="Quest charge details.",
            raw_wikitext="{{Item info|...}}",
            tips="",
            unknown_templates={"sm2"},
        ),
        wiki_title=item_name,
    )


def _patch_champion_fetch(monkeypatch, champions: dict[str, dict]) -> None:
    monkeypatch.setattr(
        "ingestion.wiki.source.fetch_champion_list", lambda patch: champions
    )


def _patch_item_fetch(monkeypatch, items: dict[str, dict]) -> None:
    monkeypatch.setattr("ingestion.wiki.source.fetch_items", lambda patch: items)


def test_resolve_patch_passthrough_and_sentinel() -> None:
    source = WikiSource()
    assert source.resolve_patch("14.14.1") == "14.14.1"
    assert source.resolve_patch(None) == "unversioned"


def test_fetch_requires_explicit_patch() -> None:
    source = WikiSource()
    with pytest.raises(ValueError):
        source.fetch("unversioned")


def test_fetch_combines_champion_roster_and_abilities(monkeypatch) -> None:
    _patch_champion_fetch(monkeypatch, {"Ahri": {"name": "Ahri"}})
    _patch_item_fetch(monkeypatch, {})
    monkeypatch.setattr(
        wiki_champions, "fetch_champion_abilities", lambda name: _fake_abilities(name)
    )

    data = WikiSource().fetch("14.14.1")

    assert len(data["champions"]) == 1
    assert data["champions"][0]["riot_key"] == "Ahri"
    assert data["champions"][0]["abilities"].abilities["Q"].fields["cooldown"] == "7"


def test_fetch_records_page_not_found_as_error_entry(monkeypatch) -> None:
    _patch_champion_fetch(monkeypatch, {"Ahri": {"name": "Ahri"}})
    _patch_item_fetch(monkeypatch, {})

    def _raise(name):
        raise WikiPageNotFoundError(f"{name}/LoL#Abilities")

    monkeypatch.setattr(wiki_champions, "fetch_champion_abilities", _raise)

    data = WikiSource().fetch("14.14.1")

    assert "error" in data["champions"][0]


def test_fetch_items_skips_non_summoners_rift_and_fetches_wiki_data(monkeypatch) -> None:
    _patch_champion_fetch(monkeypatch, {})
    _patch_item_fetch(
        monkeypatch,
        {
            "3865": {
                "name": "World Atlas",
                "maps": {"11": True},
                "gold": {"purchasable": True},
            },
            "9999": {
                "name": "ARAM Only Trinket",
                "maps": {"11": False},
                "gold": {"purchasable": True},
            },
        },
    )
    monkeypatch.setattr(
        wiki_items, "fetch_item_wiki_data", lambda name: _fake_item_wiki_data(name)
    )

    data = WikiSource().fetch("14.14.1")

    assert len(data["items"]) == 1
    assert data["items"][0]["item_id"] == 3865
    assert data["items"][0]["wiki_data"].parsed.notes == "Quest charge details."


def test_load_is_idempotent(session: Session) -> None:
    patch = Patch(version="14.14.1")
    session.add(patch)
    session.flush()

    champion = Champion(
        champion_id=103,
        riot_key="Ahri",
        display_name="Ahri",
        normalized_name="ahri",
        title="the Nine-Tailed Fox",
        patch_id=patch.id,
    )
    item = Item(
        item_id=3865,
        name="World Atlas",
        description="",
        plaintext="",
        gold_base=400,
        gold_total=400,
        gold_sell=160,
        patch_id=patch.id,
        raw_data={},
    )
    session.add_all([champion, item])
    session.commit()

    source = WikiSource()
    source.warnings = []
    data = {
        "champions": [
            {"riot_key": "Ahri", "display_name": "Ahri", "abilities": _fake_abilities("Ahri")}
        ],
        "items": [
            {"item_id": 3865, "name": "World Atlas", "wiki_data": _fake_item_wiki_data("World Atlas")}
        ],
    }

    counts_first = source.load(session, "14.14.1", data)
    session.commit()
    counts_second = source.load(session, "14.14.1", data)
    session.commit()

    expected = {"champion_ability_details": 1, "item_wiki_details": 1}
    assert counts_first == counts_second == expected

    ability_rows = session.execute(select(ChampionAbilityDetail)).scalars().all()
    assert len(ability_rows) == 1
    assert ability_rows[0].notes == "Some mechanical note."
    assert ability_rows[0].tips == "A build-specific combo tip."

    item_rows = session.execute(select(ItemWikiDetail)).scalars().all()
    assert len(item_rows) == 1
    assert item_rows[0].notes == "Quest charge details."
    assert item_rows[0].wiki_title == "World Atlas"


def test_load_skips_champion_not_in_db_and_warns(session: Session) -> None:
    patch = Patch(version="14.14.1")
    session.add(patch)
    session.commit()

    source = WikiSource()
    source.warnings = []
    data = {
        "champions": [
            {"riot_key": "Nobody", "display_name": "Nobody", "abilities": _fake_abilities("Nobody")}
        ],
        "items": [],
    }

    counts = source.load(session, "14.14.1", data)

    assert counts == {"champion_ability_details": 0, "item_wiki_details": 0}
    assert any("Nobody" in warning for warning in source.warnings)
    assert session.execute(select(ChampionAbilityDetail)).scalars().all() == []


def test_load_skips_item_not_in_db_and_warns(session: Session) -> None:
    patch = Patch(version="14.14.1")
    session.add(patch)
    session.commit()

    source = WikiSource()
    source.warnings = []
    data = {
        "champions": [],
        "items": [
            {"item_id": 404, "name": "Nonexistent Item", "wiki_data": _fake_item_wiki_data("Nonexistent Item")}
        ],
    }

    counts = source.load(session, "14.14.1", data)

    assert counts == {"champion_ability_details": 0, "item_wiki_details": 0}
    assert any("Nonexistent Item" in warning for warning in source.warnings)
    assert session.execute(select(ItemWikiDetail)).scalars().all() == []


def test_load_warns_on_missing_patch_but_still_loads(session: Session) -> None:
    champion = Champion(
        champion_id=103,
        riot_key="Ahri",
        display_name="Ahri",
        normalized_name="ahri",
        title="the Nine-Tailed Fox",
    )
    session.add(champion)
    session.commit()

    source = WikiSource()
    source.warnings = []
    data = {
        "champions": [
            {"riot_key": "Ahri", "display_name": "Ahri", "abilities": _fake_abilities("Ahri")}
        ],
        "items": [],
    }

    counts = source.load(session, "99.99.9", data)

    assert counts == {"champion_ability_details": 1, "item_wiki_details": 0}
    assert any("99.99.9" in warning for warning in source.warnings)
