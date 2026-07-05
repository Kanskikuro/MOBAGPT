from pathlib import Path

from ingestion.wiki.wikitext import (
    _split_top_level,
    discover_ability_slots,
    parse_wiki_template,
    parse_key_value_fields,
    unwrap_decorators,
)

FIXTURES = Path(__file__).parent / "fixtures" / "wiki"


def test_parse_key_value_fields_basic() -> None:
    wikitext = "{{Ability data\n|champion = Ahri\n|skill = Q\n}}"
    assert parse_key_value_fields(wikitext) == {"champion": "Ahri", "skill": "Q"}


def test_parse_key_value_fields_ignores_preamble_before_first_field() -> None:
    wikitext = (
        "{{{{{1<noinclude>|Ability data</noinclude>}}}|Orb of Deception|{{{2|}}}\n"
        "|champion = Ahri\n"
        "}}"
    )
    assert parse_key_value_fields(wikitext) == {"champion": "Ahri"}


def test_unwrap_decorators_nested_braces() -> None:
    text = "{{ap|40 to 140}} {{as|(+ 50% AP)}}"
    result, unknown = unwrap_decorators(text)
    assert result == "40 to 140 (+ 50% AP)"
    assert unknown == set()


def test_unwrap_decorators_deeply_nested() -> None:
    text = "{{as|(+ {{ap|50*2}}% AP)}}"
    result, unknown = unwrap_decorators(text)
    assert result == "(+ 50*2% AP)"
    assert unknown == set()


def test_unwrap_decorators_unknown_template_left_verbatim() -> None:
    text = "before {{weirdTemplate|foo|bar}} after"
    result, unknown = unwrap_decorators(text)
    assert result == "before {{weirdTemplate|foo|bar}} after"
    assert unknown == {"weirdtemplate"}


def test_split_top_level_ignores_pipe_inside_wikilink() -> None:
    """A wikilink's internal '|' must not be mistaken for a template
    parameter boundary - this is what the bracket-depth counter (as opposed
    to a brace-only counter) is specifically for."""
    parts = _split_top_level("as|[[Physical Damage|physical damage]]|extra")
    assert parts == ["as", "[[Physical Damage|physical damage]]", "extra"]


def test_parse_wiki_template_cleans_wikilinks_and_bold_italic() -> None:
    wikitext = (
        "{{Ability data\n"
        "|notes = See [[Physical Damage|physical damage]] and '''bold''' text.\n"
        "}}"
    )
    result = parse_wiki_template(wikitext)
    assert result.notes == "See physical damage and bold text."


def test_discover_ability_slots_multi_form_champion() -> None:
    section = (
        "== Abilities ==\n"
        "{{Data Elise/I|Ability}}\n"
        "{{Image tabber\n"
        "|title1=Human abilities\n"
        "|content1=\n"
        "{{Data Elise/Q|Ability}}\n"
        "{{Data Elise/W|Ability}}\n"
        "{{Data Elise/E|Ability}}\n"
        "|title2=Spider abilities\n"
        "|content2=\n"
        "{{Data Elise/Venomous Bite|Ability}}\n"
        "{{Data Elise/Skittering Frenzy|Ability}}\n"
        "{{Data Elise/Rappel|Ability}}\n"
        "}}\n"
        "{{Data Elise/R|Ability}}"
    )
    assert discover_ability_slots(section, "Elise") == [
        "I", "Q", "W", "E", "Venomous Bite", "Skittering Frenzy", "Rappel", "R",
    ]


def test_parse_wiki_template_promotes_tips() -> None:
    wikitext = (FIXTURES / "katarina_death_lotus.wikitext").read_text(encoding="utf-8")
    result = parse_wiki_template(wikitext)
    assert "tips" not in result.fields
    assert "light up once an enemy" in result.tips


def test_parse_wiki_template_real_fixture() -> None:
    wikitext = (FIXTURES / "ahri_orb_of_deception.wikitext").read_text(encoding="utf-8")
    result = parse_wiki_template(wikitext)

    assert result.fields["champion"] == "Ahri"
    assert result.fields["skill"] == "Q"
    assert result.fields["cooldown"] == "7"
    assert result.fields["cost"] == "55 to 95"
    assert result.fields["cast time"] == "0.25"
    assert result.fields["leveling"] == (
        "Damage Per Pass: 40 to 140 (+ 50% AP); "
        "Total Mixed Damage: 40*2 to 140*2 (+ 50*2% AP)"
    )
    assert "dies while the orb is out" in result.notes
    assert "effect at cast time end" in result.unknown_templates


def test_parse_wiki_template_item_fixture() -> None:
    """Items use the same generic {{Item info}} key=value shape as champion
    abilities use {{Ability data}} - confirmed on World Atlas, a quest item
    whose Data Dragon description is empty (the real mechanic - charge
    timing, quest thresholds - only exists in this wiki prose)."""
    wikitext = (FIXTURES / "world_atlas_item.wikitext").read_text(encoding="utf-8")
    result = parse_wiki_template(wikitext)

    assert result.fields["goldvalue"] == ""
    assert "1:50" in result.notes
    assert "minion kill" in result.notes
    assert "diminishing gold info" in result.unknown_templates
