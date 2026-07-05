"""Match-V5 *participant summary* dict parsing (as opposed to
ingestion.riot_api.timeline's frame-by-frame *timeline event* parsing),
shared by ingestion.otp.source (per-match final build capture) and
knowledge.archetypes.builds (reconstructing per-game builds from the
Challenger-aggregate match_participants population, since ItemStatistics/
RuneStatistics only store item-marginal/rune-marginal aggregates, not each
game's joint item+rune set). Both callers pass a plain participant dict - a
freshly-fetched Match-V5 participant, or a stored `MatchParticipant.raw_data`
row alike, since that column is the same dict verbatim.
"""

from __future__ import annotations


def final_items(participant: dict) -> list[int]:
    """item0..item5; item6 (trinket) excluded - same convention as
    ingestion.riot_api.source's _recompute_item_statistics."""

    return [
        item_id
        for slot in range(6)
        if (item_id := participant.get(f"item{slot}", 0)) > 0
    ]


def rune_selections(participant: dict, style_index: int) -> list[int]:
    """style_index 0 = primary tree, 1 = secondary tree - selections[0] of
    style_index 0 is the keystone, same convention as ingestion.riot_api.
    source's _recompute_rune_statistics."""

    styles = participant.get("perks", {}).get("styles", [])
    if style_index >= len(styles):
        return []
    return [
        selection["perk"]
        for selection in styles[style_index].get("selections", [])
        if selection.get("perk")
    ]
