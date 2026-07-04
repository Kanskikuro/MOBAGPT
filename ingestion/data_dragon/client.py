"""Thin HTTP client for the Data Dragon static-data CDN. No DB access here -
see source.py for how the fetched JSON is turned into rows."""

from __future__ import annotations

import requests

from config.settings import DATA_DRAGON


def _get(url: str) -> dict | list:
    response = requests.get(url, timeout=DATA_DRAGON.request_timeout_seconds)
    response.raise_for_status()
    return response.json()


def list_versions() -> list[str]:
    return _get(f"{DATA_DRAGON.base_url}/api/versions.json")


def latest_version() -> str:
    versions = list_versions()

    if not versions:
        raise RuntimeError("Data Dragon returned no versions.")

    return versions[0]


def resolve_version(patch: str | None) -> str:
    """`patch=None` (or "latest") resolves to the newest Data Dragon version.
    An explicit patch (e.g. "14.14") resolves to the matching full version
    (e.g. "14.14.1"); an already-full version (e.g. "14.14.1") passes through
    if it exists."""

    if patch is None or patch == "latest":
        return latest_version()

    versions = list_versions()

    if patch in versions:
        return patch

    matches = [v for v in versions if v.startswith(f"{patch}.")]

    if not matches:
        raise ValueError(f"No Data Dragon version found matching patch '{patch}'.")

    return matches[0]


def fetch_champion_list(version: str, locale: str | None = None) -> dict:
    loc = locale or DATA_DRAGON.locale
    url = f"{DATA_DRAGON.base_url}/cdn/{version}/data/{loc}/champion.json"
    return _get(url)["data"]


def fetch_champion_full(version: str, riot_key: str, locale: str | None = None) -> dict:
    loc = locale or DATA_DRAGON.locale
    url = f"{DATA_DRAGON.base_url}/cdn/{version}/data/{loc}/champion/{riot_key}.json"
    return _get(url)["data"][riot_key]


def fetch_items(version: str, locale: str | None = None) -> dict:
    loc = locale or DATA_DRAGON.locale
    url = f"{DATA_DRAGON.base_url}/cdn/{version}/data/{loc}/item.json"
    return _get(url)["data"]


def fetch_rune_trees(version: str, locale: str | None = None) -> list:
    loc = locale or DATA_DRAGON.locale
    url = f"{DATA_DRAGON.base_url}/cdn/{version}/data/{loc}/runesReforged.json"
    return _get(url)
