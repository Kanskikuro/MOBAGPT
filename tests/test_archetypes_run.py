import dataclasses
import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import ARCHETYPES
from db.models import (
    ArchetypeItem,
    ArchetypeRune,
    ArchetypeTag,
    BuildArchetype,
    BuildPathStatistics,
    Champion,
    Item,
    ItemTag,
    Match,
    MatchParticipant,
    MatchupStatistics,
    Patch,
    Rune,
    RuneStatistics,
    RuneTag,
)
from knowledge.archetypes.run import run_archetype_extraction


def _patch_thresholds(monkeypatch, **overrides):
    patched = dataclasses.replace(ARCHETYPES, **overrides)
    for module in (
        "knowledge.archetypes.builds",
        "knowledge.archetypes.extraction",
        "knowledge.archetypes.naming",
        "knowledge.archetypes.deltas",
    ):
        monkeypatch.setattr(f"{module}.ARCHETYPES", patched)
    return patched


def _seed_common(session: Session) -> Patch:
    patch = Patch(version="14.14.1")
    session.add(patch)
    session.flush()

    session.add(
        Champion(
            champion_id=103, riot_key="Ahri", display_name="Ahri",
            normalized_name="ahri", title="the Nine-Tailed Fox",
        )
    )
    session.add(
        Item(
            item_id=1, name="Item1", description="", plaintext="",
            gold_base=0, gold_total=0, gold_sell=0,
            stats={"FlatMagicDamageMod": 100}, raw_data={},
        )
    )
    session.add(
        Item(
            item_id=2, name="Item2", description="", plaintext="",
            gold_base=0, gold_total=0, gold_sell=0,
            stats={"FlatArmorMod": 40}, raw_data={},
        )
    )
    session.add(
        Rune(
            rune_id=100, path_name="Domination", slot=0, name="Rune100",
            short_desc="", long_desc="", raw_data={},
        )
    )
    session.add(
        Rune(
            rune_id=200, path_name="Domination", slot=1, name="Rune200",
            short_desc="", long_desc="", raw_data={},
        )
    )
    session.add(ItemTag(item_id=1, tag="burst", source="llm"))
    session.add(ItemTag(item_id=2, tag="tank", source="llm"))
    session.add(RuneTag(rune_id=100, tag="burst", source="llm"))
    session.flush()
    return patch


def _seed_matches(session: Session, patch: Patch, count: int) -> None:
    for i in range(count):
        match_id = f"NA1_{i}"
        session.add(
            Match(
                match_id=match_id, patch_id=patch.id, queue_id=420, game_duration=1800,
                game_creation=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC),
                region="americas", raw_data={},
            )
        )
        session.add(
            MatchParticipant(
                match_id=match_id, champion_id=103, team_position="MIDDLE", win=True,
                kills=5, deaths=1, assists=7,
                raw_data={
                    "item0": 1, "item1": 2, "item2": 0, "item3": 0, "item4": 0, "item5": 0,
                    "perks": {
                        "styles": [
                            {"selections": [{"perk": 100}]},
                            {"selections": [{"perk": 200}]},
                        ]
                    },
                },
            )
        )


def test_run_archetype_extraction_end_to_end(session: Session, monkeypatch) -> None:
    _patch_thresholds(monkeypatch, min_builds_per_champion_role=2, min_cluster_weight=1.0)

    patch = _seed_common(session)
    _seed_matches(session, patch, count=3)

    session.add(
        MatchupStatistics(
            patch_id=patch.id, champion_id=103, role="MIDDLE", games=3, wins=3,
            picks=3, bans=0, win_rate=1.0, pick_rate=1.0, ban_rate=0.0, sample_size=3,
        )
    )
    session.add(
        BuildPathStatistics(
            patch_id=patch.id, champion_id=103, role="MIDDLE", purchase_order=1,
            item_id=1, games=3, picks=3, wins=3, win_rate=1.0, pick_rate=1.0, sample_size=3,
        )
    )
    session.add(
        BuildPathStatistics(
            patch_id=patch.id, champion_id=103, role="MIDDLE", purchase_order=2,
            item_id=2, games=3, picks=3, wins=3, win_rate=1.0, pick_rate=1.0, sample_size=3,
        )
    )
    session.add(
        RuneStatistics(
            patch_id=patch.id, champion_id=103, role="MIDDLE", rune_id=100, is_keystone=True,
            games=3, picks=3, wins=3, win_rate=1.0, pick_rate=1.0, sample_size=3,
        )
    )
    session.add(
        RuneStatistics(
            patch_id=patch.id, champion_id=103, role="MIDDLE", rune_id=200, is_keystone=False,
            games=3, picks=3, wins=3, win_rate=1.0, pick_rate=1.0, sample_size=3,
        )
    )
    session.commit()

    result = run_archetype_extraction(session, patch="14.14.1")

    assert result.warnings == []
    assert result.champions_processed == 1
    assert result.archetypes_created == 1

    archetype = session.execute(select(BuildArchetype).where(BuildArchetype.champion_id == 103)).scalar_one()
    assert archetype.role == "MIDDLE"
    assert archetype.name == "AP Burst"  # item1 is AP-only, dominant tag is burst (0.5 fraction)

    items_by_id = {i.item_id: i for i in archetype.items}
    assert items_by_id[1].build_order == 1
    assert items_by_id[1].is_situational is False
    assert items_by_id[2].build_order == 2

    runes_by_id = {r.rune_id: r for r in archetype.runes}
    assert runes_by_id[100].is_keystone is True
    assert runes_by_id[200].is_keystone is False

    ratings = {t.tag: t.delta for t in archetype.tags}
    assert ratings["burst"] == ARCHETYPES.archetype_delta_scale * 0.5
    assert ratings["frontline"] == ARCHETYPES.archetype_delta_scale * 0.25


def test_run_archetype_extraction_skips_thin_champion_role(session: Session, monkeypatch) -> None:
    _patch_thresholds(monkeypatch, min_builds_per_champion_role=5, min_cluster_weight=1.0)

    patch = _seed_common(session)
    _seed_matches(session, patch, count=1)  # only 1 build, below the threshold of 5

    session.add(
        MatchupStatistics(
            patch_id=patch.id, champion_id=103, role="MIDDLE", games=1, wins=1,
            picks=1, bans=0, win_rate=1.0, pick_rate=1.0, ban_rate=0.0, sample_size=1,
        )
    )
    session.commit()

    result = run_archetype_extraction(session, patch="14.14.1")

    assert result.champions_processed == 1
    assert result.archetypes_created == 0
    assert session.execute(select(BuildArchetype)).first() is None


def test_run_archetype_extraction_unknown_patch_warns(session: Session) -> None:
    result = run_archetype_extraction(session, patch="99.99")
    assert result.champions_processed == 0
    assert any("no ingested patch" in w for w in result.warnings)


def test_run_archetype_extraction_unknown_champion_warns(session: Session, monkeypatch) -> None:
    _patch_thresholds(monkeypatch, min_builds_per_champion_role=2, min_cluster_weight=1.0)
    patch = _seed_common(session)
    session.commit()

    result = run_archetype_extraction(session, patch="14.14.1", champion_name="NotAChampion")

    assert result.champions_processed == 0
    assert any("NotAChampion" in w for w in result.warnings)
