"""Centralized configuration. Nothing outside this module should hard-code
DB paths, external API endpoints, or patch-fallback policy."""

from __future__ import annotations

from dataclasses import dataclass
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


DATA_DRAGON = DataDragonSettings()
PATCH_POLICY = PatchPolicy()
