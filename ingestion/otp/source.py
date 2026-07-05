"""OTP ingestion source: Component 3 (OTP DB, docs/sepc.md) - identifies
high-ELO one-trick mains and captures their per-match builds (starting
items, completed-item order, final build, runes, skill order, lane
opponent).

docs/sepc.md's literal source for this component is OneTricks.gg, "same ToS
caveat" as Lolalytics. OneTricks.gg's robots.txt returns HTTP 429 on every
fetch attempt (a harder block than Lolalytics, which at least served a
readable robots.txt with named-bot disallow rules) - so per the precedent
already set for item_counter_statistics (ingestion/riot_api/source.py),
this module derives the one-trick signal directly from the Riot API
instead of scraping OneTricks.gg: Champion-Mastery-V4 point concentration
identifies one-trick mains (no existing tool tracks "mastery" or
"one-trick" anywhere else in this codebase), then Match-V5 (the same
match/timeline endpoints ingestion/riot_api already uses) captures their
recent builds.
"""

from __future__ import annotations

from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import OTP
from db.models import OtpBuild, OtpPlayer, Patch
from ingestion.base import IngestionSource
from ingestion.riot_api import client, timeline
from ingestion.riot_api.identity import seed_challenger_puuids


class OtpSource(IngestionSource):
    name = "otp"

    def resolve_patch(self, patch: str | None) -> str:
        if patch is None:
            raise ValueError(
                "otp source requires an explicit --patch matching an "
                "already-ingested data_dragon version (Riot's match API has "
                "no 'latest patch' concept of its own)."
            )
        return patch

    def fetch(self, patch: str) -> dict[str, Any]:
        puuids = seed_challenger_puuids(self.warnings)

        one_tricks: list[dict[str, Any]] = []
        for puuid in puuids:
            try:
                masteries = client.fetch_champion_masteries(puuid)
            except requests.exceptions.RequestException as exc:
                self.warnings.append(f"Champion mastery lookup failed for a seed summoner: {exc}")
                continue

            candidate = _qualify_one_trick(puuid, masteries)
            if candidate is not None:
                one_tricks.append(candidate)

        one_tricks = one_tricks[: OTP.max_one_tricks_per_run]

        for candidate in one_tricks:
            puuid = candidate["puuid"]
            try:
                match_ids = client.fetch_match_ids(puuid, count=OTP.matches_per_one_trick)
            except requests.exceptions.RequestException as exc:
                self.warnings.append(f"Match id lookup failed for one-trick {puuid}: {exc}")
                match_ids = []

            matches = []
            timelines = []
            for match_id in match_ids:
                try:
                    match_data = client.fetch_match(match_id)
                except requests.exceptions.RequestException as exc:
                    self.warnings.append(f"Match fetch failed for {match_id}: {exc}")
                    continue

                try:
                    timeline_data = client.fetch_match_timeline(match_id)
                except requests.exceptions.RequestException as exc:
                    self.warnings.append(f"Timeline fetch failed for {match_id}: {exc}")
                    continue  # no timeline, nothing worth storing for this match

                matches.append(match_data)
                timelines.append(timeline_data)

            candidate["matches"] = matches
            candidate["timelines"] = timelines

        return {"one_tricks": one_tricks}

    def load(self, session: Session, patch: str, data: dict[str, Any]) -> dict[str, int]:
        patch_row = session.execute(
            select(Patch).where(Patch.version == patch)
        ).scalar_one_or_none()

        if patch_row is None:
            self.warnings.append(
                f"Patch '{patch}' not found in DB; storing builds with patch_id=None"
            )
        patch_id = patch_row.id if patch_row is not None else None
        patch_prefix = _major_minor(patch)

        terminal_item_ids = timeline.terminal_item_ids(session)

        new_player_count = 0
        new_build_count = 0
        skipped_count = 0

        for one_trick in data["one_tricks"]:
            puuid = one_trick["puuid"]
            champion_id = one_trick["champion_id"]

            player = session.execute(
                select(OtpPlayer).where(
                    OtpPlayer.puuid == puuid, OtpPlayer.primary_champion_id == champion_id
                )
            ).scalar_one_or_none()

            if player is None:
                player = OtpPlayer(
                    puuid=puuid,
                    primary_champion_id=champion_id,
                    role="",
                    patch_id=patch_id,
                    mastery_points=one_trick["mastery_points"],
                    mastery_concentration=one_trick["mastery_concentration"],
                    games_sampled=0,
                    win_rate=0.0,
                    sample_size=0,
                )
                session.add(player)
                session.flush()  # need player.id to FK new OtpBuild rows below
                new_player_count += 1
            else:
                player.patch_id = patch_id
                player.mastery_points = one_trick["mastery_points"]
                player.mastery_concentration = one_trick["mastery_concentration"]

            existing_match_ids = set(
                session.execute(
                    select(OtpBuild.match_id).where(OtpBuild.otp_player_id == player.id)
                ).scalars()
            )

            timelines_by_match_id = {
                timeline_data["metadata"]["matchId"]: timeline_data
                for timeline_data in one_trick["timelines"]
            }

            for match_data in one_trick["matches"]:
                match_id = match_data["metadata"]["matchId"]
                if match_id in existing_match_ids:
                    continue

                info = match_data["info"]
                if _major_minor(info["gameVersion"]) != patch_prefix:
                    skipped_count += 1
                    continue

                participants = info["participants"]
                participant = _find_participant(participants, puuid)
                if participant is None or participant["championId"] != champion_id:
                    skipped_count += 1
                    continue

                match_timeline = timelines_by_match_id.get(match_id)
                if match_timeline is None:
                    continue  # fetch() only pairs a match with a successfully-fetched timeline

                events = timeline.timeline_events(match_timeline["info"])
                participant_id = participants.index(participant) + 1

                completed_items = timeline.completed_item_order(events, terminal_item_ids).get(
                    participant_id, []
                )
                skill_order = timeline.skill_level_order(events).get(participant_id, [])

                session.add(
                    OtpBuild(
                        otp_player_id=player.id,
                        patch_id=patch_id,
                        match_id=match_id,
                        win=participant["win"],
                        role=participant.get("teamPosition", ""),
                        starting_items=_starting_item_purchases(events, participant_id),
                        completed_items=completed_items,
                        final_items=_final_items(participant),
                        primary_runes=_rune_selections(participant, style_index=0),
                        secondary_runes=_rune_selections(participant, style_index=1),
                        skill_order=[list(entry) for entry in skill_order],
                        enemy_champion_id=_lane_opponent(participants, participant),
                        raw_data=participant,
                    )
                )
                existing_match_ids.add(match_id)
                new_build_count += 1

            session.flush()
            _refresh_player_aggregate(session, player)

        if skipped_count:
            self.warnings.append(
                f"{skipped_count} sampled match(es) were off-patch or the player wasn't on "
                f"their primary champion that game, and were skipped."
            )

        return {
            "otp_players": new_player_count,
            "otp_builds": new_build_count,
        }


def _major_minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def _qualify_one_trick(puuid: str, masteries: list[dict]) -> dict[str, Any] | None:
    """A player qualifies as a one-trick on their single top-mastery
    champion when both absolute points and concentration (top champion's
    points / total points across all champions) clear OtpSettings'
    thresholds - see OtpSettings docstring for why both are required."""

    if not masteries:
        return None

    total_points = sum(mastery["championPoints"] for mastery in masteries)
    if total_points <= 0:
        return None

    top = max(masteries, key=lambda mastery: mastery["championPoints"])
    concentration = top["championPoints"] / total_points

    if top["championPoints"] < OTP.min_mastery_points:
        return None
    if concentration < OTP.min_mastery_concentration:
        return None

    return {
        "puuid": puuid,
        "champion_id": top["championId"],
        "mastery_points": top["championPoints"],
        "mastery_concentration": concentration,
    }


def _find_participant(participants: list[dict], puuid: str) -> dict | None:
    for participant in participants:
        if participant.get("puuid") == puuid:
            return participant
    return None


def _lane_opponent(participants: list[dict], participant: dict) -> int | None:
    """Same team_position + opposite `win` within the match - same pairing
    rule ingestion.riot_api.source's _recompute_champion_counters uses."""

    role = participant.get("teamPosition", "")
    win = participant["win"]
    for other in participants:
        if other is participant:
            continue
        if other.get("teamPosition", "") == role and other["win"] != win:
            return other["championId"]
    return None


def _final_items(participant: dict) -> list[int]:
    """item0..item5; item6 (trinket) excluded - same convention as
    ingestion.riot_api.source's _recompute_item_statistics."""

    return [
        item_id
        for slot in range(6)
        if (item_id := participant.get(f"item{slot}", 0)) > 0
    ]


def _rune_selections(participant: dict, style_index: int) -> list[int]:
    styles = participant.get("perks", {}).get("styles", [])
    if style_index >= len(styles):
        return []
    return [
        selection["perk"]
        for selection in styles[style_index].get("selections", [])
        if selection.get("perk")
    ]


def _starting_item_purchases(events: list[dict], participant_id: int) -> list[int]:
    """Items purchased before OtpSettings.starting_items_cutoff_ms for this
    participant, in chronological order - a fixed-cutoff approximation of
    "starting items" (Match-V5 has no first-recall event to key off
    instead). Nets out a straightforward undo the same way
    ingestion.riot_api.timeline.completed_item_order does, but keeps every
    item (components/consumables included - a starting buy is commonly a
    Doran's item plus potions/wards, neither of which counts as
    "terminal")."""

    relevant = (
        event
        for event in events
        if event["participantId"] == participant_id
        and event["type"] in ("ITEM_PURCHASED", "ITEM_UNDO")
        and event["timestamp"] < OTP.starting_items_cutoff_ms
    )
    order: list[int] = []
    for event in sorted(relevant, key=lambda event: event["timestamp"]):
        if event["type"] == "ITEM_PURCHASED":
            order.append(event["itemId"])
        else:  # ITEM_UNDO
            if order and order[-1] == event.get("beforeId"):
                order.pop()
    return order


def _refresh_player_aggregate(session: Session, player: OtpPlayer) -> None:
    """Recomputes win_rate/games_sampled/role from every OtpBuild row ever
    stored for this player (not just this run's newly-inserted subset) -
    otherwise a second run where this run's match ids were already stored
    would misleadingly collapse the sample to just the new delta. See
    OtpPlayer's docstring."""

    builds = session.execute(
        select(OtpBuild).where(OtpBuild.otp_player_id == player.id)
    ).scalars().all()
    if not builds:
        return

    player.games_sampled = len(builds)
    player.sample_size = len(builds)
    player.win_rate = sum(int(build.win) for build in builds) / len(builds)

    role_counts: dict[str, int] = {}
    for build in builds:
        role_counts[build.role] = role_counts.get(build.role, 0) + 1
    player.role = max(role_counts, key=role_counts.get)
