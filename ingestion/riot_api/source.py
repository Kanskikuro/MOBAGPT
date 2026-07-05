"""Riot API ingestion source: high-ELO ranked-solo match capture, plus the
derived per-(patch, champion, role) win/pick/ban rate table
(matchup_statistics). First slice of Component 2 (docs/sepc.md) -
champion_synergy/champion_counters, build paths, and rune/item/skill-order
stats are deferred (see docs/architecture.md's known gaps).
"""

from __future__ import annotations

import datetime
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import RIOT_API
from db.models import Match, MatchParticipant, MatchupStatistics, Patch
from ingestion.base import IngestionSource
from ingestion.riot_api import client
from ingestion.riot_api.identity import extract_puuid


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
        puuids = self._seed_puuids()

        match_ids: set[str] = set()
        for puuid in puuids:
            try:
                match_ids.update(client.fetch_match_ids(puuid))
            except requests.exceptions.RequestException as exc:
                self.warnings.append(f"Match id lookup failed for a seed summoner: {exc}")

        matches = []
        for match_id in match_ids:
            try:
                matches.append(client.fetch_match(match_id))
            except requests.exceptions.RequestException as exc:
                self.warnings.append(f"Match fetch failed for {match_id}: {exc}")

        return {"matches": matches}

    def _seed_puuids(self) -> list[str]:
        entries = client.fetch_league_entries()[: RIOT_API.max_seed_summoners]
        puuids: list[str] = []

        for entry in entries:
            puuid = extract_puuid(entry)

            if puuid is None:
                summoner_id = entry.get("summonerId")
                if not summoner_id:
                    self.warnings.append(
                        "League entry has neither puuid nor summonerId; skipping"
                    )
                    continue
                try:
                    puuid = client.fetch_summoner(summoner_id).get("puuid")
                except requests.exceptions.RequestException as exc:
                    self.warnings.append(f"Summoner lookup failed for {summoner_id}: {exc}")
                    continue

            if puuid:
                puuids.append(puuid)

        return puuids

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

        session.flush()

        stats_count = _recompute_matchup_statistics(session, patch_id) if patch_id is not None else 0

        return {
            "matches": new_match_count,
            "match_participants": new_participant_count,
            "matchup_statistics": stats_count,
        }


def _major_minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def _recompute_matchup_statistics(session: Session, patch_id: int) -> int:
    """Full recompute from match_participants/matches for this patch - a
    derived aggregate, not a natural upsert, so it's deleted-and-reinserted
    rather than incrementally merged (same rationale as
    ingestion/data_dragon's child-collection delete-then-reinsert)."""

    session.execute(
        MatchupStatistics.__table__.delete().where(MatchupStatistics.patch_id == patch_id)
    )

    matches = session.execute(select(Match).where(Match.patch_id == patch_id)).scalars().all()
    total_games = len(matches)
    if total_games == 0:
        return 0

    match_ids = [m.match_id for m in matches]

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

    participants = (
        session.execute(select(MatchParticipant).where(MatchParticipant.match_id.in_(match_ids)))
        .scalars()
        .all()
    )

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
