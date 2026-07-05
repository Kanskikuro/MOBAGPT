"""Anthropic API wrapper for the knowledge/ tag+rating pipeline.

`_call_anthropic` is the only function that talks to the network; tests
monkeypatch it directly, same pattern as ingestion/wiki/client.py's `_get`.
Everything above it (`extract_champion_profile`, `extract_tags`) is pure
parsing/validation logic, exercised for real in tests: even though the tool
schema (knowledge/prompts.py) constrains the model to the fixed taxonomy,
validation here still defends against a provider not respecting that
schema, same defensive-parsing philosophy as ingestion/wiki's handling of
inconsistent wiki markup.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import anthropic

from config.settings import LLM_TAGGING
from config.taxonomy import RATING_MAX, RATING_MIN, RATING_NAMES, SEMANTIC_TAGS
from knowledge import prompts

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not LLM_TAGGING.api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. The knowledge/ tag+rating "
                "pipeline needs it to call the LLM (same requirement as "
                "RIOT_API_KEY for ingestion/riot_api and ingestion/otp)."
            )
        _client = anthropic.Anthropic(api_key=LLM_TAGGING.api_key)
    return _client


@dataclass
class ChampionExtraction:
    tags: list[str]
    ratings: dict[str, float]
    warnings: list[str] = field(default_factory=list)


@dataclass
class TagExtraction:
    tags: list[str]
    warnings: list[str] = field(default_factory=list)


def _call_anthropic(prompt: str, tool: dict) -> dict:
    """Single tool-forced call; retries on transient API errors. Returns the
    tool_use block's `input` dict."""
    client = _get_client()
    last_exc: anthropic.APIError | None = None

    for attempt in range(1, LLM_TAGGING.max_retries + 1):
        try:
            response = client.messages.create(
                model=LLM_TAGGING.model,
                max_tokens=1024,
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": prompt}],
                timeout=LLM_TAGGING.request_timeout_seconds,
            )
            for block in response.content:
                if block.type == "tool_use":
                    return block.input
            raise RuntimeError("Anthropic response contained no tool_use block")
        except anthropic.APIError as exc:
            last_exc = exc
            if attempt < LLM_TAGGING.max_retries:
                time.sleep(2**attempt)

    assert last_exc is not None
    raise last_exc


def extract_champion_profile(name: str, source_text: str) -> ChampionExtraction:
    raw = _call_anthropic(prompts.champion_prompt(source_text), prompts.champion_tool())
    warnings: list[str] = []

    tags = _validate_tags(raw.get("tags", []), name, warnings)
    ratings = _validate_ratings(raw.get("ratings", {}), name, warnings)

    return ChampionExtraction(tags=tags, ratings=ratings, warnings=warnings)


def extract_tags(entity_kind: str, name: str, source_text: str) -> TagExtraction:
    prompt_fn = prompts.item_prompt if entity_kind == "item" else prompts.rune_prompt
    raw = _call_anthropic(prompt_fn(source_text), prompts.tags_only_tool())

    warnings: list[str] = []
    tags = _validate_tags(raw.get("tags", []), name, warnings)
    return TagExtraction(tags=tags, warnings=warnings)


def _validate_tags(raw_tags: list, name: str, warnings: list[str]) -> list[str]:
    valid: list[str] = []
    for tag in raw_tags:
        if tag in SEMANTIC_TAGS:
            if tag not in valid:
                valid.append(tag)
        else:
            warnings.append(f"{name}: dropped out-of-taxonomy tag {tag!r}")
    return valid


def _validate_ratings(raw_ratings: dict, name: str, warnings: list[str]) -> dict[str, float]:
    midpoint = (RATING_MIN + RATING_MAX) / 2
    ratings: dict[str, float] = {}

    for rating_name in RATING_NAMES:
        if rating_name not in raw_ratings:
            warnings.append(f"{name}: missing rating {rating_name!r}, defaulting to midpoint")
            ratings[rating_name] = midpoint
            continue

        value = raw_ratings[rating_name]
        try:
            value = float(value)
        except (TypeError, ValueError):
            warnings.append(
                f"{name}: non-numeric rating {rating_name!r}={value!r}, defaulting to midpoint"
            )
            ratings[rating_name] = midpoint
            continue

        clamped = max(RATING_MIN, min(RATING_MAX, value))
        if clamped != value:
            warnings.append(
                f"{name}: rating {rating_name!r}={value} out of range, clamped to {clamped}"
            )
        ratings[rating_name] = clamped

    for rating_name in raw_ratings:
        if rating_name not in RATING_NAMES:
            warnings.append(f"{name}: dropped out-of-taxonomy rating {rating_name!r}")

    return ratings
