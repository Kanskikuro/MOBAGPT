"""Match-V5 timeline event parsing, shared by ingestion.riot_api.source
(build_path_statistics/skill_order_statistics) and ingestion.otp.source
(per-match completed-item order, skill order, and terminal-item filtering
for one-trick builds). Deliberately decoupled from the MatchTimeline ORM
row - both callers pass a plain `info` dict (a stored row's
`.raw_data["info"]`, or a freshly-fetched, not-yet-persisted timeline
payload's `["info"]` alike).
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Item, ItemTag


def terminal_item_ids(session: Session) -> set[int]:
    """Items that represent a real 'completed' purchase for build-path
    purposes. Item.depth alone isn't reliable - checked against real data,
    plenty of genuine components (Long Sword, Boots, Cloth Armor) *and*
    genuine standalone final items (Doran's items, jungle pets) both have
    depth=NULL in Data Dragon. The reliable signal is builds_into being
    empty (nothing upgrades from this item - covers legendaries, tier-2
    boots that have no further enchant, and 2024+ boot-enchant items alike)
    combined with excluding item_tags' Consumable/Trinket tags (potions,
    wards, elixirs also have empty builds_into but aren't a 'build' pick)."""

    excluded_tags = ("Consumable", "Trinket")
    excluded_ids = set(
        session.execute(select(ItemTag.item_id).where(ItemTag.tag.in_(excluded_tags))).scalars()
    )
    return {
        item_id
        for item_id, builds_into in session.execute(select(Item.item_id, Item.builds_into))
        if not builds_into and item_id not in excluded_ids
    }


def completed_item_order(
    events: list[dict], terminal_item_ids: set[int]
) -> dict[int, list[int]]:
    """Per participantId, terminal items purchased in chronological order.
    Nets out a straightforward 'undo the last terminal purchase'
    (ITEM_UNDO.beforeId matching that participant's most recent recorded
    item); undoing a sale or an out-of-order undo isn't modeled - known
    simplification, same spirit as this module's other documented gaps
    (e.g. ban role attribution)."""

    order: dict[int, list[int]] = defaultdict(list)
    relevant = (e for e in events if e["type"] in ("ITEM_PURCHASED", "ITEM_UNDO"))
    for event in sorted(relevant, key=lambda e: e["timestamp"]):
        participant_id = event["participantId"]
        if event["type"] == "ITEM_PURCHASED":
            if event["itemId"] in terminal_item_ids:
                order[participant_id].append(event["itemId"])
        else:  # ITEM_UNDO
            sequence = order.get(participant_id)
            if sequence and sequence[-1] == event.get("beforeId"):
                sequence.pop()

    return order


def skill_level_order(events: list[dict]) -> dict[int, list[tuple[int, str]]]:
    """Per participantId, (skill_slot, level_up_type) in the order the
    points were spent. The champion level for the Nth entry is N - one
    skill point is spent per level and Riot's SKILL_LEVEL_UP event carries
    no level field of its own."""

    by_participant: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
    for event in events:
        if event["type"] != "SKILL_LEVEL_UP":
            continue
        by_participant[event["participantId"]].append(
            (event["timestamp"], event["skillSlot"], event.get("levelUpType", "NORMAL"))
        )

    return {
        participant_id: [(slot, level_up_type) for _, slot, level_up_type in sorted(entries)]
        for participant_id, entries in by_participant.items()
    }


def timeline_events(timeline_info: dict) -> list[dict]:
    frames = timeline_info.get("frames", [])
    return [event for frame in frames for event in frame.get("events", [])]
