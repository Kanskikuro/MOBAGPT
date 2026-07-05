"""Thin wrapper around the League of Legends Fandom wiki's MediaWiki API
(api.php). Plain HTML page fetches on this site are blocked by a Cloudflare
JS challenge; api.php is not behind that challenge and works fine with a
descriptive User-Agent - this is the documented, sanctioned API, not
scraping evasion.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from config.settings import WIKI

_session = requests.Session()
_session.headers.update({"User-Agent": WIKI.user_agent})


class WikiPageNotFoundError(Exception):
    def __init__(self, title: str) -> None:
        super().__init__(f"Wiki page not found: {title!r}")
        self.title = title


@dataclass
class WikitextPage:
    resolved_title: str
    wikitext: str
    pageid: int


_MAX_ATTEMPTS = 3


def _get(params: dict) -> dict:
    url = f"{WIKI.base_url}{WIKI.api_path}"
    query = {"format": "json", "formatversion": "2", **params}

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = _session.get(url, params=query, timeout=WIKI.request_timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            break
        except requests.exceptions.RequestException:
            if attempt == _MAX_ATTEMPTS:
                raise
            time.sleep(WIKI.request_delay_seconds * (2 ** attempt))
        finally:
            time.sleep(WIKI.request_delay_seconds)

    error = payload.get("error")
    if error is not None:
        if error.get("code") == "missingtitle":
            raise WikiPageNotFoundError(str(params.get("page", "")))
        raise RuntimeError(f"MediaWiki API error: {error}")

    return payload


def fetch_wikitext(title: str, *, section: str | None = None, redirects: bool = True) -> WikitextPage:
    params: dict = {"action": "parse", "page": title, "prop": "wikitext"}

    if section is not None:
        params["section"] = section

    if redirects:
        params["redirects"] = "1"

    payload = _get(params)
    parse = payload["parse"]

    return WikitextPage(
        resolved_title=parse["title"],
        wikitext=parse["wikitext"],
        pageid=parse["pageid"],
    )


def fetch_sections(title: str) -> list[dict]:
    payload = _get({"action": "parse", "page": title, "prop": "sections"})
    return payload["parse"]["sections"]
