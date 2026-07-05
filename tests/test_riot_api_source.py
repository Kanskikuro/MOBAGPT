from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Match, MatchParticipant, MatchupStatistics, Patch
from ingestion.riot_api import client
from ingestion.riot_api.source import RiotApiSource

AHRI = 103
ZED = 238
YONE = 777
AKALI = 84


def _participant(champion_id: int, team_position: str, win: bool) -> dict:
    return {
        "championId": champion_id,
        "teamPosition": team_position,
        "win": win,
        "kills": 5,
        "deaths": 2,
        "assists": 7,
        "puuid": "p",
    }


def _match(
    match_id: str,
    game_version: str,
    ally_champion: int,
    enemy_champion: int,
    ally_wins: bool,
    ally_bans: list[dict] | None = None,
    enemy_bans: list[dict] | None = None,
) -> dict:
    return {
        "metadata": {"matchId": match_id},
        "info": {
            "gameCreation": 1720000000000,
            "gameDuration": 1800,
            "gameVersion": game_version,
            "queueId": 420,
            "teams": [
                {"teamId": 100, "win": ally_wins, "bans": ally_bans or []},
                {"teamId": 200, "win": not ally_wins, "bans": enemy_bans or []},
            ],
            "participants": [
                _participant(ally_champion, "MIDDLE", ally_wins),
                _participant(enemy_champion, "MIDDLE", not ally_wins),
            ],
        },
    }


def test_fetch_dedups_puuids_and_match_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        client,
        "fetch_league_entries",
        lambda: [{"puuid": "puuid-1"}, {"summonerId": "sid-2"}],
    )
    monkeypatch.setattr(
        client, "fetch_summoner", lambda sid: {"puuid": "puuid-2"} if sid == "sid-2" else {}
    )
    monkeypatch.setattr(
        client,
        "fetch_match_ids",
        lambda puuid, **kwargs: {
            "puuid-1": ["NA1_1", "NA1_2"],
            "puuid-2": ["NA1_2", "NA1_3"],
        }[puuid],
    )
    monkeypatch.setattr(
        client, "fetch_match", lambda match_id: {"metadata": {"matchId": match_id}, "info": {}}
    )

    data = RiotApiSource().fetch("14.14.1")

    fetched_ids = {m["metadata"]["matchId"] for m in data["matches"]}
    assert fetched_ids == {"NA1_1", "NA1_2", "NA1_3"}  # deduped across both summoners


def test_load_is_idempotent_and_computes_matchup_statistics(session: Session) -> None:
    session.add(Patch(version="14.14.1"))
    session.commit()

    payload = {
        "matches": [
            _match(
                "NA1_1",
                "14.14.567890",
                AHRI,
                ZED,
                True,
                ally_bans=[{"championId": YONE}],
                enemy_bans=[{"championId": AKALI}],
            ),
            _match("NA1_2", "14.14.567891", ZED, AHRI, False, ally_bans=[{"championId": YONE}]),
        ]
    }

    source = RiotApiSource()
    source.warnings = []
    counts_first = source.load(session, "14.14.1", payload)
    session.commit()
    counts_second = source.load(session, "14.14.1", payload)
    session.commit()

    assert counts_first == {"matches": 2, "match_participants": 4, "matchup_statistics": 2}
    # Re-running with the same fetched matches must not create duplicates -
    # matchup_statistics is still recomputed (a full recompute), but no new
    # match/participant rows are added.
    assert counts_second == {"matches": 0, "match_participants": 0, "matchup_statistics": 2}

    assert len(session.execute(select(Match)).scalars().all()) == 2
    assert len(session.execute(select(MatchParticipant)).scalars().all()) == 4

    stats = {
        (s.champion_id, s.role): s for s in session.execute(select(MatchupStatistics)).scalars().all()
    }

    ahri = stats[(AHRI, "MIDDLE")]
    assert ahri.games == 2
    assert ahri.picks == 2
    assert ahri.wins == 2
    assert ahri.win_rate == 1.0
    assert ahri.pick_rate == 0.5  # 2 picks / (2 games * 2 role-slots)

    zed = stats[(ZED, "MIDDLE")]
    assert zed.picks == 2
    assert zed.wins == 0
    assert zed.win_rate == 0.0

    # Yone was banned in both matches but never picked - no per-role row
    # exists to attach a ban count to in this first slice (bans have no
    # role attribution in Riot's data; see source.py's docstring).
    assert (YONE, "MIDDLE") not in stats


def test_load_skips_off_patch_matches(session: Session) -> None:
    session.add(Patch(version="14.14.1"))
    session.commit()

    payload = {"matches": [_match("NA1_9", "14.13.999999", AHRI, ZED, True)]}

    source = RiotApiSource()
    source.warnings = []
    counts = source.load(session, "14.14.1", payload)

    assert counts == {"matches": 0, "match_participants": 0, "matchup_statistics": 0}
    assert session.execute(select(Match)).scalars().all() == []
    assert any("off-patch" in warning for warning in source.warnings)


def test_load_without_ingested_patch_warns_and_skips_statistics(session: Session) -> None:
    payload = {"matches": [_match("NA1_1", "14.14.567890", AHRI, ZED, True)]}

    source = RiotApiSource()
    source.warnings = []
    counts = source.load(session, "14.14.1", payload)

    assert counts["matchup_statistics"] == 0
    assert any("not found in DB" in warning for warning in source.warnings)
    match = session.execute(select(Match).where(Match.match_id == "NA1_1")).scalar_one()
    assert match.patch_id is None
