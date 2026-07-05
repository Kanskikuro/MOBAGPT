from sqlalchemy.orm import Session

from db.models import Item, ItemTag
from ingestion.riot_api.timeline import completed_item_order, skill_level_order, terminal_item_ids

RABADONS = 3089
LONG_SWORD = 1036
HEALTH_POTION = 2003
INFINITY_EDGE = 3031


def _purchase(participant_id: int, item_id: int, timestamp: int) -> dict:
    return {
        "type": "ITEM_PURCHASED",
        "timestamp": timestamp,
        "participantId": participant_id,
        "itemId": item_id,
    }


def _undo(participant_id: int, before_id: int, timestamp: int) -> dict:
    return {
        "type": "ITEM_UNDO",
        "timestamp": timestamp,
        "participantId": participant_id,
        "beforeId": before_id,
        "afterId": 0,
    }


def _skill_level_up(
    participant_id: int, skill_slot: int, timestamp: int, level_up_type: str = "NORMAL"
) -> dict:
    return {
        "type": "SKILL_LEVEL_UP",
        "timestamp": timestamp,
        "participantId": participant_id,
        "skillSlot": skill_slot,
        "levelUpType": level_up_type,
    }


def test_completed_item_order_filters_components_and_consumables() -> None:
    terminal_ids = {RABADONS, INFINITY_EDGE}
    events = [
        _purchase(1, LONG_SWORD, 1000),  # component - not in terminal_ids, excluded
        _purchase(1, HEALTH_POTION, 1500),  # consumable - not in terminal_ids, excluded
        _purchase(1, RABADONS, 5000),
        _purchase(1, INFINITY_EDGE, 9000),
    ]

    order = completed_item_order(events, terminal_ids)

    assert order[1] == [RABADONS, INFINITY_EDGE]


def test_completed_item_order_nets_out_a_straightforward_undo() -> None:
    terminal_ids = {RABADONS, INFINITY_EDGE}
    events = [
        _purchase(1, RABADONS, 1000),
        _undo(1, RABADONS, 1100),  # immediately undone - shouldn't count
        _purchase(1, INFINITY_EDGE, 2000),
    ]

    order = completed_item_order(events, terminal_ids)

    assert order[1] == [INFINITY_EDGE]


def test_skill_level_order_tracks_slot_and_level_up_type_in_order() -> None:
    events = [
        _skill_level_up(1, 2, 2000),  # deliberately out of timestamp order
        _skill_level_up(1, 1, 1000),
        _skill_level_up(1, 1, 3000),
        _skill_level_up(1, 4, 4000, level_up_type="EVOLVE"),
    ]

    order = skill_level_order(events)

    assert order[1] == [(1, "NORMAL"), (2, "NORMAL"), (1, "NORMAL"), (4, "EVOLVE")]


def test_terminal_item_ids_excludes_components_and_consumables(session: Session) -> None:
    session.add_all(
        [
            Item(
                item_id=RABADONS,
                name="Rabadon's Deathcap",
                description="",
                plaintext="",
                gold_base=1600,
                gold_total=3500,
                gold_sell=2450,
                raw_data={},
            ),
            Item(
                item_id=LONG_SWORD,
                name="Long Sword",
                description="",
                plaintext="",
                gold_base=350,
                gold_total=350,
                gold_sell=140,
                raw_data={},
                builds_into=[str(RABADONS)],  # non-empty - a component, not terminal
            ),
            Item(
                item_id=HEALTH_POTION,
                name="Health Potion",
                description="",
                plaintext="",
                gold_base=50,
                gold_total=50,
                gold_sell=0,
                raw_data={},
            ),
            ItemTag(item_id=HEALTH_POTION, tag="Consumable"),
        ]
    )
    session.commit()

    assert terminal_item_ids(session) == {RABADONS}
