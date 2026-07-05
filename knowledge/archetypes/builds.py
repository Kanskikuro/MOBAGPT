"""Gathers per-game observed builds for build-archetype clustering
(docs/sepc.md Component 1), from the two sources the spec calls for
("cluster observed builds from statistical + OTP data"):

- Challenger-aggregate: match_participants for the resolved patch, parsed
  via ingestion.riot_api.participants (final items + rune selections).
  ItemStatistics/RuneStatistics only store item-marginal/rune-marginal
  aggregates, not each game's joint item+rune set, so this reconstructs it
  fresh from the same raw_data those aggregates are computed from.
- OTP: otp_builds, already-parsed lists - weighted by the sampled player's
  sample size and win-rate consistency (docs/sepc.md's "Role of OTP data":
  OTP is weighted, never overrides statistics blindly), vs. weight 1.0 for
  every aggregate build.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.settings import ARCHETYPES
from db.models import Match, MatchParticipant, OtpBuild, OtpPlayer
from ingestion.riot_api.participants import final_items, rune_selections


@dataclass
class ObservedBuild:
    item_ids: list[int]
    primary_runes: list[int]
    secondary_runes: list[int]
    win: bool
    weight: float
    source: str  # "aggregate" or "otp"


def gather_observed_builds(
    session: Session, champion_id: int, role: str, patch_id: int
) -> list[ObservedBuild]:
    builds = _aggregate_builds(session, champion_id, role, patch_id)
    builds.extend(_otp_builds(session, champion_id, role, patch_id))
    return builds


def _aggregate_builds(
    session: Session, champion_id: int, role: str, patch_id: int
) -> list[ObservedBuild]:
    participants = (
        session.execute(
            select(MatchParticipant)
            .join(Match, Match.match_id == MatchParticipant.match_id)
            .where(
                Match.patch_id == patch_id,
                MatchParticipant.champion_id == champion_id,
                MatchParticipant.team_position == role,
            )
        )
        .scalars()
        .all()
    )

    return [
        ObservedBuild(
            item_ids=final_items(participant.raw_data),
            primary_runes=rune_selections(participant.raw_data, style_index=0),
            secondary_runes=rune_selections(participant.raw_data, style_index=1),
            win=participant.win,
            weight=1.0,
            source="aggregate",
        )
        for participant in participants
    ]


def _otp_builds(
    session: Session, champion_id: int, role: str, patch_id: int
) -> list[ObservedBuild]:
    rows = session.execute(
        select(OtpBuild, OtpPlayer)
        .join(OtpPlayer, OtpPlayer.id == OtpBuild.otp_player_id)
        .where(
            OtpPlayer.primary_champion_id == champion_id,
            OtpBuild.role == role,
            OtpBuild.patch_id == patch_id,
        )
    ).all()

    builds = []
    for build, player in rows:
        weight = min(1.0, player.games_sampled / ARCHETYPES.otp_weight_normalizer) * (
            0.5 + 0.5 * player.win_rate
        )
        builds.append(
            ObservedBuild(
                item_ids=list(build.final_items),
                primary_runes=list(build.primary_runes),
                secondary_runes=list(build.secondary_runes),
                win=build.win,
                weight=weight,
                source="otp",
            )
        )
    return builds
