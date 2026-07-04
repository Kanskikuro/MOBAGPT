import pytest

from ingestion.data_dragon import client


@pytest.fixture
def fake_versions(monkeypatch):
    monkeypatch.setattr(
        client, "list_versions", lambda: ["14.14.1", "14.13.1", "14.12.1"]
    )


def test_resolve_version_latest(fake_versions) -> None:
    assert client.resolve_version(None) == "14.14.1"
    assert client.resolve_version("latest") == "14.14.1"


def test_resolve_version_exact_match(fake_versions) -> None:
    assert client.resolve_version("14.13.1") == "14.13.1"


def test_resolve_version_prefix_match(fake_versions) -> None:
    assert client.resolve_version("14.13") == "14.13.1"


def test_resolve_version_no_match_raises(fake_versions) -> None:
    with pytest.raises(ValueError):
        client.resolve_version("9.1")
