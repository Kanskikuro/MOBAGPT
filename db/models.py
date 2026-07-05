"""Knowledge-database schema (Component 1 of docs/sepc.md), plus the
statistical DB schema (Component 2): Match, MatchParticipant,
MatchupStatistics, ChampionSynergy, ChampionCounters, ItemStatistics,
RuneStatistics, MatchTimeline, BuildPathStatistics, SkillOrderStatistics,
all populated by ingestion/riot_api.

The OTP DB tables (otp_builds, otp_players, evaluation_runs) live in the
same physical SQLite file per docs/sepc.md's "Database" section, but are
added in a later migration when that ingestion exists. The model/inference
boundary is enforced by which query-layer module callers import (see
CLAUDE.md), not by physical file separation.
"""

from __future__ import annotations

import datetime

from sqlalchemy import JSON, CheckConstraint, ForeignKey, UniqueConstraint
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


class Match(Base):
    """Raw Match-V5 match summary. `match_id` (Riot's string id, e.g.
    "NA1_1234567890") is the natural key: match data is immutable once a
    game is played, so re-ingesting an already-known match_id is a no-op
    rather than requiring delete-then-reinsert like the mutable
    data_dragon child collections."""

    __tablename__ = "matches"

    match_id: Mapped[str] = mapped_column(primary_key=True)
    patch_id: Mapped[int | None] = mapped_column(ForeignKey("patches.id"))
    queue_id: Mapped[int]
    game_duration: Mapped[int]
    game_creation: Mapped[datetime.datetime]
    region: Mapped[str]
    raw_data: Mapped[dict] = mapped_column(JSON)

    participants: Mapped[list["MatchParticipant"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )


class MatchParticipant(Base):
    """One row per champion per match. Keyed on (match_id, champion_id) -
    not puuid - since this project has no use for player identity, only
    the champion/role/outcome tuple needed for matchup_statistics and (in a
    later slice) champion_synergy/champion_counters."""

    __tablename__ = "match_participants"
    __table_args__ = (UniqueConstraint("match_id", "champion_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.match_id"))
    champion_id: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    team_position: Mapped[str]  # Riot's TOP/JUNGLE/MIDDLE/BOTTOM/UTILITY
    win: Mapped[bool]
    kills: Mapped[int]
    deaths: Mapped[int]
    assists: Mapped[int]
    raw_data: Mapped[dict] = mapped_column(JSON)

    match: Mapped["Match"] = relationship(back_populates="participants")


class MatchupStatistics(Base):
    """Per (patch, champion, role) win/pick/ban rate, per docs/sepc.md
    Component 2. Recomputed in full from match_participants/matches on every
    ingestion/riot_api run for the resolved patch - a derived aggregate,
    not a natural upsert, so it's deleted-and-reinserted per patch rather
    than incrementally merged."""

    __tablename__ = "matchup_statistics"
    __table_args__ = (UniqueConstraint("patch_id", "champion_id", "role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    patch_id: Mapped[int] = mapped_column(ForeignKey("patches.id"))
    champion_id: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    role: Mapped[str]
    games: Mapped[int]
    wins: Mapped[int]
    picks: Mapped[int]
    bans: Mapped[int]
    win_rate: Mapped[float]
    pick_rate: Mapped[float]
    ban_rate: Mapped[float]
    sample_size: Mapped[int]


class ChampionSynergy(Base):
    """Symmetric ally-pair win rate per (patch, champion+role, champion+role),
    per docs/sepc.md Component 2. Canonicalized champion_id_a < champion_id_b
    so a pair is stored once rather than twice. Recomputed in full on every
    ingestion/riot_api run, same rationale as MatchupStatistics: a derived
    aggregate, not a natural upsert."""

    __tablename__ = "champion_synergy"
    __table_args__ = (
        UniqueConstraint("patch_id", "champion_id_a", "role_a", "champion_id_b", "role_b"),
        CheckConstraint("champion_id_a < champion_id_b"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patch_id: Mapped[int] = mapped_column(ForeignKey("patches.id"))
    champion_id_a: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    role_a: Mapped[str]
    champion_id_b: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    role_b: Mapped[str]
    games: Mapped[int]
    wins: Mapped[int]  # times this pair was on the winning side together
    win_rate: Mapped[float]
    sample_size: Mapped[int]


class ChampionCounters(Base):
    """Directional: how champion+role performs specifically against
    enemy_champion+enemy_role, per docs/sepc.md Component 2. Not symmetric -
    the reverse pairing is a separate row (derivable from the same match set,
    but stored explicitly so training code never needs a reverse-lookup
    rule). Recomputed in full on every ingestion/riot_api run, same
    rationale as MatchupStatistics."""

    __tablename__ = "champion_counters"
    __table_args__ = (
        UniqueConstraint("patch_id", "champion_id", "role", "enemy_champion_id", "enemy_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patch_id: Mapped[int] = mapped_column(ForeignKey("patches.id"))
    champion_id: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    role: Mapped[str]
    enemy_champion_id: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    enemy_role: Mapped[str]
    games: Mapped[int]
    wins: Mapped[int]
    win_rate: Mapped[float]
    sample_size: Mapped[int]


class ItemStatistics(Base):
    """Per (patch, champion, role, item) win rate, from participants' final
    item builds (item0-item5; item6/trinket excluded - near-uniform per
    role, not informative for build comparison). Recomputed in full per
    patch, same rationale as MatchupStatistics."""

    __tablename__ = "item_statistics"
    __table_args__ = (UniqueConstraint("patch_id", "champion_id", "role", "item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    patch_id: Mapped[int] = mapped_column(ForeignKey("patches.id"))
    champion_id: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    role: Mapped[str]
    item_id: Mapped[int] = mapped_column(ForeignKey("items.item_id"))
    games: Mapped[int]  # champion+role's total games this patch
    picks: Mapped[int]  # of those, games where this item was in the final build
    wins: Mapped[int]
    win_rate: Mapped[float]  # wins / picks
    pick_rate: Mapped[float]  # picks / games - "build rate" given champion+role
    sample_size: Mapped[int]  # == picks


class RuneStatistics(Base):
    """Per (patch, champion, role, rune) win rate, from participants'
    selected runes (both primary and secondary paths, 6 selections total).
    Stat-shard perks (statPerks.offense/flex/defense, e.g. 5008/5002/5001)
    are NOT included - Data Dragon's rune tree (what populates the `runes`
    table) has no entries for them at all, so there's no Rune row to key
    against; tracked as a known gap, not solved here."""

    __tablename__ = "rune_statistics"
    __table_args__ = (UniqueConstraint("patch_id", "champion_id", "role", "rune_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    patch_id: Mapped[int] = mapped_column(ForeignKey("patches.id"))
    champion_id: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    role: Mapped[str]
    rune_id: Mapped[int] = mapped_column(ForeignKey("runes.rune_id"))
    is_keystone: Mapped[bool]
    games: Mapped[int]
    picks: Mapped[int]
    wins: Mapped[int]
    win_rate: Mapped[float]
    pick_rate: Mapped[float]
    sample_size: Mapped[int]


class MatchTimeline(Base):
    """Raw Match-V5 timeline (frame-by-frame event log), keyed on the same
    natural match_id as Match - immutable once played, so re-ingesting an
    already-known match_id is a no-op, same rationale as Match.raw_data.
    Feeds BuildPathStatistics/SkillOrderStatistics; kept as its own table
    (rather than a second JSON column on Match) since it's a separate Riot
    endpoint fetched in its own request."""

    __tablename__ = "match_timelines"

    match_id: Mapped[str] = mapped_column(ForeignKey("matches.match_id"), primary_key=True)
    raw_data: Mapped[dict] = mapped_column(JSON)


class BuildPathStatistics(Base):
    """Per (patch, champion, role, purchase_order, item) win rate, from
    participants' completed-item purchase order in ingestion/riot_api's
    timeline processing. purchase_order is 1-based (1st/2nd/3rd/...
    completed item this champion+role bought), capped at
    RiotApiSettings.build_path_slots. 'Completed item' means a terminal,
    non-consumable/non-trinket item (item_tags has no Consumable/Trinket
    tag and Item.builds_into is empty) - see source.py's _terminal_item_ids
    docstring for why depth alone isn't a reliable signal. Recomputed in
    full per patch, same rationale as ItemStatistics."""

    __tablename__ = "build_path_statistics"
    __table_args__ = (
        UniqueConstraint("patch_id", "champion_id", "role", "purchase_order", "item_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patch_id: Mapped[int] = mapped_column(ForeignKey("patches.id"))
    champion_id: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    role: Mapped[str]
    purchase_order: Mapped[int]
    item_id: Mapped[int] = mapped_column(ForeignKey("items.item_id"))
    games: Mapped[int]  # champion+role's total games this patch
    picks: Mapped[int]  # of those, games where this item was completed at this purchase_order
    wins: Mapped[int]
    win_rate: Mapped[float]
    pick_rate: Mapped[float]
    sample_size: Mapped[int]


class SkillOrderStatistics(Base):
    """Per (patch, champion, role, level, skill_slot, level_up_type) pick
    rate, from participants' SKILL_LEVEL_UP timeline events. skill_slot is
    Riot's 1=Q/2=W/3=E/4=R; level is the champion level the point was spent
    at, derived from event order (one skill point per level - Riot's raw
    event carries no level field). level_up_type ('NORMAL' or 'EVOLVE') is
    part of the unique key since a rare evolve pick (Kled, Rengar, ...) can
    share a level/slot with a normal pick across different games.
    Recomputed in full per patch, same rationale as ItemStatistics."""

    __tablename__ = "skill_order_statistics"
    __table_args__ = (
        UniqueConstraint(
            "patch_id", "champion_id", "role", "level", "skill_slot", "level_up_type"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    patch_id: Mapped[int] = mapped_column(ForeignKey("patches.id"))
    champion_id: Mapped[int] = mapped_column(ForeignKey("champions.champion_id"))
    role: Mapped[str]
    level: Mapped[int]
    skill_slot: Mapped[int]
    level_up_type: Mapped[str]
    games: Mapped[int]
    picks: Mapped[int]
    wins: Mapped[int]
    win_rate: Mapped[float]
    pick_rate: Mapped[float]
    sample_size: Mapped[int]
