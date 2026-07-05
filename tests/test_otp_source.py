import pytest
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import OtpSettings
from db.models import Item, ItemTag, OtpBuild, OtpPlayer, Patch
from ingestion.otp import source as otp_source
from ingestion.otp.source import (
    OtpSource,
    _lane_opponent,
    _qualify_one_trick,
)
from ingestion.riot_api import client

AHRI = 103
ZED = 238
RABADONS = 3089
LONG_SWORD = 1036
HEALTH_POTION = 2003
INFINITY_EDGE = 3031


def _mastery(champion_id: int, points: int) -> dict:
    return {"championId": champion_id, "championPoints": points, "championLevel": 7}


def _participant(
    champion_id: int,
    team_position: str,
    win: bool,
    puuid: str,
    items: list[int] | None = None,
    primary_runes: list[int] | None = None,
    sub_runes: list[int] | None = None,
) -> dict:
    items = items or []
    participant = {
        "championId": champion_id,
        "teamPosition": team_position,
        "win": win,
        "puuid": puuid,
    }
    for slot in range(7):  # item0..item6 (item6 is the trinket slot)
        participant[f"item{slot}"] = items[slot] if slot < len(items) else 0
    participant["perks"] = {
        "styles": [
            {"selections": [{"perk": rune_id} for rune_id in (primary_runes or [])]},
            {"selections": [{"perk": rune_id} for rune_id in (sub_runes or [])]},
        ]
    }
    return participant


def _match(match_id: str, game_version: str, participants: list[dict]) -> dict:
    return {
        "metadata": {"matchId": match_id},
        "info": {"gameVersion": game_version, "participants": participants},
    }


def _timeline(match_id: str, events: list[dict]) -> dict:
    return {"metadata": {"matchId": match_id}, "info": {"frames": [{"events": events}]}}


def _purchase(participant_id: int, item_id: int, timestamp: int) -> dict:
    return {
        "type": "ITEM_PURCHASED",
        "timestamp": timestamp,
        "participantId": participant_id,
        "itemId": item_id,
    }


def _skill_level_up(participant_id: int, skill_slot: int, timestamp: int) -> dict:
    return {
        "type": "SKILL_LEVEL_UP",
        "timestamp": timestamp,
        "participantId": participant_id,
        "skillSlot": skill_slot,
        "levelUpType": "NORMAL",
    }


def test_resolve_patch_requires_explicit_patch() -> None:
    with pytest.raises(ValueError):
        OtpSource().resolve_patch(None)


def test_qualify_one_trick_requires_both_points_and_concentration_thresholds() -> None:
    # Concentration is 1.0 (only one champion played) but points fall short.
    assert _qualify_one_trick("p1", [_mastery(AHRI, 100_000)]) is None

    # The top champion's points alone clear the threshold, but two other
    # champions have comparable points, so concentration falls short.
    YONE = 777
    assert (
        _qualify_one_trick(
            "p2", [_mastery(AHRI, 220_000), _mastery(ZED, 200_000), _mastery(YONE, 200_000)]
        )
        is None
    )

    # Both clear.
    candidate = _qualify_one_trick("p3", [_mastery(AHRI, 300_000), _mastery(ZED, 50_000)])
    assert candidate is not None
    assert candidate["puuid"] == "p3"
    assert candidate["champion_id"] == AHRI
    assert candidate["mastery_points"] == 300_000
    assert candidate["mastery_concentration"] == 300_000 / 350_000


def test_qualify_one_trick_handles_empty_masteries_list() -> None:
    assert _qualify_one_trick("p1", []) is None


def test_lane_opponent_returns_none_when_no_opponent_found() -> None:
    participant = _participant(AHRI, "MIDDLE", True, puuid="otp-1")
    assert _lane_opponent([participant], participant) is None


def test_fetch_qualifies_one_tricks_and_collects_matches_and_timelines(monkeypatch) -> None:
    monkeypatch.setattr(
        client, "fetch_league_entries", lambda: [{"puuid": "otp-1"}, {"puuid": "otp-2"}]
    )
    monkeypatch.setattr(
        client,
        "fetch_champion_masteries",
        lambda puuid: {
            "otp-1": [_mastery(AHRI, 300_000), _mastery(ZED, 50_000)],  # qualifies
            "otp-2": [_mastery(ZED, 100_000)],  # fails the points threshold
        }[puuid],
    )
    monkeypatch.setattr(client, "fetch_match_ids", lambda puuid, **kwargs: ["NA1_1", "NA1_2"])
    monkeypatch.setattr(
        client,
        "fetch_match",
        lambda match_id: _match(
            match_id, "14.14.1", [_participant(AHRI, "MIDDLE", True, puuid="otp-1")]
        ),
    )
    monkeypatch.setattr(client, "fetch_match_timeline", lambda match_id: _timeline(match_id, []))

    source = OtpSource()
    source.warnings = []
    data = source.fetch("14.14")

    assert len(data["one_tricks"]) == 1
    one_trick = data["one_tricks"][0]
    assert one_trick["puuid"] == "otp-1"
    assert one_trick["champion_id"] == AHRI
    assert {m["metadata"]["matchId"] for m in one_trick["matches"]} == {"NA1_1", "NA1_2"}
    assert {t["metadata"]["matchId"] for t in one_trick["timelines"]} == {"NA1_1", "NA1_2"}


def test_fetch_skips_timeline_when_match_fetch_fails(monkeypatch) -> None:
    monkeypatch.setattr(client, "fetch_league_entries", lambda: [{"puuid": "otp-1"}])
    monkeypatch.setattr(client, "fetch_champion_masteries", lambda puuid: [_mastery(AHRI, 300_000)])
    monkeypatch.setattr(client, "fetch_match_ids", lambda puuid, **kwargs: ["NA1_1", "NA1_BAD"])

    def _fetch_match(match_id: str) -> dict:
        if match_id == "NA1_BAD":
            raise requests.exceptions.RequestException("boom")
        return _match(match_id, "14.14.1", [_participant(AHRI, "MIDDLE", True, puuid="otp-1")])

    monkeypatch.setattr(client, "fetch_match", _fetch_match)
    timeline_calls: list[str] = []
    monkeypatch.setattr(
        client,
        "fetch_match_timeline",
        lambda match_id: timeline_calls.append(match_id) or _timeline(match_id, []),
    )

    source = OtpSource()
    source.warnings = []
    data = source.fetch("14.14")

    assert timeline_calls == ["NA1_1"]  # NA1_BAD's match fetch failed, so no timeline attempt
    assert data["one_tricks"][0]["matches"] == [
        _match("NA1_1", "14.14.1", [_participant(AHRI, "MIDDLE", True, puuid="otp-1")])
    ]
    assert any("Match fetch failed" in w for w in source.warnings)


def test_fetch_caps_at_max_one_tricks_per_run(monkeypatch) -> None:
    monkeypatch.setattr(otp_source, "OTP", OtpSettings(max_one_tricks_per_run=1))
    monkeypatch.setattr(
        client, "fetch_league_entries", lambda: [{"puuid": "otp-1"}, {"puuid": "otp-2"}]
    )
    monkeypatch.setattr(
        client, "fetch_champion_masteries", lambda puuid: [_mastery(AHRI, 300_000)]  # both qualify
    )
    monkeypatch.setattr(client, "fetch_match_ids", lambda puuid, **kwargs: [])

    source = OtpSource()
    source.warnings = []
    data = source.fetch("14.14")

    assert len(data["one_tricks"]) == 1


def test_load_extracts_full_build_details(session: Session) -> None:
    session.add(Patch(version="14.14.1"))
    session.add_all(
        [
            Item(
                item_id=RABADONS,
                name="Rabadon's Deathcap",
                description="",
                plaintext="",
                gold_base=1600,
                gold_total=3500,
                gold_sell=2450,
                raw_data={},
            ),
            Item(
                item_id=INFINITY_EDGE,
                name="Infinity Edge",
                description="",
                plaintext="",
                gold_base=825,
                gold_total=3500,
                gold_sell=2450,
                raw_data={},
            ),
            Item(
                item_id=LONG_SWORD,
                name="Long Sword",
                description="",
                plaintext="",
                gold_base=350,
                gold_total=350,
                gold_sell=140,
                raw_data={},
                builds_into=[str(RABADONS)],  # non-empty - a component, not terminal
            ),
            Item(
                item_id=HEALTH_POTION,
                name="Health Potion",
                description="",
                plaintext="",
                gold_base=50,
                gold_total=50,
                gold_sell=0,
                raw_data={},
            ),
            ItemTag(item_id=HEALTH_POTION, tag="Consumable"),
        ]
    )
    session.commit()

    participant = _participant(
        AHRI,
        "MIDDLE",
        True,
        puuid="otp-1",
        items=[RABADONS, INFINITY_EDGE, 0, 0, 0, 0, 3364],
        primary_runes=[8112, 8126, 8143],
        sub_runes=[8009],
    )
    enemy = _participant(ZED, "MIDDLE", False, puuid="enemy-1")
    match_data = _match("NA1_1", "14.14.567890", [participant, enemy])

    events = [
        _purchase(1, HEALTH_POTION, 20_000),  # starting item (consumable)
        _purchase(1, LONG_SWORD, 30_000),  # starting item (component)
        _purchase(1, RABADONS, 200_000),  # past the starting-item cutoff; a completed item
        _purchase(1, INFINITY_EDGE, 400_000),
        _skill_level_up(1, 1, 100),
        _skill_level_up(1, 2, 200),
    ]
    timeline_data = _timeline("NA1_1", events)

    data = {
        "one_tricks": [
            {
                "puuid": "otp-1",
                "champion_id": AHRI,
                "mastery_points": 300_000,
                "mastery_concentration": 0.9,
                "matches": [match_data],
                "timelines": [timeline_data],
            }
        ]
    }

    source = OtpSource()
    source.warnings = []
    counts = source.load(session, "14.14.1", data)

    assert counts == {"otp_players": 1, "otp_builds": 1}

    player = session.execute(select(OtpPlayer)).scalar_one()
    assert player.puuid == "otp-1"
    assert player.primary_champion_id == AHRI
    assert player.role == "MIDDLE"
    assert player.games_sampled == 1
    assert player.sample_size == 1
    assert player.win_rate == 1.0
    assert player.mastery_points == 300_000

    build = session.execute(select(OtpBuild)).scalar_one()
    assert build.match_id == "NA1_1"
    assert build.win is True
    assert build.role == "MIDDLE"
    assert build.starting_items == [HEALTH_POTION, LONG_SWORD]
    assert build.completed_items == [RABADONS, INFINITY_EDGE]
    assert set(build.final_items) == {RABADONS, INFINITY_EDGE}
    assert build.primary_runes == [8112, 8126, 8143]
    assert build.secondary_runes == [8009]
    assert build.skill_order == [[1, "NORMAL"], [2, "NORMAL"]]
    assert build.enemy_champion_id == ZED


def test_load_is_idempotent(session: Session) -> None:
    session.add(Patch(version="14.14.1"))
    session.commit()

    participant = _participant(AHRI, "MIDDLE", True, puuid="otp-1")
    enemy = _participant(ZED, "MIDDLE", False, puuid="enemy-1")
    match_data = _match("NA1_1", "14.14.567890", [participant, enemy])
    timeline_data = _timeline("NA1_1", [])

    data = {
        "one_tricks": [
            {
                "puuid": "otp-1",
                "champion_id": AHRI,
                "mastery_points": 300_000,
                "mastery_concentration": 0.9,
                "matches": [match_data],
                "timelines": [timeline_data],
            }
        ]
    }

    source = OtpSource()
    source.warnings = []
    counts_first = source.load(session, "14.14.1", data)
    session.commit()
    counts_second = source.load(session, "14.14.1", data)
    session.commit()

    assert counts_first == {"otp_players": 1, "otp_builds": 1}
    assert counts_second == {"otp_players": 0, "otp_builds": 0}
    assert len(session.execute(select(OtpPlayer)).scalars().all()) == 1
    assert len(session.execute(select(OtpBuild)).scalars().all()) == 1

    player = session.execute(select(OtpPlayer)).scalar_one()
    assert player.games_sampled == 1  # not doubled by the second run
    assert player.win_rate == 1.0


def test_load_skips_off_patch_and_wrong_champion_matches(session: Session) -> None:
    session.add(Patch(version="14.14.1"))
    session.commit()

    off_patch = _match(
        "NA1_OFF",
        "14.13.999999",
        [_participant(AHRI, "MIDDLE", True, puuid="otp-1"), _participant(ZED, "MIDDLE", False, puuid="enemy-1")],
    )
    wrong_champion = _match(
        "NA1_WRONG",
        "14.14.567890",
        [_participant(ZED, "MIDDLE", True, puuid="otp-1"), _participant(AHRI, "MIDDLE", False, puuid="enemy-1")],
    )

    data = {
        "one_tricks": [
            {
                "puuid": "otp-1",
                "champion_id": AHRI,
                "mastery_points": 300_000,
                "mastery_concentration": 0.9,
                "matches": [off_patch, wrong_champion],
                "timelines": [_timeline("NA1_OFF", []), _timeline("NA1_WRONG", [])],
            }
        ]
    }

    source = OtpSource()
    source.warnings = []
    counts = source.load(session, "14.14.1", data)

    assert counts == {"otp_players": 1, "otp_builds": 0}
    assert session.execute(select(OtpBuild)).scalars().all() == []
    assert any("off-patch" in w for w in source.warnings)


def test_load_without_ingested_patch_warns_and_stores_null_patch_id(session: Session) -> None:
    participant = _participant(AHRI, "MIDDLE", True, puuid="otp-1")
    enemy = _participant(ZED, "MIDDLE", False, puuid="enemy-1")
    match_data = _match("NA1_1", "14.14.567890", [participant, enemy])
    timeline_data = _timeline("NA1_1", [])

    data = {
        "one_tricks": [
            {
                "puuid": "otp-1",
                "champion_id": AHRI,
                "mastery_points": 300_000,
                "mastery_concentration": 0.9,
                "matches": [match_data],
                "timelines": [timeline_data],
            }
        ]
    }

    source = OtpSource()
    source.warnings = []
    source.load(session, "14.14.1", data)

    build = session.execute(select(OtpBuild)).scalar_one()
    assert build.patch_id is None
    player = session.execute(select(OtpPlayer)).scalar_one()
    assert player.patch_id is None
    assert any("not found in DB" in w for w in source.warnings)


def test_player_aggregate_is_recomputed_over_full_lifetime_history(session: Session) -> None:
    session.add(Patch(version="14.14.1"))
    session.commit()

    player = OtpPlayer(
        puuid="otp-1",
        primary_champion_id=AHRI,
        role="MIDDLE",
        patch_id=None,
        mastery_points=250_000,
        mastery_concentration=0.8,
        games_sampled=1,
        win_rate=0.0,
        sample_size=1,
    )
    session.add(player)
    session.flush()
    session.add(
        OtpBuild(
            otp_player_id=player.id,
            patch_id=None,
            match_id="NA1_OLD",
            win=False,
            role="MIDDLE",
            starting_items=[],
            completed_items=[],
            final_items=[],
            primary_runes=[],
            secondary_runes=[],
            skill_order=[],
            enemy_champion_id=None,
            raw_data={},
        )
    )
    session.commit()

    participant = _participant(AHRI, "MIDDLE", True, puuid="otp-1")
    enemy = _participant(ZED, "MIDDLE", False, puuid="enemy-1")
    match_data = _match("NA1_NEW", "14.14.567890", [participant, enemy])
    timeline_data = _timeline("NA1_NEW", [])

    data = {
        "one_tricks": [
            {
                "puuid": "otp-1",
                "champion_id": AHRI,
                "mastery_points": 300_000,
                "mastery_concentration": 0.9,
                "matches": [match_data],
                "timelines": [timeline_data],
            }
        ]
    }

    source = OtpSource()
    source.warnings = []
    source.load(session, "14.14.1", data)

    refreshed = session.execute(select(OtpPlayer)).scalar_one()
    assert refreshed.games_sampled == 2  # the pre-existing build plus this run's new one
    assert refreshed.win_rate == 0.5  # 1 win out of 2 total, not just this run's 1/1
    assert refreshed.mastery_points == 300_000  # refreshed to this run's value
