from __future__ import annotations

import httpx

from app.models import SourceCandidate

from .utils import compact_text, first, normalize_doi


class CrossrefResolver:
    def __init__(self, mailto: str | None = None, timeout: float = 12.0) -> None:
        self.mailto = mailto
        self.timeout = timeout

    async def resolve_doi(self, doi: str) -> SourceCandidate | None:
        normalized = normalize_doi(doi)
        url = f"https://api.crossref.org/works/{normalized}"
        params = {"mailto": self.mailto} if self.mailto else None
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(url, params=params)
            if response.status_code == 404:
                return None
            response.raise_for_status()

        return _candidate_from_message(normalized, response.json().get("message", {}), confidence=0.98)

    async def search_bibliographic(self, query: str, rows: int = 5) -> list[SourceCandidate]:
        params = {"query.bibliographic": query, "rows": str(rows)}
        if self.mailto:
            params["mailto"] = self.mailto
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get("https://api.crossref.org/works", params=params)
            response.raise_for_status()

        items = response.json().get("message", {}).get("items", [])
        candidates: list[SourceCandidate] = []
        for item in items:
            candidates.append(_candidate_from_message(item.get("DOI"), item, confidence=0.5))
        return candidates


def _candidate_from_message(doi: str | None, message: dict, confidence: float) -> SourceCandidate:
    title = first(message.get("title"))
    normalized = normalize_doi(message.get("DOI") or doi) if message.get("DOI") or doi else None
    fallback_id = normalized or compact_text(title) or message.get("URL") or "unknown"
    return SourceCandidate(
        id=f"crossref:{fallback_id}",
        provider="Crossref",
        title=compact_text(title),
        authors=_format_authors(message.get("author", [])),
        year=_extract_year(message),
        doi=normalized,
        venue=first(message.get("container-title")),
        publisher=message.get("publisher"),
        url=message.get("URL") or (f"https://doi.org/{normalized}" if normalized else None),
        abstract=compact_text(message.get("abstract")),
        confidence=confidence,
        metadata={"type": message.get("type"), "is-referenced-by-count": message.get("is-referenced-by-count")},
    )


def _format_authors(authors: list[dict]) -> list[str]:
    formatted: list[str] = []
    for author in authors:
        parts = [author.get("given"), author.get("family")]
        name = " ".join(part for part in parts if part)
        if name:
            formatted.append(name)
    return formatted


def _extract_year(message: dict) -> int | None:
    for key in ("published-print", "published-online", "published", "issued"):
        parts = message.get(key, {}).get("date-parts")
        if parts and parts[0]:
            return int(parts[0][0])
    return None
