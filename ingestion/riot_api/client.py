"""Thin HTTP client for the Riot Games API (League-V4, Summoner-V4,
Match-V5). No DB access here - see source.py for how fetched JSON becomes
rows.

Riot API keys are rate-limited on multiple sliding windows enforced
simultaneously (a dev key defaults to 20 req/1s *and* 100 req/2min) - a
single fixed per-request delay (as ingestion/wiki/client.py uses, since the
wiki has only one relevant window) can't safely satisfy several windows at
once, so RateLimiter tracks request timestamps per configured window and
sleeps just long enough to stay under all of them before every request.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

import requests

from config.settings import RIOT_API

_session = requests.Session()
_session.headers.update({"X-Riot-Token": RIOT_API.api_key})

_MAX_ATTEMPTS = 3


class RateLimiter:
    """Enforces every (max_requests, window_seconds) pair simultaneously.
    One instance shared across all endpoints below, since Riot's app rate
    limit is global to the API key, not per-endpoint."""

    def __init__(
        self,
        windows: tuple[tuple[int, int], ...],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._windows = windows
        self._clock = clock
        self._sleep = sleep
        self._history: dict[int, deque[float]] = {
            window_seconds: deque() for _, window_seconds in windows
        }

    def acquire(self) -> None:
        while True:
            now = self._clock()
            wait = 0.0
            for max_requests, window_seconds in self._windows:
                history = self._history[window_seconds]
                while history and now - history[0] >= window_seconds:
                    history.popleft()
                if len(history) >= max_requests:
                    wait = max(wait, window_seconds - (now - history[0]))
            if wait <= 0:
                break
            self._sleep(wait)

        now = self._clock()
        for _, window_seconds in self._windows:
            self._history[window_seconds].append(now)


_rate_limiter = RateLimiter(RIOT_API.rate_limits)


def _get(url: str, params: dict | None = None) -> dict | list:
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        _rate_limiter.acquire()
        try:
            response = _session.get(url, params=params, timeout=RIOT_API.request_timeout_seconds)
            if response.status_code == 429:
                if attempt == _MAX_ATTEMPTS:
                    response.raise_for_status()
                retry_after = float(response.headers.get("Retry-After", 2**attempt))
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException:
            if attempt == _MAX_ATTEMPTS:
                raise
            time.sleep(2**attempt)

    raise RuntimeError("unreachable: retry loop must return or raise")


def _platform_url(path: str) -> str:
    return f"https://{RIOT_API.platform}.api.riotgames.com{path}"


def _regional_url(path: str) -> str:
    return f"https://{RIOT_API.region}.api.riotgames.com{path}"


_LEAGUE_ENDPOINT_BY_TIER = {
    "challenger": "challengerleagues",
    "grandmaster": "grandmasterleagues",
    "master": "masterleagues",
}


def fetch_league_entries(queue: str = "RANKED_SOLO_5x5", tier: str | None = None) -> list[dict]:
    """League-V4 apex league, platform routing. Only apex tiers
    (challenger/grandmaster/master) are supported - this endpoint shape
    returns the whole league in one call, unlike lower tiers which are
    paginated by division and far larger than a hobby project needs."""
    resolved_tier = tier or RIOT_API.tier
    endpoint = _LEAGUE_ENDPOINT_BY_TIER[resolved_tier]
    url = _platform_url(f"/lol/league/v4/{endpoint}/by-queue/{queue}")
    payload = _get(url)
    return payload["entries"]


def fetch_summoner(encrypted_summoner_id: str) -> dict:
    """Summoner-V4, platform routing. Fallback only - modern League-V4
    entries already embed `puuid` directly; see
    ingestion.riot_api.identity.extract_puuid."""
    url = _platform_url(f"/lol/summoner/v4/summoners/{encrypted_summoner_id}")
    return _get(url)


def fetch_match_ids(
    puuid: str,
    *,
    queue: int | None = None,
    count: int | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
) -> list[str]:
    """Match-V5, regional routing. `start_time`/`end_time` are epoch
    seconds, for narrowing to a patch's rough live window when needed."""
    params: dict[str, int] = {
        "queue": queue if queue is not None else RIOT_API.queue_id,
        "count": count if count is not None else RIOT_API.matches_per_summoner,
    }
    if start_time is not None:
        params["startTime"] = start_time
    if end_time is not None:
        params["endTime"] = end_time

    url = _regional_url(f"/lol/match/v5/matches/by-puuid/{puuid}/ids")
    return _get(url, params=params)


def fetch_match(match_id: str) -> dict:
    """Match-V5, regional routing."""
    url = _regional_url(f"/lol/match/v5/matches/{match_id}")
    return _get(url)


def fetch_match_timeline(match_id: str) -> dict:
    """Match-V5 timeline, regional routing. Frame-by-frame event log (item
    purchases, skill level-ups, ...) - a separate, heavier call from
    fetch_match's match summary."""
    url = _regional_url(f"/lol/match/v5/matches/{match_id}/timeline")
    return _get(url)
