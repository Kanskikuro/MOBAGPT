"""CLI entry point: python -m knowledge.run [--only champions|items|runes] [--force]

Populates the LLM-derived semantic tag/rating layer (docs/sepc.md Component
1) for champions, items, and runes, then applies data/tag_overrides.yaml on
top. Mirrors ingestion/run.py's engine/session_scope wiring for the CLI, but
this pipeline deliberately isn't built on ingestion/base.py's IngestionSource
(see docs/architecture.md's knowledge/ subsection) and isn't patch-scoped -
none of champion_tags/champion_ratings/item_tags/rune_tags carry a
patch_id column.

Default behavior skips entities that already have a source='llm' row, since
each entity costs a real LLM call; pass --force to re-extract everyone
(e.g. after a taxonomy or prompt change).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Champion, Item, Rune
from db.session import make_engine, session_scope
from knowledge import client, loader, sourcetext
from knowledge.overrides import load_overrides

ENTITY_KINDS = ("champions", "items", "runes")


@dataclass
class PipelineResult:
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def run_pipeline(
    session: Session, only: list[str] | None = None, force: bool = False
) -> PipelineResult:
    kinds = only if only else list(ENTITY_KINDS)
    result = PipelineResult()

    if "champions" in kinds:
        result.counts["champions"] = _tag_champions(session, force, result.warnings)
    if "items" in kinds:
        result.counts["items"] = _tag_items(session, force, result.warnings)
    if "runes" in kinds:
        result.counts["runes"] = _tag_runes(session, force, result.warnings)

    _apply_overrides(session, kinds, result.warnings)

    return result


def _tag_champions(session: Session, force: bool, warnings: list[str]) -> int:
    count = 0
    for champion in session.execute(select(Champion)).scalars():
        if not force and loader.champion_has_source(session, champion.champion_id, "llm"):
            continue

        text = sourcetext.champion_source_text(session, champion)
        extraction = client.extract_champion_profile(champion.display_name, text)
        warnings.extend(extraction.warnings)

        loader.upsert_champion_tags(session, champion.champion_id, extraction.tags, "llm")
        loader.upsert_champion_ratings(session, champion.champion_id, extraction.ratings, "llm")
        count += 1

    return count


def _tag_items(session: Session, force: bool, warnings: list[str]) -> int:
    count = 0
    for item in session.execute(select(Item)).scalars():
        if not force and loader.item_has_source(session, item.item_id, "llm"):
            continue

        text = sourcetext.item_source_text(item)
        extraction = client.extract_tags("item", item.name, text)
        warnings.extend(extraction.warnings)

        loader.upsert_item_tags(session, item.item_id, extraction.tags, "llm")
        count += 1

    return count


def _tag_runes(session: Session, force: bool, warnings: list[str]) -> int:
    count = 0
    for rune in session.execute(select(Rune)).scalars():
        if not force and loader.rune_has_source(session, rune.rune_id, "llm"):
            continue

        text = sourcetext.rune_source_text(rune)
        extraction = client.extract_tags("rune", rune.name, text)
        warnings.extend(extraction.warnings)

        loader.upsert_rune_tags(session, rune.rune_id, extraction.tags, "llm")
        count += 1

    return count


def _apply_overrides(session: Session, kinds: list[str], warnings: list[str]) -> None:
    overrides = load_overrides()

    if "champions" in kinds:
        for name, override in overrides.champions.items():
            champion = session.execute(
                select(Champion).where(Champion.display_name == name)
            ).scalar_one_or_none()
            if champion is None:
                warnings.append(f"override: champion {name!r} not found in DB, skipped")
                continue
            if override.tags is not None:
                loader.upsert_champion_tags(
                    session, champion.champion_id, override.tags, "override"
                )
            if override.ratings is not None:
                loader.upsert_champion_ratings(
                    session, champion.champion_id, override.ratings, "override"
                )

    if "items" in kinds:
        for name, override in overrides.items.items():
            item = session.execute(select(Item).where(Item.name == name)).scalar_one_or_none()
            if item is None:
                warnings.append(f"override: item {name!r} not found in DB, skipped")
                continue
            if override.tags is not None:
                loader.upsert_item_tags(session, item.item_id, override.tags, "override")

    if "runes" in kinds:
        for name, override in overrides.runes.items():
            rune = session.execute(select(Rune).where(Rune.name == name)).scalar_one_or_none()
            if rune is None:
                warnings.append(f"override: rune {name!r} not found in DB, skipped")
                continue
            if override.tags is not None:
                loader.upsert_rune_tags(session, rune.rune_id, override.tags, "override")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Populate LLM-derived semantic tags + numeric ratings (docs/sepc.md Component 1)."
        )
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=ENTITY_KINDS,
        default=None,
        help="Restrict to specific entity kinds. Omit to run all three.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract entities that already have an 'llm' row (default: skip them).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    engine = make_engine()
    with session_scope(engine) as session:
        result = run_pipeline(session, only=args.only, force=args.force)

    for entity, count in result.counts.items():
        print(f"  {entity}: {count} tagged")
    for warning in result.warnings:
        print(f"  WARNING: {warning}")


if __name__ == "__main__":
    main()
