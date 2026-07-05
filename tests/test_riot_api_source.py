from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    ChampionCounters,
    ChampionSynergy,
    Match,
    MatchParticipant,
    MatchupStatistics,
    Patch,
)
from ingestion.riot_api import client
from ingestion.riot_api.source import RiotApiSource

AHRI = 103
ZED = 238
YONE = 777
AKALI = 84
ORNN = 516
LEONA = 89
VI = 254


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


def _team_match(
    match_id: str,
    game_version: str,
    ally: list[tuple[int, str]],
    enemy: list[tuple[int, str]],
    ally_wins: bool,
) -> dict:
    """Like _match, but supports more than one champion per side - needed to
    exercise champion_synergy pairing (which needs 2+ teammates in a match)."""
    return {
        "metadata": {"matchId": match_id},
        "info": {
            "gameCreation": 1720000000000,
            "gameDuration": 1800,
            "gameVersion": game_version,
            "queueId": 420,
            "teams": [
                {"teamId": 100, "win": ally_wins, "bans": []},
                {"teamId": 200, "win": not ally_wins, "bans": []},
            ],
            "participants": (
                [_participant(champion_id, role, ally_wins) for champion_id, role in ally]
                + [_participant(champion_id, role, not ally_wins) for champion_id, role in enemy]
            ),
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

    assert counts_first == {
        "matches": 2,
        "match_participants": 4,
        "matchup_statistics": 2,
        # Each match here has exactly one participant per side, so there are
        # no teammates to pair - champion_synergy stays empty.
        "champion_synergy": 0,
        # AHRI/ZED face each other in both matches (win alternates), giving
        # both directional rows (AHRI->ZED, ZED->AHRI).
        "champion_counters": 2,
    }
    # Re-running with the same fetched matches must not create duplicates -
    # matchup_statistics/champion_synergy/champion_counters are still
    # recomputed (a full recompute), but no new match/participant rows are
    # added.
    assert counts_second == {
        "matches": 0,
        "match_participants": 0,
        "matchup_statistics": 2,
        "champion_synergy": 0,
        "champion_counters": 2,
    }

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

    assert counts == {
        "matches": 0,
        "match_participants": 0,
        "matchup_statistics": 0,
        "champion_synergy": 0,
        "champion_counters": 0,
    }
    assert session.execute(select(Match)).scalars().all() == []
    assert any("off-patch" in warning for warning in source.warnings)


def test_load_without_ingested_patch_warns_and_skips_statistics(session: Session) -> None:
    payload = {"matches": [_match("NA1_1", "14.14.567890", AHRI, ZED, True)]}

    source = RiotApiSource()
    source.warnings = []
    counts = source.load(session, "14.14.1", payload)

    assert counts["matchup_statistics"] == 0
    assert counts["champion_synergy"] == 0
    assert counts["champion_counters"] == 0
    assert any("not found in DB" in warning for warning in source.warnings)
    match = session.execute(select(Match).where(Match.match_id == "NA1_1")).scalar_one()
    assert match.patch_id is None


def test_champion_synergy_pairs_teammates_and_canonicalizes_order(session: Session) -> None:
    session.add(Patch(version="14.14.1"))
    session.commit()

    payload = {
        "matches": [
            _team_match(
                "NA1_1", "14.14.567890", [(ORNN, "TOP"), (LEONA, "UTILITY")], [(VI, "JUNGLE")], True
            ),
            # Same pair, roles, and winning side again but listed in the
            # opposite order - must still collapse into the same row
            # (champion_id_a < champion_id_b), not a second one.
            _team_match(
                "NA1_2", "14.14.567891", [(LEONA, "UTILITY"), (ORNN, "TOP")], [(VI, "JUNGLE")], True
            ),
        ]
    }

    source = RiotApiSource()
    source.warnings = []
    counts = source.load(session, "14.14.1", payload)

    assert counts["champion_synergy"] == 1
    rows = session.execute(select(ChampionSynergy)).scalars().all()
    assert len(rows) == 1

    row = rows[0]
    assert (row.champion_id_a, row.champion_id_b) == (LEONA, ORNN)  # LEONA(89) < ORNN(516)
    assert row.role_a == "UTILITY"
    assert row.role_b == "TOP"
    assert row.games == 2
    assert row.wins == 2
    assert row.win_rate == 1.0


def test_champion_counters_are_directional(session: Session) -> None:
    session.add(Patch(version="14.14.1"))
    session.commit()

    payload = {
        "matches": [
            _team_match("NA1_1", "14.14.567890", [(ORNN, "TOP")], [(VI, "JUNGLE")], True),
        ]
    }

    source = RiotApiSource()
    source.warnings = []
    counts = source.load(session, "14.14.1", payload)

    assert counts["champion_counters"] == 2  # ORNN->VI and VI->ORNN, stored separately
    rows = {
        (r.champion_id, r.role, r.enemy_champion_id, r.enemy_role): r
        for r in session.execute(select(ChampionCounters)).scalars().all()
    }

    ornn_vs_vi = rows[(ORNN, "TOP", VI, "JUNGLE")]
    vi_vs_ornn = rows[(VI, "JUNGLE", ORNN, "TOP")]

    assert ornn_vs_vi.games == vi_vs_ornn.games == 1
    assert ornn_vs_vi.win_rate == 1.0  # ORNN was on the winning side
    assert vi_vs_ornn.win_rate == 0.0
    assert ornn_vs_vi.wins + vi_vs_ornn.wins == ornn_vs_vi.games


def test_no_synergy_across_unrelated_matches(session: Session) -> None:
    """Two separate 1v1 matches (one participant per side) give no teammates
    to pair in either match, so champion_synergy stays empty even though
    champion_counters is populated - pairing never crosses match boundaries."""
    session.add(Patch(version="14.14.1"))
    session.commit()

    payload = {
        "matches": [
            _match("NA1_1", "14.14.567890", AHRI, ZED, True),
            _match("NA1_2", "14.14.567891", ORNN, VI, False),
        ]
    }

    source = RiotApiSource()
    source.warnings = []
    counts = source.load(session, "14.14.1", payload)

    assert counts["champion_synergy"] == 0
    assert counts["champion_counters"] == 4  # 2 directional rows per match, 2 matches


def test_champion_synergy_and_counters_are_idempotent(session: Session) -> None:
    session.add(Patch(version="14.14.1"))
    session.commit()

    payload = {
        "matches": [
            _team_match(
                "NA1_1", "14.14.567890", [(ORNN, "TOP"), (LEONA, "UTILITY")], [(VI, "JUNGLE")], True
            ),
        ]
    }

    source = RiotApiSource()
    source.warnings = []
    source.load(session, "14.14.1", payload)
    session.commit()
    counts = source.load(session, "14.14.1", payload)
    session.commit()

    assert counts["champion_synergy"] == 1
    # 2 allies x 1 enemy, both directions each: ORNN<->VI and LEONA<->VI.
    assert counts["champion_counters"] == 4
    assert len(session.execute(select(ChampionSynergy)).scalars().all()) == 1
    assert len(session.execute(select(ChampionCounters)).scalars().all()) == 4
