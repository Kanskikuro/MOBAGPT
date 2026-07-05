"""Prompt text and Anthropic tool (structured-output) schemas for the
knowledge/ tag+rating pipeline. Tags are constrained to `SEMANTIC_TAGS` and
ratings to `RATING_NAMES` via the tool's JSON schema (enum / fixed
properties), so the model literally cannot emit an out-of-taxonomy value -
knowledge/client.py still validates defensively on the way in, since
`additionalProperties: false` on ratings guards structure but not whether
every provider respects it faithfully.
"""

from __future__ import annotations

from config.taxonomy import RATING_MAX, RATING_MIN, RATING_NAMES, SEMANTIC_TAGS

CHAMPION_TOOL_NAME = "record_champion_profile"
TAGS_ONLY_TOOL_NAME = "record_tags"

_TAGS_SCHEMA = {
    "type": "array",
    "items": {"type": "string", "enum": sorted(SEMANTIC_TAGS)},
    "description": "Semantic gameplay tags that apply, drawn only from the given taxonomy.",
}


def champion_tool() -> dict:
    return {
        "name": CHAMPION_TOOL_NAME,
        "description": "Record a champion's semantic tags and numeric ratings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tags": _TAGS_SCHEMA,
                "ratings": {
                    "type": "object",
                    "properties": {
                        name: {
                            "type": "number",
                            "minimum": RATING_MIN,
                            "maximum": RATING_MAX,
                        }
                        for name in RATING_NAMES
                    },
                    "required": list(RATING_NAMES),
                    "additionalProperties": False,
                },
            },
            "required": ["tags", "ratings"],
            "additionalProperties": False,
        },
    }


def tags_only_tool() -> dict:
    return {
        "name": TAGS_ONLY_TOOL_NAME,
        "description": "Record the semantic tags that apply to this item or rune.",
        "input_schema": {
            "type": "object",
            "properties": {"tags": _TAGS_SCHEMA},
            "required": ["tags"],
            "additionalProperties": False,
        },
    }


_TAXONOMY_BLOCK = (
    "Fixed tag taxonomy (use ONLY these, exact spelling):\n"
    + ", ".join(sorted(SEMANTIC_TAGS))
)

_RATING_BLOCK = (
    "Rating scale is 0-10 for every rating below. 'scaling_curve': 0 means "
    "hyper-early/snowball-oriented, 10 means hyper-late-game scaling.\n"
    + ", ".join(RATING_NAMES)
)


def champion_prompt(source_text: str) -> str:
    return (
        "You are tagging a League of Legends champion for a build-recommendation "
        "engine's knowledge base. Analyze the champion's kit below and call the "
        f"tool with its semantic tags and numeric ratings.\n\n{_TAXONOMY_BLOCK}\n\n"
        f"{_RATING_BLOCK}\n\n---\n{source_text}"
    )


def item_prompt(source_text: str) -> str:
    return (
        "You are tagging a League of Legends item for a build-recommendation "
        "engine's knowledge base. Analyze the item below and call the tool with "
        f"the semantic tags that apply to what it does.\n\n{_TAXONOMY_BLOCK}\n\n"
        f"---\n{source_text}"
    )


def rune_prompt(source_text: str) -> str:
    return (
        "You are tagging a League of Legends rune for a build-recommendation "
        "engine's knowledge base. Analyze the rune below and call the tool with "
        f"the semantic tags that apply to what it does.\n\n{_TAXONOMY_BLOCK}\n\n"
        f"---\n{source_text}"
    )
