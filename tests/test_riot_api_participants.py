from ingestion.riot_api.participants import final_items, rune_selections


def _participant(**overrides) -> dict:
    base = {
        "item0": 3153, "item1": 3006, "item2": 0, "item3": 0, "item4": 0, "item5": 0, "item6": 3364,
        "perks": {
            "styles": [
                {"selections": [{"perk": 8112}, {"perk": 8126}]},
                {"selections": [{"perk": 8139}]},
            ]
        },
    }
    base.update(overrides)
    return base


def test_final_items_excludes_zero_slots_and_trinket() -> None:
    assert final_items(_participant()) == [3153, 3006]


def test_final_items_handles_full_inventory() -> None:
    participant = _participant(item2=3031, item3=3072, item4=3046, item5=3026)
    assert final_items(participant) == [3153, 3006, 3031, 3072, 3046, 3026]


def test_rune_selections_primary_and_secondary() -> None:
    participant = _participant()
    assert rune_selections(participant, style_index=0) == [8112, 8126]
    assert rune_selections(participant, style_index=1) == [8139]


def test_rune_selections_missing_style_returns_empty() -> None:
    participant = _participant()
    assert rune_selections(participant, style_index=5) == []


def test_rune_selections_missing_perks_key_returns_empty() -> None:
    assert rune_selections({}, style_index=0) == []
