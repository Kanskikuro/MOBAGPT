"""Data Dragon ingestion source: champions (incl. abilities), items, runes.

Rated 'Reuse (as a starting point)' in docs/skadz_audit.md against
SKADZALGORITM's scripts/download_champions_and_icons.py — the version
discovery pattern is carried over; this module fetches into the DB instead
of a CSV, and additionally pulls abilities, items, and runes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    Champion,
    ChampionAbility,
    ChampionStats,
    ChampionTag,
    Item,
    ItemTag,
    Patch,
    Rune,
)
from ingestion.base import IngestionSource
from ingestion.data_dragon import client
from ingestion.data_dragon.identity import normalize_filename

_SPELL_SLOTS = ["Q", "W", "E", "R"]


class DataDragonSource(IngestionSource):
    name = "data_dragon"

    def resolve_patch(self, patch: str | None) -> str:
        return client.resolve_version(patch)

    def fetch(self, patch: str) -> dict[str, Any]:
        champion_list = client.fetch_champion_list(patch)
        champions_full = [
            client.fetch_champion_full(patch, riot_key)
            for riot_key in champion_list
        ]
        items = client.fetch_items(patch)
        rune_trees = client.fetch_rune_trees(patch)

        return {
            "champions": champions_full,
            "items": items,
            "rune_trees": rune_trees,
        }

    def load(self, session: Session, patch: str, data: dict[str, Any]) -> dict[str, int]:
        patch_row = _get_or_create_patch(session, patch)
        session.flush()

        champion_count = 0
        for champ_data in data["champions"]:
            _upsert_champion(session, patch_row.id, champ_data)
            champion_count += 1

        item_count = 0
        for item_id_str, item_data in data["items"].items():
            _upsert_item(session, patch_row.id, item_id_str, item_data)
            item_count += 1

        rune_count = 0
        for tree in data["rune_trees"]:
            rune_count += _upsert_rune_tree(session, patch_row.id, tree)

        return {
            "champions": champion_count,
            "items": item_count,
            "runes": rune_count,
        }


def _get_or_create_patch(session: Session, version: str) -> Patch:
    existing = session.execute(
        select(Patch).where(Patch.version == version)
    ).scalar_one_or_none()

    if existing is not None:
        return existing

    patch_row = Patch(version=version)
    session.add(patch_row)
    return patch_row


def _upsert_champion(session: Session, patch_id: int, champ_data: dict) -> None:
    champion_id = int(champ_data["key"])
    display_name = champ_data["name"]

    session.merge(
        Champion(
            champion_id=champion_id,
            riot_key=champ_data["id"],
            display_name=display_name,
            normalized_name=normalize_filename(display_name),
            title=champ_data.get("title"),
            patch_id=patch_id,
        )
    )

    stats = champ_data["stats"]
    info = champ_data.get("info", {})

    session.merge(
        ChampionStats(
            champion_id=champion_id,
            hp=stats["hp"],
            hp_per_level=stats["hpperlevel"],
            mp=stats["mp"],
            mp_per_level=stats["mpperlevel"],
            move_speed=stats["movespeed"],
            armor=stats["armor"],
            armor_per_level=stats["armorperlevel"],
            spell_block=stats["spellblock"],
            spell_block_per_level=stats["spellblockperlevel"],
            attack_range=stats["attackrange"],
            hp_regen=stats["hpregen"],
            hp_regen_per_level=stats["hpregenperlevel"],
            mp_regen=stats["mpregen"],
            mp_regen_per_level=stats["mpregenperlevel"],
            crit=stats["crit"],
            crit_per_level=stats["critperlevel"],
            attack_damage=stats["attackdamage"],
            attack_damage_per_level=stats["attackdamageperlevel"],
            attack_speed_per_level=stats["attackspeedperlevel"],
            attack_speed=stats["attackspeed"],
            attack_rating=info.get("attack"),
            defense_rating=info.get("defense"),
            magic_rating=info.get("magic"),
            difficulty_rating=info.get("difficulty"),
        )
    )

    session.execute(
        ChampionTag.__table__.delete().where(
            ChampionTag.champion_id == champion_id,
            ChampionTag.source == "data_dragon",
        )
    )
    for tag in champ_data.get("tags", []):
        session.add(ChampionTag(champion_id=champion_id, tag=tag, source="data_dragon"))

    session.execute(
        ChampionAbility.__table__.delete().where(
            ChampionAbility.champion_id == champion_id
        )
    )

    passive = champ_data.get("passive")
    if passive:
        session.add(
            ChampionAbility(
                champion_id=champion_id,
                slot="passive",
                ability_id=f"{champ_data['id']}Passive",
                name=passive["name"],
                description=passive.get("description", ""),
                cooldown=None,
                cost=None,
                ability_range=None,
                raw_data=passive,
            )
        )

    for slot, spell in zip(_SPELL_SLOTS, champ_data.get("spells", [])):
        session.add(
            ChampionAbility(
                champion_id=champion_id,
                slot=slot,
                ability_id=spell.get("id", f"{champ_data['id']}{slot}"),
                name=spell["name"],
                description=spell.get("description", ""),
                cooldown=spell.get("cooldown"),
                cost=spell.get("cost"),
                ability_range=spell.get("range"),
                raw_data=spell,
            )
        )


def _upsert_item(session: Session, patch_id: int, item_id_str: str, item_data: dict) -> None:
    item_id = int(item_id_str)
    gold = item_data.get("gold", {})

    session.merge(
        Item(
            item_id=item_id,
            name=item_data["name"],
            description=item_data.get("description", ""),
            plaintext=item_data.get("plaintext"),
            gold_base=gold.get("base", 0),
            gold_total=gold.get("total", 0),
            gold_sell=gold.get("sell", 0),
            patch_id=patch_id,
            raw_data=item_data,
        )
    )

    session.execute(
        ItemTag.__table__.delete().where(
            ItemTag.item_id == item_id,
            ItemTag.source == "data_dragon",
        )
    )
    for tag in item_data.get("tags", []):
        session.add(ItemTag(item_id=item_id, tag=tag, source="data_dragon"))


def _upsert_rune_tree(session: Session, patch_id: int, tree: dict) -> int:
    count = 0
    path_name = tree["name"]

    for slot_index, slot in enumerate(tree.get("slots", [])):
        for rune in slot.get("runes", []):
            session.merge(
                Rune(
                    rune_id=rune["id"],
                    path_name=path_name,
                    slot=slot_index,
                    name=rune["name"],
                    short_desc=rune.get("shortDesc", ""),
                    long_desc=rune.get("longDesc", ""),
                    patch_id=patch_id,
                    raw_data=rune,
                )
            )
            count += 1

    return count
