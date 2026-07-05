"""Centralized configuration. Nothing outside this module should hard-code
DB paths, external API endpoints, or patch-fallback policy."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "knowledge.db"


@dataclass(frozen=True)
class DataDragonSettings:
    base_url: str = "https://ddragon.leagueoflegends.com"
    locale: str = "en_US"
    request_timeout_seconds: float = 10.0


@dataclass(frozen=True)
class PatchPolicy:
    """Governs how far back the training pipeline may fall back when a
    patch has insufficient statistical sample size. Used by Component 2
    (statistical DB); knowledge-DB ingestion does not consult this."""

    max_patch_lookback: int = 3


@dataclass(frozen=True)
class WikiSettings:
    base_url: str = "https://leagueoflegends.fandom.com"
    api_path: str = "/api.php"
    user_agent: str = (
        "MOBAGPT-research/0.1 (hobby project data pipeline; "
        "contact: kanski.kuro@gmail.com)"
    )
    request_timeout_seconds: float = 10.0
    request_delay_seconds: float = 0.5


@dataclass(frozen=True)
class RiotApiSettings:
    """Component 2 (statistical DB) ingestion source. `api_key` is read from
    the RIOT_API_KEY environment variable at import time - never hard-coded,
    never committed. A dev key (developer.riotgames.com) works but expires
    every 24h and is capped at the rate_limits below; tests never need a
    real key since ingestion/riot_api/client.py is monkeypatched in tests,
    same as ingestion/data_dragon and ingestion/wiki."""

    api_key: str = field(default_factory=lambda: os.environ.get("RIOT_API_KEY", ""))

    # League/Summoner-V4 use platform routing; Match-V5/Account-V1 use
    # regional routing. These are genuinely different Riot routing values,
    # not interchangeable - see https://developer.riotgames.com/apis.
    platform: str = "na1"
    region: str = "americas"

    queue_id: int = 420  # ranked solo/duo
    tier: str = "challenger"

    # Caps a first run's request volume to something predictable under the
    # dev-key rate limit rather than crawling every high-ELO summoner.
    max_seed_summoners: int = 300
    matches_per_summoner: int = 15

    # (max_requests, window_seconds) pairs, all enforced simultaneously.
    # Defaults match a Riot dev key's app rate limit.
    rate_limits: tuple[tuple[int, int], ...] = ((20, 1), (100, 120))

    request_timeout_seconds: float = 10.0


DATA_DRAGON = DataDragonSettings()
PATCH_POLICY = PatchPolicy()
WIKI = WikiSettings()
RIOT_API = RiotApiSettings()
