"""Loader for hand-curated champion ability data
(data/manual_sources/<champion>.md).

Used when a champion has no wiki page yet (too recently released) and the
user copies the rendered page content themselves - robots.txt governs
automated crawlers, not a human browsing their own browser, so this sits
outside the ingestion/wiki robots.txt constraint entirely.

There is no automated parser for the raw copy-pasted page text (it has no
fixed structure - navigation chrome, icon alt-text, and inconsistent layout
mixed into rendered prose). Each data/manual_sources/<champion>.md file is
read and structured by hand (or by an assistant reading it) into a
ManualAbility per slot, then loaded through this module using the same
idempotent-upsert pattern as ingestion/wiki. data/manual_sources/ is
gitignored - copied third-party wiki content, not redistributed via the repo.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Champion, ChampionAbilityDetail


@dataclass
class ManualAbility:
    slot: str
    notes: str
    fields: dict[str, str] = field(default_factory=dict)
    tips: str = ""
    raw_text: str = ""


def load_manual_abilities(
    session: Session,
    riot_key: str,
    abilities: list[ManualAbility],
    source_title: str,
) -> int:
    champion = session.execute(
        select(Champion).where(Champion.riot_key == riot_key)
    ).scalar_one_or_none()

    if champion is None:
        raise ValueError(f"Champion '{riot_key}' not found in DB; ingest Data Dragon first")

    count = 0
    for ability in abilities:
        session.merge(
            ChampionAbilityDetail(
                champion_id=champion.champion_id,
                slot=ability.slot,
                notes=ability.notes,
                tips=ability.tips,
                fields=ability.fields,
                raw_wikitext=ability.raw_text,
                wiki_title=source_title,
                source="manual",
                patch_id=None,
                fetched_at=datetime.datetime.now(datetime.UTC),
            )
        )
        count += 1

    return count
