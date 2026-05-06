from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from app.models import SourceCandidate

from .utils import compact_text, normalize_doi


class DataCiteResolver:
    def __init__(self, user_agent: str | None = None, timeout: float = 12.0) -> None:
        self.user_agent = user_agent
        self.timeout = timeout

    async def resolve_doi(self, doi: str) -> SourceCandidate | None:
        normalized = normalize_doi(doi)
        encoded = quote(normalized, safe="")
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(
                f"https://api.datacite.org/dois/{encoded}",
                headers=_headers(self.user_agent),
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()

        data = response.json().get("data")
        return _candidate_from_item(data, confidence=0.9) if data else None

    async def search(self, query: str, rows: int = 5) -> list[SourceCandidate]:
        params = {"query": query, "page[size]": str(rows)}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(
                "https://api.datacite.org/dois",
                params=params,
                headers=_headers(self.user_agent),
            )
            response.raise_for_status()

        return [_candidate_from_item(item, confidence=0.42) for item in response.json().get("data", [])]


def _candidate_from_item(item: dict, confidence: float = 0.42) -> SourceCandidate:
    attrs = item.get("attributes") or {}
    doi = normalize_doi(attrs.get("doi") or item.get("id") or "")
    return SourceCandidate(
        id=f"datacite:{doi or item.get('id') or 'unknown'}",
        provider="DataCite",
        title=_title(attrs),
        authors=_creators(attrs),
        year=_year(attrs),
        doi=doi or None,
        venue=compact_text((attrs.get("container") or {}).get("title")),
        publisher=compact_text(attrs.get("publisher")),
        url=attrs.get("url") or (f"https://doi.org/{doi}" if doi else None),
        abstract=_description(attrs),
        confidence=confidence,
        metadata={
            "types": attrs.get("types"),
            "state": attrs.get("state"),
            "contentUrl": attrs.get("contentUrl"),
            "rightsList": attrs.get("rightsList"),
            "citationCount": attrs.get("citationCount"),
            "referenceCount": attrs.get("referenceCount"),
        },
    )


def _title(attrs: dict) -> str | None:
    titles = attrs.get("titles") or []
    if not titles:
        return None
    return compact_text(titles[0].get("title"))


def _creators(attrs: dict) -> list[str]:
    return [creator.get("name") for creator in attrs.get("creators", []) if creator.get("name")]


def _year(attrs: dict) -> int | None:
    value = attrs.get("publicationYear")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _description(attrs: dict) -> str | None:
    descriptions = attrs.get("descriptions") or []
    for item in descriptions:
        text = item.get("description")
        if text:
            text = re.sub(r"<[^>]+>", " ", text)
            return compact_text(text)
    return None


def _headers(user_agent: str | None) -> dict[str, str] | None:
    return {"User-Agent": user_agent} if user_agent else None
