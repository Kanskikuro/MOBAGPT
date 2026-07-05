"""Knowledge-database schema (Component 1 of docs/sepc.md).

Statistical DB tables (matchup_statistics, champion_synergy, champion_counters,
otp_builds, otp_players, matches, evaluation_runs) live in the same physical
SQLite file per docs/sepc.md's "Database" section, but are added in a later
migration when Phase 1's statistical/OTP ingestion is built. The model/
inference boundary is enforced by which query-layer module callers import
(see CLAUDE.md), not by physical file separation.
"""

from __future__ import annotations

import datetime

from sqlalchemy import JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Patch(Base):
    __tablename__ = "patches"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(unique=True)
    ingested_at: Mapped[datetime.datetime] = mapped_column(
        default=lambda: datetime.datetime.now(datetime.UTC)
    )


class Champion(Base):
    __tablename__ = "champions"

    champion_id: Mapped[int] = mapped_column(primary_key=True)
    riot_key: Mapped[str] = mapped_column(unique=True)
    display_name: Mapped[str]
    normalized_name: Mapped[str]
    title: Mapped[str | None]
    patch_id: Mapped[int | None] = mapped_column(ForeignKey("patches.id"))

    stats: Mapped["ChampionStats"] = relationship(
        back_populates="champion", cascade="all, delete-orphan", uselist=False
    )
    tags: Mapped[list["ChampionTag"]] = relationship(
        back_populates="champion", cascade="all, delete-orphan"
    )
    ratings: Mapped[list["ChampionRating"]] = relationship(
        back_populates="champion", cascade="all, delete-orphan"
    )
    abilities: Mapped[list["ChampionAbility"]] = relationship(
        back_populates="champion", cascade="all, delete-orphan"
    )


class ChampionStats(Base):
    __tablename__ = "champion_stats"

    champion_id: Mapped[int] = mapped_column(
        ForeignKey("champions.champion_id"), primary_key=True
    )
    hp: Mapped[float]
    hp_per_level: Mapped[float]
    mp: Mapped[float]
    mp_per_level: Mapped[float]
    move_speed: Mapped[float]
    armor: Mapped[float]
    armor_per_level: Mapped[float]
    spell_block: Mapped[float]
    spell_block_per_level: Mapped[float]
    attack_range: Mapped[float]
    hp_regen: Mapped[float]
    hp_regen_per_level: Mapped[float]
    mp_regen: Mapped[float]
    mp_regen_per_level: Mapped[float]
    crit: Mapped[float]
    crit_per_level: Mapped[float]
    attack_damage: Mapped[float]
    attack_damage_per_level: Mapped[float]
    attack_speed_per_level: Mapped[float]
    attack_speed: Mapped[float]

    # Riot's own coarse 1-10 ratings (Data Dragon "info" block) - distinct
    # from the LLM-derived semantic ratings in ChampionRating.
    attack_rating: Mapped[int | None]
    defense_rating: Mapped[int | None]
    magic_rating: Mapped[int | None]
    difficulty_rating: Mapped[int | None]

    champion: Mapped["Champion"] = relationship(back_populates="stats")


class ChampionTag(Base):
    """Both Riot's coarse tags (source='data_dragon') and, later, the
    semantic taxonomy from the knowledge/ tag pipeline (source='llm' /
    'override') per docs/sepc.md Component 1."""

    __tablename__ = "champion_tags"
    __table_args__ = (UniqueConstraint("champion_id", "tag", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    champion_id: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    tag: Mapped[str]
    source: Mapped[str] = mapped_column(default="data_dragon")

    champion: Mapped["Champion"] = relationship(back_populates="tags")


class ChampionRating(Base):
    """Numeric per-champion ratings (engage, frontline, scaling curve, etc.)
    from the knowledge/ pipeline. Not populated by ingestion/data_dragon —
    left empty until the rating pipeline (docs/sepc.md Component 1) exists."""

    __tablename__ = "champion_ratings"
    __table_args__ = (UniqueConstraint("champion_id", "rating_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    champion_id: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    rating_name: Mapped[str]
    value: Mapped[float]
    source: Mapped[str] = mapped_column(default="llm")

    champion: Mapped["Champion"] = relationship(back_populates="ratings")


class ChampionAbility(Base):
    __tablename__ = "champion_abilities"
    __table_args__ = (UniqueConstraint("champion_id", "slot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    champion_id: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    slot: Mapped[str]  # "passive", "Q", "W", "E", "R"
    ability_id: Mapped[str]
    name: Mapped[str]
    description: Mapped[str]
    cooldown: Mapped[dict | list | None] = mapped_column(JSON)
    cost: Mapped[dict | list | None] = mapped_column(JSON)
    ability_range: Mapped[dict | list | None] = mapped_column(JSON)
    raw_data: Mapped[dict] = mapped_column(JSON)

    champion: Mapped["Champion"] = relationship(back_populates="abilities")


class ChampionAbilityDetail(Base):
    """Wiki-sourced supplement to ChampionAbility: exact scalings and hidden
    mechanics Data Dragon's single description blurb omits (docs/sepc.md
    Data Sources #3). Keyed by (champion_id, slot) rather than a FK to
    ChampionAbility.id, since DataDragonSource deletes and re-inserts those
    rows on every run (unstable ids). `slot` is free-form, not constrained
    to passive/Q/W/E/R, because multi-form champions (e.g. Elise) have
    extra named slots (e.g. "Venomous Bite") with no Data Dragon counterpart.
    """

    __tablename__ = "champion_ability_details"

    champion_id: Mapped[int] = mapped_column(
        ForeignKey("champions.champion_id"), primary_key=True
    )
    slot: Mapped[str] = mapped_column(primary_key=True)
    notes: Mapped[str]
    tips: Mapped[str] = mapped_column(default="")
    fields: Mapped[dict] = mapped_column(JSON)
    raw_wikitext: Mapped[str]
    wiki_title: Mapped[str]
    source: Mapped[str] = mapped_column(default="wiki")
    patch_id: Mapped[int | None] = mapped_column(ForeignKey("patches.id"))
    fetched_at: Mapped[datetime.datetime] = mapped_column(
        default=lambda: datetime.datetime.now(datetime.UTC)
    )


class Item(Base):
    __tablename__ = "items"

    item_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    description: Mapped[str]
    plaintext: Mapped[str | None]
    gold_base: Mapped[int]
    gold_total: Mapped[int]
    gold_sell: Mapped[int]
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    depth: Mapped[int | None]
    builds_from: Mapped[list] = mapped_column(JSON, default=list)
    builds_into: Mapped[list] = mapped_column(JSON, default=list)
    patch_id: Mapped[int | None] = mapped_column(ForeignKey("patches.id"))
    raw_data: Mapped[dict] = mapped_column(JSON)

    tags: Mapped[list["ItemTag"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    effects: Mapped[list["ItemEffect"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class ItemTag(Base):
    __tablename__ = "item_tags"
    __table_args__ = (UniqueConstraint("item_id", "tag", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.item_id"))
    tag: Mapped[str]
    source: Mapped[str] = mapped_column(default="data_dragon")

    item: Mapped["Item"] = relationship(back_populates="tags")


class ItemEffect(Base):
    """Structured passive/active effect breakdown. Data Dragon only gives a
    single HTML description blob; splitting it into discrete effects is
    knowledge/ pipeline work, not done by ingestion/data_dragon. Left
    unpopulated until that pipeline exists."""

    __tablename__ = "item_effects"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.item_id"))
    effect_type: Mapped[str]
    description: Mapped[str]
    raw_data: Mapped[dict | None] = mapped_column(JSON)

    item: Mapped["Item"] = relationship(back_populates="effects")


class ItemWikiDetail(Base):
    """Wiki-sourced supplement to Item: Data Dragon's description is
    sometimes empty or too shallow for items with complex mechanics (e.g.
    quest/support items like World Atlas, whose real behavior - charge
    timing, upgrade thresholds - only exists in prose). Unlike
    ChampionAbilityDetail, items have no per-slot concept - one row per
    item, keyed directly on item_id since Data Dragon's Item upsert doesn't
    delete-and-reinsert (merge() on the natural id is stable across runs)."""

    __tablename__ = "item_wiki_details"

    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.item_id"), primary_key=True
    )
    notes: Mapped[str]
    tips: Mapped[str] = mapped_column(default="")
    fields: Mapped[dict] = mapped_column(JSON)
    raw_wikitext: Mapped[str]
    wiki_title: Mapped[str]
    source: Mapped[str] = mapped_column(default="wiki")
    patch_id: Mapped[int | None] = mapped_column(ForeignKey("patches.id"))
    fetched_at: Mapped[datetime.datetime] = mapped_column(
        default=lambda: datetime.datetime.now(datetime.UTC)
    )


class Rune(Base):
    __tablename__ = "runes"

    rune_id: Mapped[int] = mapped_column(primary_key=True)
    path_name: Mapped[str]  # e.g. "Domination"
    slot: Mapped[int]
    name: Mapped[str]
    short_desc: Mapped[str]
    long_desc: Mapped[str]
    patch_id: Mapped[int | None] = mapped_column(ForeignKey("patches.id"))
    raw_data: Mapped[dict] = mapped_column(JSON)

    tags: Mapped[list["RuneTag"]] = relationship(
        back_populates="rune", cascade="all, delete-orphan"
    )


class RuneTag(Base):
    """Semantic tags for runes from the knowledge/ pipeline. Data Dragon has
    no native rune tags, so this is empty until that pipeline exists."""

    __tablename__ = "rune_tags"
    __table_args__ = (UniqueConstraint("rune_id", "tag", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rune_id: Mapped[int] = mapped_column(ForeignKey("runes.rune_id"))
    tag: Mapped[str]
    source: Mapped[str] = mapped_column(default="llm")

    rune: Mapped["Rune"] = relationship(back_populates="tags")


class BuildArchetype(Base):
    """Per docs/sepc.md: 'for every champion, cluster observed builds into
    named archetypes'. Populated by the build-archetype-extraction pipeline
    (needs statistical + OTP data), not by ingestion/data_dragon."""

    __tablename__ = "build_archetypes"
    __table_args__ = (UniqueConstraint("champion_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    champion_id: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    name: Mapped[str]  # e.g. "AD assassin"
    role: Mapped[str | None]

    items: Mapped[list["ArchetypeItem"]] = relationship(
        back_populates="archetype", cascade="all, delete-orphan"
    )
    runes: Mapped[list["ArchetypeRune"]] = relationship(
        back_populates="archetype", cascade="all, delete-orphan"
    )
    tags: Mapped[list["ArchetypeTag"]] = relationship(
        back_populates="archetype", cascade="all, delete-orphan"
    )


class ArchetypeItem(Base):
    __tablename__ = "archetype_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    archetype_id: Mapped[int] = mapped_column(ForeignKey("build_archetypes.id"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.item_id"))
    build_order: Mapped[int | None]
    is_situational: Mapped[bool] = mapped_column(default=False)

    archetype: Mapped["BuildArchetype"] = relationship(back_populates="items")


class ArchetypeRune(Base):
    __tablename__ = "archetype_runes"

    id: Mapped[int] = mapped_column(primary_key=True)
    archetype_id: Mapped[int] = mapped_column(ForeignKey("build_archetypes.id"))
    rune_id: Mapped[int] = mapped_column(ForeignKey("runes.rune_id"))
    is_keystone: Mapped[bool] = mapped_column(default=False)

    archetype: Mapped["BuildArchetype"] = relationship(back_populates="runes")


class ArchetypeTag(Base):
    """The functional profile (tags + numeric deltas) an archetype confers,
    per docs/sepc.md Component 1."""

    __tablename__ = "archetype_tags"
    __table_args__ = (UniqueConstraint("archetype_id", "tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    archetype_id: Mapped[int] = mapped_column(ForeignKey("build_archetypes.id"))
    tag: Mapped[str]
    delta: Mapped[float] = mapped_column(default=0.0)

    archetype: Mapped["BuildArchetype"] = relationship(back_populates="tags")
