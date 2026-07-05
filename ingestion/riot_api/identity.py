"""PUUID resolution and seeding for Riot API ingestion.

Unlike ingestion/wiki (which must resolve champion display names to wiki
page titles), champion identity here needs no resolution at all - Match-V5
participants carry Riot's numeric `championId` directly, the same id
ingestion/data_dragon already uses as Champion.champion_id.

The one identity gap: League-V4 league entries should carry `puuid`
directly on current API versions, but a defensive fallback to a
Summoner-V4 lookup (by the entry's `summonerId`) is needed in case a
response is missing it.
"""

from __future__ import annotations

import requests

from config.settings import RIOT_API
from ingestion.riot_api import client


def extract_puuid(league_entry: dict) -> str | None:
    puuid = league_entry.get("puuid")
    return puuid if puuid else None


def seed_challenger_puuids(warnings: list[str]) -> list[str]:
    """Challenger-tier seed pool (League-V4), with Summoner-V4 fallback for
    entries missing `puuid` - shared by every source needing a high-ELO
    player sample (ingestion.riot_api.source.RiotApiSource,
    ingestion.otp.source.OtpSource). `warnings` is the caller's
    IngestionSource.warnings list, appended to in place rather than
    returned, matching how every other recoverable-failure path in this
    package works."""

    entries = client.fetch_league_entries()[: RIOT_API.max_seed_summoners]
    puuids: list[str] = []

    for entry in entries:
        puuid = extract_puuid(entry)

        if puuid is None:
            summoner_id = entry.get("summonerId")
            if not summoner_id:
                warnings.append("League entry has neither puuid nor summonerId; skipping")
                continue
            try:
                puuid = client.fetch_summoner(summoner_id).get("puuid")
            except requests.exceptions.RequestException as exc:
                warnings.append(f"Summoner lookup failed for {summoner_id}: {exc}")
                continue

        if puuid:
            puuids.append(puuid)

    return puuids
