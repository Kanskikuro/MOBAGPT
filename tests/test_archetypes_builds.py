import datetime

from sqlalchemy.orm import Session

from db.models import Champion, Match, MatchParticipant, OtpBuild, OtpPlayer, Patch
from knowledge.archetypes.builds import gather_observed_builds


def _seed_patch(session: Session) -> Patch:
    patch = Patch(version="14.14.1")
    session.add(patch)
    session.flush()
    return patch


def _seed_champion(session: Session, champion_id: int = 103) -> Champion:
    champion = Champion(
        champion_id=champion_id, riot_key="Ahri", display_name="Ahri",
        normalized_name="ahri", title="the Nine-Tailed Fox",
    )
    session.add(champion)
    session.flush()
    return champion


def _participant_raw(item0: int = 3153) -> dict:
    return {
        "item0": item0, "item1": 0, "item2": 0, "item3": 0, "item4": 0, "item5": 0,
        "perks": {"styles": [{"selections": [{"perk": 8112}]}, {"selections": [{"perk": 8139}]}]},
    }


def test_gather_observed_builds_reads_aggregate_match_participants(session: Session) -> None:
    patch = _seed_patch(session)
    champion = _seed_champion(session)

    session.add(
        Match(
            match_id="NA1_1", patch_id=patch.id, queue_id=420, game_duration=1800,
            game_creation=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC),
            region="americas", raw_data={},
        )
    )
    session.add(
        MatchParticipant(
            match_id="NA1_1", champion_id=champion.champion_id, team_position="MIDDLE",
            win=True, kills=5, deaths=1, assists=7, raw_data=_participant_raw(),
        )
    )
    session.commit()

    builds = gather_observed_builds(session, champion.champion_id, "MIDDLE", patch.id)

    assert len(builds) == 1
    assert builds[0].source == "aggregate"
    assert builds[0].weight == 1.0
    assert builds[0].item_ids == [3153]
    assert builds[0].primary_runes == [8112]
    assert builds[0].secondary_runes == [8139]
    assert builds[0].win is True


def test_gather_observed_builds_includes_otp_with_weight_formula(session: Session) -> None:
    patch = _seed_patch(session)
    champion = _seed_champion(session)

    player = OtpPlayer(
        puuid="puuid-1", primary_champion_id=champion.champion_id, role="MIDDLE",
        patch_id=patch.id, mastery_points=300_000, mastery_concentration=0.9,
        games_sampled=5, win_rate=0.6, sample_size=5,
    )
    session.add(player)
    session.flush()

    session.add(
        OtpBuild(
            otp_player_id=player.id, patch_id=patch.id, match_id="NA1_2", win=True,
            role="MIDDLE", starting_items=[], completed_items=[],
            final_items=[3157, 3020], primary_runes=[8112], secondary_runes=[8210],
            skill_order=[], raw_data={},
        )
    )
    session.commit()

    builds = gather_observed_builds(session, champion.champion_id, "MIDDLE", patch.id)

    assert len(builds) == 1
    build = builds[0]
    assert build.source == "otp"
    assert build.item_ids == [3157, 3020]
    # weight = min(1.0, 5/10) * (0.5 + 0.5*0.6) = 0.5 * 0.8 = 0.4
    assert build.weight == 0.4


def test_gather_observed_builds_excludes_other_role_and_patch(session: Session) -> None:
    patch = _seed_patch(session)
    other_patch = Patch(version="14.13.1")
    session.add(other_patch)
    session.flush()
    champion = _seed_champion(session)

    session.add(
        Match(
            match_id="NA1_3", patch_id=other_patch.id, queue_id=420, game_duration=1800,
            game_creation=datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC),
            region="americas", raw_data={},
        )
    )
    session.add(
        MatchParticipant(
            match_id="NA1_3", champion_id=champion.champion_id, team_position="MIDDLE",
            win=True, kills=1, deaths=1, assists=1, raw_data=_participant_raw(),
        )
    )
    session.add(
        Match(
            match_id="NA1_4", patch_id=patch.id, queue_id=420, game_duration=1800,
            game_creation=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC),
            region="americas", raw_data={},
        )
    )
    session.add(
        MatchParticipant(
            match_id="NA1_4", champion_id=champion.champion_id, team_position="TOP",
            win=True, kills=1, deaths=1, assists=1, raw_data=_participant_raw(),
        )
    )
    session.commit()

    builds = gather_observed_builds(session, champion.champion_id, "MIDDLE", patch.id)

    assert builds == []
