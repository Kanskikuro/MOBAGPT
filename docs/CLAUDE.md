# LoL Draft Engine
Full specification: docs/spec.md — read the relevant section before implementing anything.

## Hard rules
- /model must NEVER import from the statistical DB query layer. Stats are training/eval only.
- The recommendation unit is (champion, build_archetype), never champion alone.
- Config values (weights, thresholds, patch policy) live in config/, never hard-coded.
- Every ingestion module implements the common interface in ingestion/base.py.

## Commands
- pytest tests/ — run before considering any task done
- python -m ingestion.run --source <name> --patch <patch>

## Conventions
- Python 3.11+, type hints everywhere, no bare excepts