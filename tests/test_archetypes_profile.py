from sqlalchemy.orm import Session

from db.models import Item, ItemTag, Rune, RuneTag
from knowledge.archetypes.builds import ObservedBuild
from knowledge.archetypes.profile import tag_fraction_dict, tag_profile_vector


def _make_item(session: Session, item_id: int) -> Item:
    item = Item(
        item_id=item_id, name=f"Item{item_id}", description="", plaintext="",
        gold_base=0, gold_total=0, gold_sell=0, raw_data={},
    )
    session.add(item)
    session.flush()
    return item


def _make_rune(session: Session, rune_id: int) -> Rune:
    rune = Rune(
        rune_id=rune_id, path_name="Domination", slot=0, name=f"Rune{rune_id}",
        short_desc="", long_desc="", raw_data={},
    )
    session.add(rune)
    session.flush()
    return rune


def test_tag_profile_vector_computes_fraction_across_items_and_runes(session: Session) -> None:
    _make_item(session, 1)
    _make_item(session, 2)
    _make_rune(session, 100)
    session.add(ItemTag(item_id=1, tag="burst", source="llm"))
    session.add(ItemTag(item_id=2, tag="tank", source="llm"))
    session.add(RuneTag(rune_id=100, tag="burst", source="llm"))
    session.commit()

    build = ObservedBuild(
        item_ids=[1, 2], primary_runes=[100], secondary_runes=[],
        win=True, weight=1.0, source="aggregate",
    )
    fractions = tag_fraction_dict(tag_profile_vector(session, build))

    # 3 tag-bearing entities total (2 items + 1 rune): burst appears twice
    # (item 1, rune 100), tank once (item 2).
    assert fractions["burst"] == 2 / 3
    assert fractions["tank"] == 1 / 3
    assert fractions["engage"] == 0.0


def test_tag_profile_vector_empty_build_is_all_zero(session: Session) -> None:
    build = ObservedBuild(
        item_ids=[], primary_runes=[], secondary_runes=[], win=True, weight=1.0, source="aggregate"
    )
    vector = tag_profile_vector(session, build)
    assert all(value == 0.0 for value in vector)


def test_tag_profile_vector_uses_effective_tags_override_precedence(session: Session) -> None:
    _make_item(session, 1)
    session.add(ItemTag(item_id=1, tag="burst", source="llm"))
    session.add(ItemTag(item_id=1, tag="tank", source="override"))
    session.commit()

    build = ObservedBuild(
        item_ids=[1], primary_runes=[], secondary_runes=[], win=True, weight=1.0, source="aggregate"
    )
    fractions = tag_fraction_dict(tag_profile_vector(session, build))

    assert fractions["tank"] == 1.0
    assert fractions["burst"] == 0.0
