import dataclasses

import knowledge.archetypes.deltas as deltas_module
from config.settings import ARCHETYPES
from knowledge.archetypes.deltas import rating_deltas


def test_rating_deltas_direct_tag_mapping() -> None:
    deltas = rating_deltas({"engage": 1.0})
    assert deltas["engage"] == ARCHETYPES.archetype_delta_scale
    assert deltas["disengage"] == 0.0


def test_rating_deltas_frontline_maps_from_tank_tag() -> None:
    deltas = rating_deltas({"tank": 0.5})
    assert deltas["frontline"] == ARCHETYPES.archetype_delta_scale * 0.5


def test_rating_deltas_scaling_curve_is_negative_for_early_game() -> None:
    deltas = rating_deltas({"early_game": 1.0})
    assert deltas["scaling_curve"] == -ARCHETYPES.archetype_delta_scale


def test_rating_deltas_missing_tag_defaults_to_zero() -> None:
    deltas = rating_deltas({})
    assert all(value == 0.0 for value in deltas.values())


def test_rating_deltas_clamped_to_archetype_delta_max(monkeypatch) -> None:
    patched = dataclasses.replace(ARCHETYPES, archetype_delta_scale=100.0)
    monkeypatch.setattr(deltas_module, "ARCHETYPES", patched)

    deltas = deltas_module.rating_deltas({"engage": 1.0})

    assert deltas["engage"] == patched.archetype_delta_max
