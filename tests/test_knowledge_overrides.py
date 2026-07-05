from pathlib import Path

import pytest

from knowledge.overrides import load_overrides


def test_missing_file_returns_empty_overrides(tmp_path: Path) -> None:
    overrides = load_overrides(tmp_path / "does_not_exist.yaml")

    assert overrides.champions == {}
    assert overrides.items == {}
    assert overrides.runes == {}


def test_parses_full_replacement_tags_and_ratings(tmp_path: Path) -> None:
    path = tmp_path / "tag_overrides.yaml"
    path.write_text(
        """
champions:
  Ambessa:
    tags: [engage, burst]
    ratings:
      engage: 8
      frontline: 6
items:
  Rageblade:
    tags: [sustained_dps]
runes: {}
""",
        encoding="utf-8",
    )

    overrides = load_overrides(path)

    assert overrides.champions["Ambessa"].tags == ["engage", "burst"]
    assert overrides.champions["Ambessa"].ratings == {"engage": 8, "frontline": 6}
    assert overrides.items["Rageblade"].tags == ["sustained_dps"]
    assert overrides.items["Rageblade"].ratings is None
    assert overrides.runes == {}


def test_omitted_field_stays_none(tmp_path: Path) -> None:
    path = tmp_path / "tag_overrides.yaml"
    path.write_text(
        """
champions:
  Ambessa:
    ratings:
      engage: 8
""",
        encoding="utf-8",
    )

    overrides = load_overrides(path)

    assert overrides.champions["Ambessa"].tags is None
    assert overrides.champions["Ambessa"].ratings == {"engage": 8}


def test_rejects_out_of_taxonomy_tag(tmp_path: Path) -> None:
    path = tmp_path / "tag_overrides.yaml"
    path.write_text(
        """
champions:
  Ambessa:
    tags: [not_a_real_tag]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not_a_real_tag"):
        load_overrides(path)


def test_rejects_unknown_rating_name(tmp_path: Path) -> None:
    path = tmp_path / "tag_overrides.yaml"
    path.write_text(
        """
champions:
  Ambessa:
    ratings:
      not_a_rating: 5
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not_a_rating"):
        load_overrides(path)


def test_rejects_out_of_range_rating(tmp_path: Path) -> None:
    path = tmp_path / "tag_overrides.yaml"
    path.write_text(
        """
champions:
  Ambessa:
    ratings:
      engage: 15
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="engage"):
        load_overrides(path)
