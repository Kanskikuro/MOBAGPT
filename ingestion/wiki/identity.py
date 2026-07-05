"""Resolves a Champion DB row to a League of Legends Fandom wiki page title.

Distinct from ingestion/data_dragon/identity.py::normalize_filename, which
strips punctuation for icon filenames - wiki titles keep punctuation as-is
(confirmed: "Kai'Sa/LoL" resolves directly, no transformation needed).
"""

from __future__ import annotations

# Filled only when a real mismatch between a champion's display_name and its
# wiki page title is confirmed (not guessed).
_TITLE_OVERRIDES: dict[str, str] = {
    "Nunu & Willump": "Nunu",  # wiki gameplay page is "Nunu/LoL", not "Nunu & Willump/LoL"
}


def wiki_base_name(display_name: str) -> str:
    """The name used for every wiki title derived from this champion: the
    '/LoL' page, ability template transclusions, and per-slot template
    titles all key off this same base (e.g. "Nunu", not "Nunu & Willump")."""
    return _TITLE_OVERRIDES.get(display_name, display_name)


def gameplay_page_title(display_name: str) -> str:
    """The '/LoL' sub-page holds gameplay data; the bare display name is a
    lore/bio page."""
    return f"{wiki_base_name(display_name)}/LoL"
