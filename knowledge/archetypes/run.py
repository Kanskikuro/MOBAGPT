"""CLI entry point: python -m knowledge.archetypes.run --patch <patch>
[--champion <name>] [--role <role>]

Populates build_archetypes + archetype_items/archetype_runes/archetype_tags
(docs/sepc.md Component 1) - the last Phase 1 knowledge-DB deliverable,
consuming knowledge/'s tag+rating pipeline output plus the statistical DB
and OTP DB together. Driven by matchup_statistics rows for the resolved
patch (reusing its games/sample_size as the "is this champion+role viable"
signal, matching the spec's "every viable champion+role" phrasing) rather
than iterating every champion+role combination blindly.

All of a champion's roles are extracted before the single per-champion
upsert (knowledge.archetypes.loader does a full delete-then-reinsert scoped
by champion_id only, not by role - upserting once per role would each time
wipe out the previous role's freshly-inserted archetypes for that champion).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Champion, MatchupStatistics, Patch
from db.session import make_engine, session_scope
from knowledge.archetypes.extraction import ExtractedArchetype, extract_champion_role_archetypes
from knowledge.archetypes.loader import upsert_champion_archetypes


@dataclass
class ArchetypeRunResult:
    champions_processed: int = 0
    archetypes_created: int = 0
    warnings: list[str] = field(default_factory=list)


def run_archetype_extraction(
    session: Session,
    patch: str | None = None,
    champion_name: str | None = None,
    role: str | None = None,
) -> ArchetypeRunResult:
    result = ArchetypeRunResult()

    patch_row = _resolve_patch(session, patch)
    if patch_row is None:
        result.warnings.append("no ingested patch found; nothing to extract")
        return result

    query = select(MatchupStatistics).where(MatchupStatistics.patch_id == patch_row.id)

    if champion_name is not None:
        champion = session.execute(
            select(Champion).where(Champion.display_name == champion_name)
        ).scalar_one_or_none()
        if champion is None:
            result.warnings.append(f"champion {champion_name!r} not found in DB")
            return result
        query = query.where(MatchupStatistics.champion_id == champion.champion_id)

    if role is not None:
        query = query.where(MatchupStatistics.role == role)

    by_champion: dict[int, list[ExtractedArchetype]] = {}
    for stats in session.execute(query).scalars():
        archetypes = extract_champion_role_archetypes(
            session, stats.champion_id, stats.role, patch_row.id
        )
        by_champion.setdefault(stats.champion_id, []).extend(archetypes)

    for champion_id, archetypes in by_champion.items():
        result.archetypes_created += upsert_champion_archetypes(session, champion_id, archetypes)

    result.champions_processed = len(by_champion)
    return result


def _resolve_patch(session: Session, patch: str | None) -> Patch | None:
    if patch is not None:
        return session.execute(select(Patch).where(Patch.version == patch)).scalar_one_or_none()
    return session.execute(select(Patch).order_by(Patch.id.desc())).scalars().first()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Populate build_archetypes from statistical + OTP data (docs/sepc.md Component 1)."
        )
    )
    parser.add_argument(
        "--patch", default=None, help="Patch to extract for. Omit for the latest ingested."
    )
    parser.add_argument("--champion", default=None, help="Restrict to one champion's display name.")
    parser.add_argument("--role", default=None, help="Restrict to one role (e.g. MIDDLE).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    engine = make_engine()
    with session_scope(engine) as session:
        result = run_archetype_extraction(
            session, patch=args.patch, champion_name=args.champion, role=args.role
        )

    print(f"  champions processed: {result.champions_processed}")
    print(f"  archetypes created: {result.archetypes_created}")
    for warning in result.warnings:
        print(f"  WARNING: {warning}")


if __name__ == "__main__":
    main()
