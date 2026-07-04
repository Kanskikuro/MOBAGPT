"""Common interface every ingestion module implements (CLAUDE.md hard rule).

A source resolves which patch it's ingesting, fetches raw data for that
patch, then loads it into the DB via idempotent upserts so `run()` can be
called repeatedly for the same patch without creating duplicates.
"""

from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session


@dataclass
class IngestionResult:
    source: str
    patch: str
    started_at: datetime.datetime
    finished_at: datetime.datetime
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class IngestionSource(ABC):
    """One implementation per external data source (docs/sepc.md: 'Every
    external source sits behind its own ingestion module with a common
    interface, so a source can be replaced without touching the model.')."""

    name: str

    @abstractmethod
    def resolve_patch(self, patch: str | None) -> str:
        """Turn a user-supplied patch string (or None for "latest") into the
        concrete patch identifier this run will ingest."""

    @abstractmethod
    def fetch(self, patch: str) -> Any:
        """Fetch raw data for `patch` from the external source. No DB access."""

    @abstractmethod
    def load(self, session: Session, patch: str, data: Any) -> dict[str, int]:
        """Upsert `data` into the DB. Must be idempotent: calling `run()`
        twice for the same patch must not create duplicate rows. Returns a
        dict of {entity_name: row_count} for the ingestion log."""

    def run(self, session: Session, patch: str | None = None) -> IngestionResult:
        started_at = datetime.datetime.now(datetime.UTC)
        resolved_patch = self.resolve_patch(patch)
        data = self.fetch(resolved_patch)
        counts = self.load(session, resolved_patch, data)
        finished_at = datetime.datetime.now(datetime.UTC)

        return IngestionResult(
            source=self.name,
            patch=resolved_patch,
            started_at=started_at,
            finished_at=finished_at,
            counts=counts,
        )
