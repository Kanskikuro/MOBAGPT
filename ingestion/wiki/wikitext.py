"""Bounded, defensive wikitext parsing for the LoL Fandom wiki's ability-data
templates (docs/sepc.md: "Parse defensively; wiki markup is inconsistent.").

This is deliberately NOT a MediaWiki template-evaluation engine. It handles
two things:

1. Splitting a template invocation into its `|key = value` fields, using the
   real formatting convention observed on this wiki (one field per line).
2. Unwrapping a small, explicit set of known "decorator" templates found
   inside field values (e.g. `{{ap|40 to 140}}` -> `40 to 140`), recursively,
   with a verbatim fallback (never partially stripped, never guessed) for
   anything not in that set - the unrecognized template name is collected
   instead so callers can surface it as a warning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_FIELD_LINE = re.compile(r"^\|\s*([A-Za-z][\w \-]*?)\s*=\s*(.*)$")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_WIKILINK = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]+))?\]\]")
_BOLD_ITALIC = re.compile(r"'''''|'''|''")
_BR_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _first_positional(params: list[str]) -> str:
    """Named params (containing a top-level '=', e.g. 'icononly = true') are
    decorative flags on icon/tooltip templates, not content - drop them."""
    for param in params:
        if "=" not in param:
            return param
    return ""


def _last_positional(params: list[str]) -> str:
    positional = [p for p in params if "=" not in p]
    return positional[-1] if positional else ""


def _unwrap_st(params: list[str]) -> str:
    """{{st|Label1|Value1|Label2|Value2|...}} -> "Label1: Value1; Label2: Value2"."""
    pairs = []
    for i in range(0, len(params) - 1, 2):
        pairs.append(f"{params[i]}: {params[i + 1]}")
    return "; ".join(pairs)


# Explicit, closed set of decorator templates known to appear inside ability
# field values on this wiki. Extend only when a real unrecognized one turns
# up (surfaced via ParsedWikiTemplate.unknown_templates) - never speculatively.
_KNOWN_TEMPLATES = {
    "ap": _first_positional,
    "ad": _first_positional,
    "hp": _first_positional,
    "health": _first_positional,
    "mana": _first_positional,
    "armor": _first_positional,
    "mr": _first_positional,
    "ah": _first_positional,
    "as": _first_positional,
    "sti": _first_positional,
    "stil": _first_positional,
    "sbc": _first_positional,
    "tt": _first_positional,
    "tip": _first_positional,
    "fd": _first_positional,
    "st": _unwrap_st,
    "g": _first_positional,
    "ci": _first_positional,
    "ii": _first_positional,
    "ai": _last_positional,
    "bug": lambda params: "",
}


def _find_matching_close(text: str, open_idx: int) -> int:
    """`open_idx` points at the first '{' of a '{{' at depth 0 within
    `text`. Returns the index just after the matching '}}'."""
    depth = 0
    i = open_idx
    n = len(text)

    while i < n:
        if text[i:i + 2] == "{{":
            depth += 1
            i += 2
        elif text[i:i + 2] == "}}":
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1

    return n  # unterminated template; be defensive rather than raise


def _split_top_level(text: str) -> list[str]:
    """Split `text` on '|' at depth 0, tracking both {{ }} and [[ ]] nesting
    so a wikilink like [[Physical Damage|physical damage]] inside a template
    parameter doesn't get misread as a parameter boundary."""
    parts: list[str] = []
    current: list[str] = []
    brace_depth = 0
    bracket_depth = 0
    i = 0
    n = len(text)

    while i < n:
        two = text[i:i + 2]
        if two == "{{":
            brace_depth += 1
            current.append(two)
            i += 2
        elif two == "}}":
            brace_depth = max(0, brace_depth - 1)
            current.append(two)
            i += 2
        elif two == "[[":
            bracket_depth += 1
            current.append(two)
            i += 2
        elif two == "]]":
            bracket_depth = max(0, bracket_depth - 1)
            current.append(two)
            i += 2
        elif text[i] == "|" and brace_depth == 0 and bracket_depth == 0:
            parts.append("".join(current))
            current = []
            i += 1
        else:
            current.append(text[i])
            i += 1

    parts.append("".join(current))
    return parts


def _unwrap_span(text: str, start: int, end: int) -> tuple[str, set[str]]:
    """`text[start:end]` is a full `{{...}}` span, e.g. `{{as|(+ {{ap|X}}%)}}`."""
    inner = text[start + 2:end - 2]
    parts = _split_top_level(inner)
    name = parts[0].strip().lower()

    if name not in _KNOWN_TEMPLATES:
        return text[start:end], {name}

    params = parts[1:]
    clean_params: list[str] = []
    unknown: set[str] = set()

    for param in params:
        cleaned, found = unwrap_decorators(param)
        clean_params.append(cleaned)
        unknown |= found

    return _KNOWN_TEMPLATES[name](clean_params), unknown


def unwrap_decorators(text: str) -> tuple[str, set[str]]:
    """Recursively unwrap known decorator templates in `text`. Unrecognized
    templates are left completely untouched; their names are returned in the
    second element for the caller to surface as a warning."""
    result: list[str] = []
    unknown: set[str] = set()
    i = 0
    n = len(text)

    while i < n:
        if text[i:i + 2] == "{{":
            end = _find_matching_close(text, i)
            replacement, found = _unwrap_span(text, i, end)
            result.append(replacement)
            unknown |= found
            i = end
        else:
            result.append(text[i])
            i += 1

    return "".join(result), unknown


def _clean_prose(text: str) -> str:
    """Final cosmetic pass after decorator unwrapping: wikilinks, bold/italic
    markup, <br> tags. Applied last since none of these interact with {{ }}
    brace matching."""

    def _wikilink_repl(match: re.Match) -> str:
        return match.group(2) if match.group(2) else match.group(1)

    text = _WIKILINK.sub(_wikilink_repl, text)
    text = _BOLD_ITALIC.sub("", text)
    text = _BR_TAG.sub("\n", text)
    return text.strip()


def parse_key_value_fields(wikitext: str) -> dict[str, str]:
    """Split a MediaWiki template invocation into raw (still-decorated)
    `|key = value` fields, using the one-field-per-line convention this wiki
    uses. Everything before the first recognized field line (e.g. a
    smart-template-name preamble like `{{{{{1<noinclude>|...}}}` ) is
    ignored rather than parsed - deliberately out of scope, see module
    docstring."""
    wikitext = _HTML_COMMENT.sub("", wikitext)
    lines = wikitext.split("\n")

    fields: dict[str, list[str]] = {}
    current_key: str | None = None

    for line in lines:
        if line.strip() == "}}":
            break

        match = _FIELD_LINE.match(line)
        if match:
            current_key = match.group(1).strip().lower()
            fields[current_key] = [match.group(2)]
        elif current_key is not None:
            fields[current_key].append(line)

    return {key: "\n".join(value_lines).strip() for key, value_lines in fields.items()}


def discover_ability_slots(abilities_section_wikitext: str, champion_name: str) -> list[str]:
    """Find every `{{Data {champion}/{slot}|Ability}}` transclusion in an
    Abilities section. Regex-scanning (rather than assuming a fixed
    passive/Q/W/E/R list) is required: multi-form champions like Elise have
    extra named slots (e.g. "Venomous Bite") nested inside wrapper templates
    like {{Image tabber|...}} that this scan is agnostic to."""
    pattern = re.compile(
        r"\{\{Data " + re.escape(champion_name) + r"/([^|}]+)\|Ability\}\}"
    )
    return [m.group(1).strip() for m in pattern.finditer(abilities_section_wikitext)]


@dataclass
class ParsedWikiTemplate:
    fields: dict[str, str]
    notes: str
    raw_wikitext: str
    tips: str = ""
    unknown_templates: set[str] = field(default_factory=set)


def parse_wiki_template(wikitext: str) -> ParsedWikiTemplate:
    raw_fields = parse_key_value_fields(wikitext)
    clean_fields: dict[str, str] = {}
    unknown_templates: set[str] = set()

    for key, raw_value in raw_fields.items():
        unwrapped, unknown = unwrap_decorators(raw_value)
        clean_fields[key] = _clean_prose(unwrapped)
        unknown_templates |= unknown

    notes = clean_fields.pop("notes", "")
    tips = clean_fields.pop("tips", "")

    return ParsedWikiTemplate(
        fields=clean_fields,
        notes=notes,
        tips=tips,
        raw_wikitext=wikitext,
        unknown_templates=unknown_templates,
    )
