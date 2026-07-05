"""Champion ability enrichment orchestration: discover which ability slots a
champion actually has (do not assume passive/Q/W/E/R - multi-form champions
like Elise have extra named slots), fetch each, and parse it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ingestion.wiki import client
from ingestion.wiki.identity import gameplay_page_title, wiki_base_name
from ingestion.wiki.wikitext import ParsedWikiTemplate, discover_ability_slots, parse_wiki_template

_SLOT_ALIASES = {"I": "passive"}


@dataclass
class ChampionAbilities:
    champion_display_name: str
    abilities: dict[str, ParsedWikiTemplate]
    wiki_titles: dict[str, str]


def _find_abilities_section_index(title: str) -> str | None:
    for section in client.fetch_sections(title):
        if section["line"] == "Abilities":
            return section["index"]
    return None


def fetch_champion_abilities(display_name: str) -> ChampionAbilities:
    base_name = wiki_base_name(display_name)
    page_title = gameplay_page_title(display_name)
    section_index = _find_abilities_section_index(page_title)

    if section_index is None:
        raise client.WikiPageNotFoundError(f"{page_title}#Abilities")

    section_page = client.fetch_wikitext(page_title, section=section_index)
    slots = discover_ability_slots(section_page.wikitext, base_name)

    abilities: dict[str, ParsedWikiTemplate] = {}
    wiki_titles: dict[str, str] = {}

    for slot in slots:
        template_title = f"Template:Data {base_name}/{slot}"
        page = client.fetch_wikitext(template_title, redirects=True)
        normalized_slot = _SLOT_ALIASES.get(slot, slot)
        abilities[normalized_slot] = parse_wiki_template(page.wikitext)
        wiki_titles[normalized_slot] = page.resolved_title

    return ChampionAbilities(
        champion_display_name=display_name,
        abilities=abilities,
        wiki_titles=wiki_titles,
    )
