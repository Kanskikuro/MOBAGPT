import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Champion, ChampionRating, ChampionTag, Item, ItemTag, Rune, RuneTag
from knowledge import client, run
from knowledge.client import ChampionExtraction, TagExtraction
from knowledge.overrides import EntityOverride, Overrides

_ALL_RATINGS = {
    "engage": 3, "disengage": 2, "frontline": 1, "peel": 1, "wave_clear": 5,
    "burst": 9, "sustained_dps": 4, "mobility": 8, "cc_score": 2, "scaling_curve": 5,
}


def _seed(session: Session) -> tuple[Champion, Item, Rune]:
    champion = Champion(
        champion_id=103, riot_key="Ahri", display_name="Ahri",
        normalized_name="ahri", title="the Nine-Tailed Fox",
    )
    item = Item(
        item_id=3124, name="Guinsoo's Rageblade", description="On-hit stacking item.",
        plaintext="Stack on-hit effects", gold_base=800, gold_total=3200,
        gold_sell=2240, raw_data={},
    )
    rune = Rune(
        rune_id=8112, path_name="Domination", slot=0, name="Electrocute",
        short_desc="Burst over 3 hits.", long_desc="Hit an enemy champion 3 times.",
        raw_data={},
    )
    session.add_all([champion, item, rune])
    session.commit()
    return champion, item, rune


def _patch_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client,
        "extract_champion_profile",
        lambda name, text: ChampionExtraction(tags=["burst", "mobility"], ratings=_ALL_RATINGS),
    )
    monkeypatch.setattr(
        client,
        "extract_tags",
        lambda kind, name, text: TagExtraction(tags=["sustained_dps"]),
    )


def test_run_pipeline_tags_all_entities(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    champion, item, rune = _seed(session)
    _patch_extraction(monkeypatch)

    result = run.run_pipeline(session, only=None, force=False)

    assert result.counts == {"champions": 1, "items": 1, "runes": 1}

    champ_tags = session.execute(
        select(ChampionTag).where(ChampionTag.champion_id == champion.champion_id)
    ).scalars().all()
    assert sorted(t.tag for t in champ_tags) == ["burst", "mobility"]

    champ_ratings = session.execute(
        select(ChampionRating).where(ChampionRating.champion_id == champion.champion_id)
    ).scalars().all()
    assert {r.rating_name: r.value for r in champ_ratings} == _ALL_RATINGS

    item_tags = session.execute(
        select(ItemTag).where(ItemTag.item_id == item.item_id, ItemTag.source == "llm")
    ).scalars().all()
    assert [t.tag for t in item_tags] == ["sustained_dps"]

    rune_tags = session.execute(
        select(RuneTag).where(RuneTag.rune_id == rune.rune_id, RuneTag.source == "llm")
    ).scalars().all()
    assert [t.tag for t in rune_tags] == ["sustained_dps"]


def test_run_pipeline_skips_already_tagged_unless_forced(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    champion, _, _ = _seed(session)
    session.add(ChampionTag(champion_id=champion.champion_id, tag="engage", source="llm"))
    session.commit()

    calls = []
    monkeypatch.setattr(
        client,
        "extract_champion_profile",
        lambda name, text: calls.append(name) or ChampionExtraction(tags=["burst"], ratings=_ALL_RATINGS),
    )
    monkeypatch.setattr(client, "extract_tags", lambda kind, name, text: TagExtraction(tags=[]))

    result = run.run_pipeline(session, only=["champions"], force=False)
    assert result.counts["champions"] == 0
    assert calls == []

    result = run.run_pipeline(session, only=["champions"], force=True)
    assert result.counts["champions"] == 1
    assert calls == ["Ahri"]


def test_run_pipeline_only_filters_entity_kinds(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(session)
    _patch_extraction(monkeypatch)

    result = run.run_pipeline(session, only=["champions"], force=False)

    assert result.counts == {"champions": 1}
    assert session.execute(select(ItemTag)).first() is None
    assert session.execute(select(RuneTag)).first() is None


def test_run_pipeline_applies_override_after_llm_and_replaces_rating(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    champion, _, _ = _seed(session)
    _patch_extraction(monkeypatch)
    monkeypatch.setattr(
        "knowledge.run.load_overrides",
        lambda: Overrides(
            champions={"Ahri": EntityOverride(tags=["tank"], ratings={"engage": 9})}
        ),
    )

    run.run_pipeline(session, only=["champions"], force=False)

    tags = session.execute(
        select(ChampionTag).where(ChampionTag.champion_id == champion.champion_id)
    ).scalars().all()
    by_source = {(t.tag, t.source) for t in tags}
    assert ("tank", "override") in by_source
    assert ("burst", "llm") in by_source  # llm rows untouched, override coexists

    ratings = session.execute(
        select(ChampionRating).where(
            ChampionRating.champion_id == champion.champion_id,
            ChampionRating.rating_name == "engage",
        )
    ).scalars().all()
    assert len(ratings) == 1
    assert ratings[0].value == 9
    assert ratings[0].source == "override"


def test_run_pipeline_warns_when_override_targets_unknown_entity(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(session)
    _patch_extraction(monkeypatch)
    monkeypatch.setattr(
        "knowledge.run.load_overrides",
        lambda: Overrides(champions={"NotARealChampion": EntityOverride(tags=["tank"])}),
    )

    result = run.run_pipeline(session, only=["champions"], force=False)

    assert any("NotARealChampion" in w and "not found" in w for w in result.warnings)
