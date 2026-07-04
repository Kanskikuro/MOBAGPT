"""CLI entry point: python -m ingestion.run --source <name> --patch <patch>

Schema is managed by Alembic (`alembic upgrade head`), not created here.
"""

from __future__ import annotations

import argparse

from db.session import make_engine, session_scope
from ingestion.base import IngestionSource
from ingestion.data_dragon.source import DataDragonSource

SOURCES: dict[str, type[IngestionSource]] = {
    "data_dragon": DataDragonSource,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a knowledge-DB ingestion source.")
    parser.add_argument("--source", required=True, choices=sorted(SOURCES))
    parser.add_argument(
        "--patch",
        default=None,
        help="Patch to ingest (e.g. '14.14'). Omit for the latest available.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    source = SOURCES[args.source]()

    engine = make_engine()
    with session_scope(engine) as session:
        result = source.run(session, patch=args.patch)

    duration = (result.finished_at - result.started_at).total_seconds()
    print(f"[{result.source}] patch={result.patch} duration={duration:.1f}s")
    for entity, count in result.counts.items():
        print(f"  {entity}: {count}")
    for warning in result.warnings:
        print(f"  WARNING: {warning}")


if __name__ == "__main__":
    main()
