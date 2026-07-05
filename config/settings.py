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

    # How many completed (non-component, non-consumable) items to track per
    # build_path_statistics row - deeper slots get sparse and less comparable.
    build_path_slots: int = 4

    # (max_requests, window_seconds) pairs, all enforced simultaneously.
    # Defaults match a Riot dev key's app rate limit.
    rate_limits: tuple[tuple[int, int], ...] = ((20, 1), (100, 120))

    request_timeout_seconds: float = 10.0


@dataclass(frozen=True)
class OtpSettings:
    """Component 3 (OTP DB) ingestion source. Identifies one-trick mains via
    Champion-Mastery-V4 point concentration, then samples their recent match
    history on that champion. Reuses RiotApiSettings' seed pool
    (ingestion.riot_api.identity.seed_challenger_puuids) and HTTP client
    rather than duplicating either - no rate-limit, platform, or region
    settings live here."""

    # A player qualifies as a one-trick on their single top-mastery champion
    # when *both* clear their threshold - absolute points alone would catch
    # long-tenured players who've simply played a lot of everything;
    # concentration alone would catch a fresh account with only one champion
    # played a handful of times.
    min_mastery_points: int = 200_000
    min_mastery_concentration: float = 0.5  # top champion's share of total mastery points

    # Caps a run's match+timeline fetch volume to something predictable
    # under the dev-key rate limit - same rationale as
    # RiotApiSettings.max_seed_summoners. Every seeded puuid needs its own
    # mastery call just to check qualification (no "find high-mastery
    # players" query exists), so a full run's request volume is dominated by
    # the seed pool size, not this cap - but this still bounds the
    # (potentially much heavier) per-one-trick match+timeline fetch phase.
    max_one_tricks_per_run: int = 50

    # Deeper per-player sample than RiotApiSettings.matches_per_summoner
    # (15) - OTP's point is depth on a known individual, not breadth across
    # many players.
    matches_per_one_trick: int = 20

    # A purchase before this timestamp counts as a "starting item" - a fixed
    # cutoff rather than detecting the first recall event (known
    # simplification, same spirit as ingestion.riot_api.timeline's
    # undo-handling). ~90s covers the pre-minions-spawn opening buy.
    starting_items_cutoff_ms: int = 90_000


@dataclass(frozen=True)
class LlmTaggingSettings:
    """knowledge/ semantic tag + numeric rating pipeline (docs/sepc.md
    Component 1). `api_key` follows the exact RiotApiSettings pattern: read
    from an environment variable at import time, never hard-coded/committed.
    Tests never need a real key since knowledge/client.py's low-level
    Anthropic call is monkeypatched, same as ingestion/riot_api and
    ingestion/wiki's HTTP clients."""

    api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    model: str = "claude-haiku-4-5-20251001"  # cheap/fast, bulk classification
    request_timeout_seconds: float = 30.0
    max_retries: int = 3
    override_file: Path = field(default_factory=lambda: REPO_ROOT / "data" / "tag_overrides.yaml")


@dataclass(frozen=True)
class ArchetypeSettings:
    """knowledge/archetypes/ build-archetype extraction (docs/sepc.md
    Component 1). Thresholds below are first-pass defaults, unverified
    against real data - no RIOT_API_KEY is configured in this environment,
    same caveat as OtpSettings' mastery thresholds."""

    # A champion+role needs at least this many *weighted* observed builds
    # (see otp_weight_normalizer below) before clustering is attempted at
    # all - reuses matchup_statistics.games as the viability signal.
    min_builds_per_champion_role: int = 20

    # A cluster needs at least this much total weight to become a real
    # archetype rather than being discarded as noise.
    min_cluster_weight: float = 4.0

    # Caps kept archetypes per champion+role to the highest-weight clusters,
    # same "cap a run to something predictable" spirit as
    # RiotApiSettings.max_seed_summoners.
    max_archetypes_per_champion_role: int = 4

    # scipy.cluster.hierarchy.fcluster's distance threshold in tag-fraction
    # space (criterion="distance"). A stopping rule, not a target cluster
    # count - avoids guessing k per champion.
    distance_threshold: float = 0.6

    # Fraction of a cluster's builds an item/rune must appear in to be
    # counted as core (non-situational) vs. merely situational vs. dropped.
    core_item_frequency: float = 0.6
    situational_item_frequency: float = 0.2
    core_rune_frequency: float = 0.5

    # OTP build weight = min(1.0, games_sampled / otp_weight_normalizer) *
    # (0.5 + 0.5 * win_rate) - "weighted by the player's sample size and
    # consistency" per docs/sepc.md's Role of OTP data. Aggregate
    # (Challenger match_participants) builds always weigh 1.0.
    otp_weight_normalizer: int = 10

    # ArchetypeTag.delta = archetype_delta_scale * mapped tag fraction
    # (config.archetype_rules.RATING_TAG_MAP), clamped to +/- this bound -
    # a rating delta shifting a 0-10 baseline shouldn't alone exceed it.
    archetype_delta_scale: float = 4.0
    archetype_delta_max: float = 5.0

    # An archetype's name is driven by its single highest-fraction tag
    # (config.archetype_rules.ARCHETYPE_NAME_BY_TAG) - only a tag clearing
    # this presence fraction is eligible, else the generic fallback name.
    name_tag_min_presence: float = 0.3


DATA_DRAGON = DataDragonSettings()
PATCH_POLICY = PatchPolicy()
WIKI = WikiSettings()
RIOT_API = RiotApiSettings()
OTP = OtpSettings()
LLM_TAGGING = LlmTaggingSettings()
ARCHETYPES = ArchetypeSettings()
