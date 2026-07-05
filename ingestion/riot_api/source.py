"""Riot API ingestion source: high-ELO ranked-solo match capture, plus the
derived per-(patch, champion, role) win/pick/ban rate table
(matchup_statistics), the ally/enemy pairing tables (champion_synergy,
champion_counters), final-build item/rune win rates (item_statistics,
rune_statistics), per-matchup "itemization counter" win rates
(item_counter_statistics), and completed-item purchase order / skill
leveling order (build_path_statistics, skill_order_statistics) from the
Match-V5 timeline endpoint.
"""

from __future__ import annotations

import datetime
import itertools
from collections import defaultdict
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import RIOT_API
from db.models import (
    BuildPathStatistics,
    ChampionCounters,
    ChampionSynergy,
    ItemCounterStatistics,
    ItemStatistics,
    Match,
    MatchParticipant,
    MatchTimeline,
    MatchupStatistics,
    Patch,
    RuneStatistics,
    SkillOrderStatistics,
)
from ingestion.base import IngestionSource
from ingestion.riot_api import client, timeline
from ingestion.riot_api.identity import seed_challenger_puuids


class RiotApiSource(IngestionSource):
    name = "riot_api"

    def resolve_patch(self, patch: str | None) -> str:
        if patch is None:
            raise ValueError(
                "riot_api source requires an explicit --patch matching an "
                "already-ingested data_dragon version (Riot's match API has "
                "no 'latest patch' concept of its own)."
            )
        return patch

    def fetch(self, patch: str) -> dict[str, Any]:
        puuids = seed_challenger_puuids(self.warnings)

        match_ids: set[str] = set()
        for puuid in puuids:
            try:
                match_ids.update(client.fetch_match_ids(puuid))
            except requests.exceptions.RequestException as exc:
                self.warnings.append(f"Match id lookup failed for a seed summoner: {exc}")

        matches = []
        timelines = []
        for match_id in match_ids:
            try:
                matches.append(client.fetch_match(match_id))
            except requests.exceptions.RequestException as exc:
                self.warnings.append(f"Match fetch failed for {match_id}: {exc}")
                continue  # nothing to attach a timeline to

            try:
                timelines.append(client.fetch_match_timeline(match_id))
            except requests.exceptions.RequestException as exc:
                self.warnings.append(f"Timeline fetch failed for {match_id}: {exc}")

        return {"matches": matches, "timelines": timelines}

    def load(self, session: Session, patch: str, data: dict[str, Any]) -> dict[str, int]:
        patch_row = session.execute(
            select(Patch).where(Patch.version == patch)
        ).scalar_one_or_none()

        if patch_row is None:
            self.warnings.append(
                f"Patch '{patch}' not found in DB; storing matches with patch_id=None"
            )
        patch_id = patch_row.id if patch_row is not None else None
        patch_prefix = _major_minor(patch)

        existing_match_ids = set(session.execute(select(Match.match_id)).scalars())

        new_match_count = 0
        new_participant_count = 0
        off_patch_count = 0

        for match_data in data["matches"]:
            match_id = match_data["metadata"]["matchId"]
            if match_id in existing_match_ids:
                continue

            info = match_data["info"]
            if _major_minor(info["gameVersion"]) != patch_prefix:
                off_patch_count += 1
                continue

            session.add(
                Match(
                    match_id=match_id,
                    patch_id=patch_id,
                    queue_id=info["queueId"],
                    game_duration=info["gameDuration"],
                    game_creation=datetime.datetime.fromtimestamp(
                        info["gameCreation"] / 1000, tz=datetime.UTC
                    ),
                    region=RIOT_API.region,
                    raw_data=match_data,
                )
            )
            new_match_count += 1

            for participant in info["participants"]:
                session.add(
                    MatchParticipant(
                        match_id=match_id,
                        champion_id=participant["championId"],
                        team_position=participant.get("teamPosition", ""),
                        win=participant["win"],
                        kills=participant["kills"],
                        deaths=participant["deaths"],
                        assists=participant["assists"],
                        raw_data=participant,
                    )
                )
                new_participant_count += 1

            existing_match_ids.add(match_id)

        if off_patch_count:
            self.warnings.append(
                f"{off_patch_count} fetched match(es) were off-patch (game "
                f"version prefix != '{patch_prefix}') and were skipped."
            )

        existing_timeline_ids = set(session.execute(select(MatchTimeline.match_id)).scalars())
        new_timeline_count = 0

        for timeline_data in data.get("timelines", []):
            match_id = timeline_data["metadata"]["matchId"]
            # Only store a timeline for a match that actually has (or now
            # has) a Match row - an off-patch or otherwise-skipped match
            # has none, and match_timelines.match_id is FK'd to matches.
            if match_id in existing_timeline_ids or match_id not in existing_match_ids:
                continue
            session.add(MatchTimeline(match_id=match_id, raw_data=timeline_data))
            existing_timeline_ids.add(match_id)
            new_timeline_count += 1

        session.flush()

        if patch_id is not None:
            patch_matches = session.execute(
                select(Match).where(Match.patch_id == patch_id)
            ).scalars().all()
            patch_participants = (
                session.execute(
                    select(MatchParticipant).where(
                        MatchParticipant.match_id.in_([m.match_id for m in patch_matches])
                    )
                )
                .scalars()
                .all()
            )
            participants_by_match: dict[str, list[MatchParticipant]] = defaultdict(list)
            for participant in patch_participants:
                participants_by_match[participant.match_id].append(participant)

            patch_timelines = (
                session.execute(
                    select(MatchTimeline).where(
                        MatchTimeline.match_id.in_([m.match_id for m in patch_matches])
                    )
                )
                .scalars()
                .all()
            )
            matches_by_id = {m.match_id: m for m in patch_matches}

            stats_count = _recompute_matchup_statistics(session, patch_id, patch_matches, patch_participants)
            synergy_count = _recompute_champion_synergy(session, patch_id, participants_by_match)
            counters_count = _recompute_champion_counters(session, patch_id, participants_by_match)
            item_count = _recompute_item_statistics(session, patch_id, patch_participants)
            rune_count = _recompute_rune_statistics(session, patch_id, patch_participants)
            item_counter_count = _recompute_item_counter_statistics(
                session, patch_id, participants_by_match
            )
            build_path_count = _recompute_build_path_statistics(
                session, patch_id, patch_participants, matches_by_id, patch_timelines
            )
            skill_order_count = _recompute_skill_order_statistics(
                session, patch_id, patch_participants, matches_by_id, patch_timelines
            )
        else:
            stats_count = synergy_count = counters_count = item_count = rune_count = 0
            item_counter_count = build_path_count = skill_order_count = 0

        return {
            "matches": new_match_count,
            "match_participants": new_participant_count,
            "match_timelines": new_timeline_count,
            "matchup_statistics": stats_count,
            "champion_synergy": synergy_count,
            "champion_counters": counters_count,
            "item_statistics": item_count,
            "rune_statistics": rune_count,
            "item_counter_statistics": item_counter_count,
            "build_path_statistics": build_path_count,
            "skill_order_statistics": skill_order_count,
        }


def _major_minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def _recompute_matchup_statistics(
    session: Session,
    patch_id: int,
    matches: list[Match],
    participants: list[MatchParticipant],
) -> int:
    """Full recompute from match_participants/matches for this patch - a
    derived aggregate, not a natural upsert, so it's deleted-and-reinserted
    rather than incrementally merged (same rationale as
    ingestion/data_dragon's child-collection delete-then-reinsert)."""

    session.execute(
        MatchupStatistics.__table__.delete().where(MatchupStatistics.patch_id == patch_id)
    )

    total_games = len(matches)
    if total_games == 0:
        return 0

    # Bans have no role attribution in Riot's data (a ban happens before
    # role assignment) - counted per champion for the patch, then the same
    # value is written onto every per-role row for that champion below.
    ban_counts: dict[int, int] = {}
    for match in matches:
        for team in match.raw_data["info"]["teams"]:
            for ban in team.get("bans", []):
                champion_id = ban.get("championId", -1)
                if champion_id > 0:
                    ban_counts[champion_id] = ban_counts.get(champion_id, 0) + 1

    role_stats: dict[tuple[int, str], dict[str, int]] = {}
    for participant in participants:
        key = (participant.champion_id, participant.team_position)
        bucket = role_stats.setdefault(key, {"picks": 0, "wins": 0})
        bucket["picks"] += 1
        bucket["wins"] += int(participant.win)

    slots_per_game = total_games * 2  # two team slots per role per game
    row_count = 0

    for (champion_id, role), bucket in role_stats.items():
        picks = bucket["picks"]
        wins = bucket["wins"]
        bans = ban_counts.get(champion_id, 0)

        session.add(
            MatchupStatistics(
                patch_id=patch_id,
                champion_id=champion_id,
                role=role,
                games=total_games,
                wins=wins,
                picks=picks,
                bans=bans,
                win_rate=wins / picks if picks else 0.0,
                pick_rate=picks / slots_per_game,
                ban_rate=bans / slots_per_game,
                sample_size=picks,
            )
        )
        row_count += 1

    return row_count


def _recompute_champion_synergy(
    session: Session,
    patch_id: int,
    participants_by_match: dict[str, list[MatchParticipant]],
) -> int:
    """Full recompute, delete-then-reinsert per patch (same rationale as
    _recompute_matchup_statistics). Teammates are found via `win` rather
    than the raw `teamId` in raw_data: queue 420 (ranked solo/duo, the only
    queue this source ingests) always has exactly two opposing sides with
    `win` uniform within a side, so same win value in the same match =
    teammates. This assumption would break for a non-two-team queue (e.g.
    Arena) - not in scope."""

    session.execute(ChampionSynergy.__table__.delete().where(ChampionSynergy.patch_id == patch_id))

    pairs: dict[tuple[int, str, int, str], dict[str, int]] = {}
    for participants in participants_by_match.values():
        for side in (True, False):
            teammates = [p for p in participants if p.win is side]
            for p1, p2 in itertools.combinations(teammates, 2):
                a, b = sorted((p1, p2), key=lambda p: p.champion_id)
                key = (a.champion_id, a.team_position, b.champion_id, b.team_position)
                bucket = pairs.setdefault(key, {"games": 0, "wins": 0})
                bucket["games"] += 1
                bucket["wins"] += int(side)

    for (champion_id_a, role_a, champion_id_b, role_b), bucket in pairs.items():
        games = bucket["games"]
        session.add(
            ChampionSynergy(
                patch_id=patch_id,
                champion_id_a=champion_id_a,
                role_a=role_a,
                champion_id_b=champion_id_b,
                role_b=role_b,
                games=games,
                wins=bucket["wins"],
                win_rate=bucket["wins"] / games,
                sample_size=games,
            )
        )

    return len(pairs)


def _recompute_champion_counters(
    session: Session,
    patch_id: int,
    participants_by_match: dict[str, list[MatchParticipant]],
) -> int:
    """Full recompute, delete-then-reinsert per patch. Directional: both
    (A vs B) and (B vs A) rows are stored explicitly (see ChampionCounters
    docstring) rather than requiring a reverse-lookup convention downstream.
    Opponents are found via `win` mismatch within the same match - same
    caveat as _recompute_champion_synergy."""

    session.execute(
        ChampionCounters.__table__.delete().where(ChampionCounters.patch_id == patch_id)
    )

    pairs: dict[tuple[int, str, int, str], dict[str, int]] = {}
    for participants in participants_by_match.values():
        for p, q in itertools.product(participants, repeat=2):
            if q.win is p.win:
                continue  # teammate or self, not an opponent
            key = (p.champion_id, p.team_position, q.champion_id, q.team_position)
            bucket = pairs.setdefault(key, {"games": 0, "wins": 0})
            bucket["games"] += 1
            bucket["wins"] += int(p.win)

    for (champion_id, role, enemy_champion_id, enemy_role), bucket in pairs.items():
        games = bucket["games"]
        session.add(
            ChampionCounters(
                patch_id=patch_id,
                champion_id=champion_id,
                role=role,
                enemy_champion_id=enemy_champion_id,
                enemy_role=enemy_role,
                games=games,
                wins=bucket["wins"],
                win_rate=bucket["wins"] / games,
                sample_size=games,
            )
        )

    return len(pairs)


def _recompute_item_statistics(
    session: Session, patch_id: int, participants: list[MatchParticipant]
) -> int:
    """Full recompute, delete-then-reinsert per patch. Final build only -
    item0..item5 (item6/trinket excluded, near-uniform per role and not
    informative for build comparison). `games` is champion+role's own game
    count this patch (not the total patch game count matchup_statistics
    uses), since `pick_rate` here means "build rate given this champion+role",
    not "share of all role slots patch-wide"."""

    session.execute(ItemStatistics.__table__.delete().where(ItemStatistics.patch_id == patch_id))

    role_games: dict[tuple[int, str], int] = {}
    buckets: dict[tuple[int, str, int], dict[str, int]] = {}
    for participant in participants:
        role_key = (participant.champion_id, participant.team_position)
        role_games[role_key] = role_games.get(role_key, 0) + 1

        for slot in range(6):  # item0..item5; item6 (trinket) excluded
            item_id = participant.raw_data.get(f"item{slot}", 0)
            if item_id <= 0:
                continue
            key = (participant.champion_id, participant.team_position, item_id)
            bucket = buckets.setdefault(key, {"picks": 0, "wins": 0})
            bucket["picks"] += 1
            bucket["wins"] += int(participant.win)

    for (champion_id, role, item_id), bucket in buckets.items():
        games = role_games[(champion_id, role)]
        picks = bucket["picks"]
        session.add(
            ItemStatistics(
                patch_id=patch_id,
                champion_id=champion_id,
                role=role,
                item_id=item_id,
                games=games,
                picks=picks,
                wins=bucket["wins"],
                win_rate=bucket["wins"] / picks,
                pick_rate=picks / games,
                sample_size=picks,
            )
        )

    return len(buckets)


def _recompute_item_counter_statistics(
    session: Session,
    patch_id: int,
    participants_by_match: dict[str, list[MatchParticipant]],
) -> int:
    """Full recompute, delete-then-reinsert per patch. "Itemization
    counters" - combines _recompute_champion_counters' opponent-pairing
    (same win-mismatch-within-match rule, same two-opposing-sides
    assumption) with _recompute_item_statistics' final-build reading
    (item0..item5; item6/trinket excluded). Unlike ItemStatistics, `games`
    is this champion+role's game count against this *specific*
    enemy_champion+role (the same denominator ChampionCounters.games
    uses), not the champion+role's total patch games - so `pick_rate`
    reads as "build rate given this specific matchup"."""

    session.execute(
        ItemCounterStatistics.__table__.delete().where(ItemCounterStatistics.patch_id == patch_id)
    )

    matchup_games: dict[tuple[int, str, int, str], int] = {}
    buckets: dict[tuple[int, str, int, int, str], dict[str, int]] = {}

    for participants in participants_by_match.values():
        for p, q in itertools.product(participants, repeat=2):
            if q.win is p.win:
                continue  # teammate or self, not an opponent

            matchup_key = (p.champion_id, p.team_position, q.champion_id, q.team_position)
            matchup_games[matchup_key] = matchup_games.get(matchup_key, 0) + 1

            for slot in range(6):  # item0..item5; item6 (trinket) excluded
                item_id = p.raw_data.get(f"item{slot}", 0)
                if item_id <= 0:
                    continue
                key = (p.champion_id, p.team_position, item_id, q.champion_id, q.team_position)
                bucket = buckets.setdefault(key, {"picks": 0, "wins": 0})
                bucket["picks"] += 1
                bucket["wins"] += int(p.win)

    for (champion_id, role, item_id, enemy_champion_id, enemy_role), bucket in buckets.items():
        games = matchup_games[(champion_id, role, enemy_champion_id, enemy_role)]
        picks = bucket["picks"]
        session.add(
            ItemCounterStatistics(
                patch_id=patch_id,
                champion_id=champion_id,
                role=role,
                item_id=item_id,
                enemy_champion_id=enemy_champion_id,
                enemy_role=enemy_role,
                games=games,
                picks=picks,
                wins=bucket["wins"],
                win_rate=bucket["wins"] / picks,
                pick_rate=picks / games,
                sample_size=picks,
            )
        )

    return len(buckets)


def _recompute_rune_statistics(
    session: Session, patch_id: int, participants: list[MatchParticipant]
) -> int:
    """Full recompute, delete-then-reinsert per patch. Covers both rune
    paths' selections (6 total per participant); stat-shard perks
    (statPerks.offense/flex/defense) are excluded - see RuneStatistics
    docstring. `games`/`pick_rate` follow the same champion+role-scoped
    convention as _recompute_item_statistics."""

    session.execute(RuneStatistics.__table__.delete().where(RuneStatistics.patch_id == patch_id))

    role_games: dict[tuple[int, str], int] = {}
    buckets: dict[tuple[int, str, int], dict[str, int]] = {}
    keystone_ids: set[int] = set()

    for participant in participants:
        role_key = (participant.champion_id, participant.team_position)
        role_games[role_key] = role_games.get(role_key, 0) + 1

        styles = participant.raw_data.get("perks", {}).get("styles", [])
        for style_index, style in enumerate(styles):
            for selection_index, selection in enumerate(style.get("selections", [])):
                rune_id = selection.get("perk")
                if not rune_id:
                    continue
                if style_index == 0 and selection_index == 0:
                    keystone_ids.add(rune_id)
                key = (participant.champion_id, participant.team_position, rune_id)
                bucket = buckets.setdefault(key, {"picks": 0, "wins": 0})
                bucket["picks"] += 1
                bucket["wins"] += int(participant.win)

    for (champion_id, role, rune_id), bucket in buckets.items():
        games = role_games[(champion_id, role)]
        picks = bucket["picks"]
        session.add(
            RuneStatistics(
                patch_id=patch_id,
                champion_id=champion_id,
                role=role,
                rune_id=rune_id,
                is_keystone=rune_id in keystone_ids,
                games=games,
                picks=picks,
                wins=bucket["wins"],
                win_rate=bucket["wins"] / picks,
                pick_rate=picks / games,
                sample_size=picks,
            )
        )

    return len(buckets)


def _recompute_build_path_statistics(
    session: Session,
    patch_id: int,
    participants: list[MatchParticipant],
    matches_by_id: dict[str, Match],
    timelines: list[MatchTimeline],
) -> int:
    """Full recompute, delete-then-reinsert per patch. `games`/`pick_rate`
    are scoped the same way _recompute_item_statistics's are - total
    champion+role games this patch (from `participants`, not just games
    that have a timeline), so a build-path row's pick_rate reads as 'build
    rate given this champion+role', consistent with item_statistics."""

    session.execute(
        BuildPathStatistics.__table__.delete().where(BuildPathStatistics.patch_id == patch_id)
    )

    role_games: dict[tuple[int, str], int] = {}
    for participant in participants:
        role_key = (participant.champion_id, participant.team_position)
        role_games[role_key] = role_games.get(role_key, 0) + 1

    terminal_item_ids = timeline.terminal_item_ids(session)
    buckets: dict[tuple[int, str, int, int], dict[str, int]] = {}

    for match_timeline in timelines:
        match = matches_by_id.get(match_timeline.match_id)
        if match is None:
            continue
        participants_info = match.raw_data["info"]["participants"]
        events = timeline.timeline_events(match_timeline.raw_data["info"])

        for participant_id, item_sequence in timeline.completed_item_order(
            events, terminal_item_ids
        ).items():
            info = participants_info[participant_id - 1]
            champion_id = info["championId"]
            role = info.get("teamPosition", "")
            win = info["win"]
            for position, item_id in enumerate(item_sequence[: RIOT_API.build_path_slots], start=1):
                bucket = buckets.setdefault((champion_id, role, position, item_id), {"picks": 0, "wins": 0})
                bucket["picks"] += 1
                bucket["wins"] += int(win)

    for (champion_id, role, position, item_id), bucket in buckets.items():
        games = role_games.get((champion_id, role), 0)
        picks = bucket["picks"]
        session.add(
            BuildPathStatistics(
                patch_id=patch_id,
                champion_id=champion_id,
                role=role,
                purchase_order=position,
                item_id=item_id,
                games=games,
                picks=picks,
                wins=bucket["wins"],
                win_rate=bucket["wins"] / picks,
                pick_rate=picks / games if games else 0.0,
                sample_size=picks,
            )
        )

    return len(buckets)


def _recompute_skill_order_statistics(
    session: Session,
    patch_id: int,
    participants: list[MatchParticipant],
    matches_by_id: dict[str, Match],
    timelines: list[MatchTimeline],
) -> int:
    """Full recompute, delete-then-reinsert per patch. Same
    champion+role-scoped `games`/`pick_rate` convention as
    _recompute_build_path_statistics/_recompute_item_statistics."""

    session.execute(
        SkillOrderStatistics.__table__.delete().where(SkillOrderStatistics.patch_id == patch_id)
    )

    role_games: dict[tuple[int, str], int] = {}
    for participant in participants:
        role_key = (participant.champion_id, participant.team_position)
        role_games[role_key] = role_games.get(role_key, 0) + 1

    buckets: dict[tuple[int, str, int, int, str], dict[str, int]] = {}

    for match_timeline in timelines:
        match = matches_by_id.get(match_timeline.match_id)
        if match is None:
            continue
        participants_info = match.raw_data["info"]["participants"]
        events = timeline.timeline_events(match_timeline.raw_data["info"])

        for participant_id, level_sequence in timeline.skill_level_order(events).items():
            info = participants_info[participant_id - 1]
            champion_id = info["championId"]
            role = info.get("teamPosition", "")
            win = info["win"]
            for level, (skill_slot, level_up_type) in enumerate(level_sequence, start=1):
                key = (champion_id, role, level, skill_slot, level_up_type)
                bucket = buckets.setdefault(key, {"picks": 0, "wins": 0})
                bucket["picks"] += 1
                bucket["wins"] += int(win)

    for (champion_id, role, level, skill_slot, level_up_type), bucket in buckets.items():
        games = role_games.get((champion_id, role), 0)
        picks = bucket["picks"]
        session.add(
            SkillOrderStatistics(
                patch_id=patch_id,
                champion_id=champion_id,
                role=role,
                level=level,
                skill_slot=skill_slot,
                level_up_type=level_up_type,
                games=games,
                picks=picks,
                wins=bucket["wins"],
                win_rate=bucket["wins"] / picks,
                pick_rate=picks / games if games else 0.0,
                sample_size=picks,
            )
        )

    return len(buckets)
