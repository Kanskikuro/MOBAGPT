from sqlalchemy.orm import Session

from db.models import Item
from knowledge.archetypes.naming import damage_label, dedupe_name, name_archetype


def _make_item(session: Session, item_id: int, stats: dict) -> Item:
    item = Item(
        item_id=item_id, name=f"Item{item_id}", description="", plaintext="",
        gold_base=0, gold_total=0, gold_sell=0, stats=stats, raw_data={},
    )
    session.add(item)
    session.flush()
    return item


def test_damage_label_ad_dominant(session: Session) -> None:
    _make_item(session, 1, {"FlatPhysicalDamageMod": 80})
    assert damage_label(session, [1]) == "AD"


def test_damage_label_ap_dominant(session: Session) -> None:
    _make_item(session, 1, {"FlatMagicDamageMod": 120})
    assert damage_label(session, [1]) == "AP"


def test_damage_label_tied_or_neither_is_none(session: Session) -> None:
    _make_item(session, 1, {"FlatHPPoolMod": 400})
    assert damage_label(session, [1]) is None


def test_damage_label_empty_item_list_is_none(session: Session) -> None:
    assert damage_label(session, []) is None


def test_name_archetype_uses_dominant_eligible_tag_with_damage() -> None:
    fractions = {"tank": 0.1, "burst": 0.8, "engage": 0.2}
    assert name_archetype(fractions, "AD") == "AD Burst"


def test_name_archetype_tank_ignores_damage() -> None:
    fractions = {"tank": 0.9}
    assert name_archetype(fractions, "AD") == "Tank"


def test_name_archetype_falls_back_to_generic_below_threshold() -> None:
    fractions = {"burst": 0.1}
    assert name_archetype(fractions, "AP") == "AP Generalist"


def test_name_archetype_no_damage_omits_prefix() -> None:
    fractions = {"burst": 0.9}
    assert name_archetype(fractions, None) == "Burst"


def test_dedupe_name_no_collision() -> None:
    assert dedupe_name(set(), "AD Burst", "MIDDLE") == "AD Burst"


def test_dedupe_name_appends_role_then_number() -> None:
    assert dedupe_name({"AD Burst"}, "AD Burst", "MIDDLE") == "AD Burst (MIDDLE)"
    assert (
        dedupe_name({"AD Burst", "AD Burst (MIDDLE)"}, "AD Burst", "MIDDLE")
        == "AD Burst (MIDDLE) 2"
    )
