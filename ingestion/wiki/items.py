"""Item wiki enrichment: unlike champions, an item has no lore/gameplay page
split and no per-slot sub-templates - confirmed on Infinity Edge, World
Atlas, and a punctuated name (Rabadon's Deathcap): the item's own page
(its Data Dragon name, used directly as the wiki title) has a single
`{{Item info}}` template on it, parsed with the same generic
`parse_wiki_template` used for champion abilities.
"""

from __future__ import annotations

from dataclasses import dataclass

from ingestion.wiki import client
from ingestion.wiki.wikitext import ParsedWikiTemplate, parse_wiki_template

# Filled only when a real mismatch between an item's Data Dragon name and
# its wiki page title is confirmed (not guessed). MediaWiki titles are
# case-sensitive past the first character, so this catches capitalization
# drift, not just renames - e.g. Data Dragon's "Blade of The Ruined King"
# vs. the wiki's "Blade of the Ruined King".
_TITLE_OVERRIDES: dict[str, str] = {
    "Blade of The Ruined King": "Blade of the Ruined King",
}


@dataclass
class ItemWikiData:
    parsed: ParsedWikiTemplate
    wiki_title: str


def fetch_item_wiki_data(item_name: str) -> ItemWikiData:
    title = _TITLE_OVERRIDES.get(item_name, item_name)
    page = client.fetch_wikitext(title, redirects=True)
    return ItemWikiData(parsed=parse_wiki_template(page.wikitext), wiki_title=page.resolved_title)
