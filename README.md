# MOBAGPT

A League of Legends draft and build recommendation engine. The core idea:
champion and build (runes + itemization) are recommended together as one
unit — `(champion, build_archetype)` — because the same champion can be a
completely different pick depending on its build (e.g. Ahri as AP burst vs.
on-hit). Statistics (win rates, matchup deltas) are used only to train and
calibrate the model, never as the direct basis for a recommendation — the
model reasons from champion/item/rune mechanics, not from win-rate lookup.

Full specification: [docs/sepc.md](docs/sepc.md). Implementation notes for
what's actually built so far: [docs/architecture.md](docs/architecture.md).
Audit of the prior project this supersedes: [docs/skadz_audit.md](docs/skadz_audit.md).

## Status

Phase 1 (data pipelines and databases) is in progress. The knowledge
database exists and is populated for patch `16.13.1`:

- **173 champions** (stats, tags, abilities), **254 items** (Summoner's
  Rift only — filtered out of Data Dragon's 706, which spans every map/mode),
  **62 runes** — via Data Dragon
- **Champion ability enrichment** (exact scalings, hidden mechanics Data
  Dragon's descriptions omit) — via the LoL Fandom wiki, for champions with
  an existing wiki page (168 of 173)
- **Item enrichment** (same wiki, same reason — e.g. Data Dragon's
  description for the quest item `World Atlas` is empty) — 234 of 254 items
- A **manual fallback** for champions too new to have a wiki page yet

Not yet built: the semantic tag/rating pipeline, statistical DB, OTP DB,
build-archetype extraction, and the model/features/training/explain/eval/api
layers. See [docs/architecture.md](docs/architecture.md#known-gaps--next-steps)
for the current gap list.

## Setup

Requires Python 3.11+ and [`uv`](https://github.com/astral-sh/uv).

```bash
uv venv .venv
uv pip install -e ".[dev]"
alembic upgrade head
```

## Running ingestion

```bash
# Champions, stats, tags, abilities, items, runes:
python -m ingestion.run --source data_dragon --patch 16.13.1

# Champion ability enrichment from the wiki (needs data_dragon already
# ingested for that patch — the wiki source enriches existing champions,
# it doesn't create them):
python -m ingestion.run --source wiki --patch 16.13.1
```

Champions with no wiki page yet are handled manually — see
[docs/architecture.md](docs/architecture.md#manual-source) for the process.

## Tests

```bash
pytest tests/
```

No test hits real network — external calls are mocked.

## Project structure

```
config/           # centralized settings: DB path, external API settings, patch policy
db/               # SQLAlchemy models (db/models.py) + session factory
migrations/       # Alembic migrations (schema is version-controlled, not create_all)
ingestion/
  base.py           # common interface every ingestion source implements
  data_dragon/      # champions, stats, tags, abilities, items, runes
  wiki/             # champion ability enrichment from leagueoflegends.fandom.com
  manual/           # hand-curated data for champions with no wiki page yet
  run.py            # CLI entry point: python -m ingestion.run --source <name> --patch <patch>
tests/            # pytest suite, real captured fixtures for parser tests, no real network
docs/             # full spec, architecture notes, prior-project audit
data/             # generated SQLite DB + manual source material (both gitignored)
```

## License

MIT
