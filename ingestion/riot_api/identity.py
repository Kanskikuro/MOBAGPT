"""PUUID resolution for Riot API ingestion.

Unlike ingestion/wiki (which must resolve champion display names to wiki
page titles), champion identity here needs no resolution at all - Match-V5
participants carry Riot's numeric `championId` directly, the same id
ingestion/data_dragon already uses as Champion.champion_id.

The one identity gap: League-V4 league entries should carry `puuid`
directly on current API versions, but a defensive fallback to a
Summoner-V4 lookup (by the entry's `summonerId`) is needed in case a
response is missing it - orchestrated in source.py, where the client calls
already live.
"""

from __future__ import annotations


def extract_puuid(league_entry: dict) -> str | None:
    puuid = league_entry.get("puuid")
    return puuid if puuid else None
