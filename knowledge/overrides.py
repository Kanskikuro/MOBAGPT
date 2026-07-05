"""Loads data/tag_overrides.yaml - the manually reviewed file that always
wins over LLM output (docs/sepc.md Component 1). Unlike knowledge/client.py's
defensive handling of LLM output, this file is hand-curated: an invalid tag
or rating name is a mistake worth failing loudly on, not silently dropping.

Full-replacement semantics: if `tags` or `ratings` is present for an entity,
it completely replaces the LLM-derived value for that field. An omitted
field falls back to the LLM output untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from config.settings import LLM_TAGGING
from config.taxonomy import RATING_MAX, RATING_MIN, RATING_NAMES, SEMANTIC_TAGS


@dataclass
class EntityOverride:
    tags: list[str] | None = None
    ratings: dict[str, float] | None = None


@dataclass
class Overrides:
    champions: dict[str, EntityOverride] = field(default_factory=dict)
    items: dict[str, EntityOverride] = field(default_factory=dict)
    runes: dict[str, EntityOverride] = field(default_factory=dict)


def load_overrides(path: Path | None = None) -> Overrides:
    path = path if path is not None else LLM_TAGGING.override_file

    if not path.exists():
        return Overrides()

    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return Overrides(
        champions=_parse_section(raw.get("champions") or {}, "champions"),
        items=_parse_section(raw.get("items") or {}, "items"),
        runes=_parse_section(raw.get("runes") or {}, "runes"),
    )


def _parse_section(section: dict, section_name: str) -> dict[str, EntityOverride]:
    result: dict[str, EntityOverride] = {}
    for entity_name, body in section.items():
        body = body or {}
        tags = body.get("tags")
        ratings = body.get("ratings")

        if tags is not None:
            for tag in tags:
                if tag not in SEMANTIC_TAGS:
                    raise ValueError(
                        f"{section_name}.{entity_name}: override tag {tag!r} is not "
                        "in config.taxonomy.SEMANTIC_TAGS"
                    )

        if ratings is not None:
            for rating_name, value in ratings.items():
                if rating_name not in RATING_NAMES:
                    raise ValueError(
                        f"{section_name}.{entity_name}: override rating "
                        f"{rating_name!r} is not in config.taxonomy.RATING_NAMES"
                    )
                if not (RATING_MIN <= value <= RATING_MAX):
                    raise ValueError(
                        f"{section_name}.{entity_name}: override rating "
                        f"{rating_name!r}={value} is outside [{RATING_MIN}, {RATING_MAX}]"
                    )

        result[entity_name] = EntityOverride(tags=tags, ratings=ratings)

    return result
