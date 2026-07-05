import pytest

from knowledge import client


def test_validate_tags_drops_out_of_taxonomy_and_dedupes() -> None:
    warnings: list[str] = []
    tags = client._validate_tags(
        ["burst", "engage", "burst", "not_a_real_tag"], "Ahri", warnings
    )

    assert tags == ["burst", "engage"]
    assert len(warnings) == 1
    assert "not_a_real_tag" in warnings[0]


def test_validate_ratings_fills_missing_with_midpoint() -> None:
    warnings: list[str] = []
    ratings = client._validate_ratings({"engage": 7}, "Ahri", warnings)

    assert ratings["engage"] == 7
    assert ratings["frontline"] == 5.0  # midpoint of 0-10, filled in
    assert any("missing rating" in w for w in warnings)


def test_validate_ratings_clamps_out_of_range() -> None:
    warnings: list[str] = []
    ratings = client._validate_ratings({"engage": 15, "frontline": -3}, "Ahri", warnings)

    assert ratings["engage"] == 10.0
    assert ratings["frontline"] == 0.0
    assert any("clamped" in w for w in warnings)


def test_validate_ratings_drops_unknown_rating_name() -> None:
    warnings: list[str] = []
    ratings = client._validate_ratings({"not_a_rating": 5}, "Ahri", warnings)

    assert "not_a_rating" not in ratings
    assert any("not_a_rating" in w for w in warnings)


def test_extract_champion_profile_uses_llm_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client,
        "_call_anthropic",
        lambda prompt, tool: {
            "tags": ["burst", "mobility"],
            "ratings": {
                "engage": 3, "disengage": 2, "frontline": 1, "peel": 1,
                "wave_clear": 5, "burst": 9, "sustained_dps": 4, "mobility": 8,
                "cc_score": 2, "scaling_curve": 5,
            },
        },
    )

    result = client.extract_champion_profile("Ahri", "some ability text")

    assert result.tags == ["burst", "mobility"]
    assert result.ratings["burst"] == 9
    assert result.warnings == []


def test_extract_tags_dispatches_item_vs_rune_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_prompts: list[str] = []

    def fake_call(prompt: str, tool: dict) -> dict:
        captured_prompts.append(prompt)
        return {"tags": ["sustained_dps"]}

    monkeypatch.setattr(client, "_call_anthropic", fake_call)

    item_result = client.extract_tags("item", "Rageblade", "item description text")
    rune_result = client.extract_tags("rune", "Electrocute", "rune description text")

    assert item_result.tags == ["sustained_dps"]
    assert rune_result.tags == ["sustained_dps"]
    assert "item" in captured_prompts[0].lower()
    assert "rune" in captured_prompts[1].lower()


def test_get_client_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace

    monkeypatch.setattr("knowledge.client._client", None)
    monkeypatch.setattr(
        "knowledge.client.LLM_TAGGING", replace(client.LLM_TAGGING, api_key="")
    )

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        client._get_client()
