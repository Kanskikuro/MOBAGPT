"""Wiki ingestion source: champion ability enrichment + item enrichment.

Supplements ingestion/data_dragon's champion_abilities and items with exact
scalings and hidden mechanics from leagueoflegends.fandom.com (docs/sepc.md
Data Sources #3). Enriches champions/items already in the DB - it does not
create them - so it needs an explicit --patch matching an already-ingested
Data Dragon version; wiki content has no independent "latest" concept.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Champion, ChampionAbilityDetail, Item, ItemWikiDetail, Patch
from ingestion.base import IngestionSource
from ingestion.data_dragon.client import fetch_champion_list, fetch_items
from ingestion.data_dragon.source import is_summoners_rift_item
from ingestion.wiki import champions as wiki_champions
from ingestion.wiki import items as wiki_items
from ingestion.wiki.client import WikiPageNotFoundError


class WikiSource(IngestionSource):
    name = "wiki"

    def resolve_patch(self, patch: str | None) -> str:
        return patch if patch is not None else "unversioned"

    def fetch(self, patch: str) -> Any:
        if patch == "unversioned":
            raise ValueError(
                "wiki source requires an explicit --patch matching an "
                "already-ingested Data Dragon version (wiki content has no "
                "'latest' concept of its own)."
            )

        return {
            "champions": self._fetch_champion_abilities(patch),
            "items": self._fetch_item_details(patch),
        }

    def _fetch_champion_abilities(self, patch: str) -> list[dict]:
        champion_list = fetch_champion_list(patch)
        results = []

        for riot_key, champ_data in champion_list.items():
            display_name = champ_data["name"]
            try:
                abilities = wiki_champions.fetch_champion_abilities(display_name)
            except WikiPageNotFoundError as exc:
                results.append(
                    {"riot_key": riot_key, "display_name": display_name, "error": str(exc)}
                )
                continue

            results.append(
                {"riot_key": riot_key, "display_name": display_name, "abilities": abilities}
            )

        return results

    def _fetch_item_details(self, patch: str) -> list[dict]:
        item_list = fetch_items(patch)
        results = []

        for item_id_str, item_data in item_list.items():
            if not is_summoners_rift_item(item_data):
                continue

            name = item_data["name"]
            item_id = int(item_id_str)
            try:
                wiki_data = wiki_items.fetch_item_wiki_data(name)
            except WikiPageNotFoundError as exc:
                results.append({"item_id": item_id, "name": name, "error": str(exc)})
                continue

            results.append({"item_id": item_id, "name": name, "wiki_data": wiki_data})

        return results

    def load(self, session: Session, patch: str, data: Any) -> dict[str, int]:
        patch_row = session.execute(
            select(Patch).where(Patch.version == patch)
        ).scalar_one_or_none()
        patch_id = patch_row.id if patch_row is not None else None

        if patch_row is None:
            self.warnings.append(
                f"Patch '{patch}' not found in DB; storing rows with patch_id=None"
            )

        all_unknown_templates: set[str] = set()

        ability_count = self._load_champion_abilities(
            session, patch_id, data["champions"], all_unknown_templates
        )
        item_count = self._load_item_details(
            session, patch_id, data["items"], all_unknown_templates
        )

        if all_unknown_templates:
            self.warnings.append(
                "Unrecognized decorator templates encountered (left verbatim): "
                + ", ".join(sorted(all_unknown_templates))
            )

        return {
            "champion_ability_details": ability_count,
            "item_wiki_details": item_count,
        }

    def _load_champion_abilities(
        self,
        session: Session,
        patch_id: int | None,
        entries: list[dict],
        all_unknown_templates: set[str],
    ) -> int:
        detail_count = 0

        for entry in entries:
            if "error" in entry:
                self.warnings.append(f"{entry['display_name']}: {entry['error']}")
                continue

            champion = session.execute(
                select(Champion).where(Champion.riot_key == entry["riot_key"])
            ).scalar_one_or_none()

            if champion is None:
                self.warnings.append(
                    f"Champion '{entry['riot_key']}' not found in DB; skipping wiki enrichment"
                )
                continue

            abilities: wiki_champions.ChampionAbilities = entry["abilities"]

            for slot, parsed in abilities.abilities.items():
                session.merge(
                    ChampionAbilityDetail(
                        champion_id=champion.champion_id,
                        slot=slot,
                        notes=parsed.notes,
                        tips=parsed.tips,
                        fields=parsed.fields,
                        raw_wikitext=parsed.raw_wikitext,
                        wiki_title=abilities.wiki_titles[slot],
                        patch_id=patch_id,
                        fetched_at=datetime.datetime.now(datetime.UTC),
                    )
                )
                detail_count += 1
                all_unknown_templates |= parsed.unknown_templates

        return detail_count

    def _load_item_details(
        self,
        session: Session,
        patch_id: int | None,
        entries: list[dict],
        all_unknown_templates: set[str],
    ) -> int:
        detail_count = 0

        for entry in entries:
            if "error" in entry:
                self.warnings.append(f"{entry['name']}: {entry['error']}")
                continue

            item = session.execute(
                select(Item).where(Item.item_id == entry["item_id"])
            ).scalar_one_or_none()

            if item is None:
                self.warnings.append(
                    f"Item '{entry['name']}' not found in DB; skipping wiki enrichment"
                )
                continue

            wiki_data: wiki_items.ItemWikiData = entry["wiki_data"]
            parsed = wiki_data.parsed

            session.merge(
                ItemWikiDetail(
                    item_id=item.item_id,
                    notes=parsed.notes,
                    tips=parsed.tips,
                    fields=parsed.fields,
                    raw_wikitext=parsed.raw_wikitext,
                    wiki_title=wiki_data.wiki_title,
                    patch_id=patch_id,
                    fetched_at=datetime.datetime.now(datetime.UTC),
                )
            )
            detail_count += 1
            all_unknown_templates |= parsed.unknown_templates

        return detail_count
