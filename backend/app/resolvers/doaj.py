from __future__ import annotations

from urllib.parse import quote

import httpx

from app.models import FullTextLocation, SourceCandidate

from .utils import compact_text, normalize_doi


class DOAJResolver:
    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout

    async def resolve_doi(self, doi: str) -> SourceCandidate | None:
        candidates = await self.search(f"doi:{normalize_doi(doi)}", rows=1)
        return candidates[0] if candidates else None

    async def search(
        self,
        query: str,
        *,
        title_hint: str | None = None,
        author_hint: str | None = None,
        year: int | None = None,
        rows: int = 5,
    ) -> list[SourceCandidate]:
        search_query = _build_query(query, title_hint, author_hint, year)
        url = f"https://doaj.org/api/search/articles/{quote(search_query, safe='')}"
        params = {"page": "1", "pageSize": str(rows)}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()

        return [_candidate_from_result(item) for item in response.json().get("results", [])]

    def full_text_location(self, candidate: SourceCandidate) -> FullTextLocation | None:
        links = candidate.metadata.get("link") or []
        for preferred_pdf in (True, False):
            for item in links:
                url = item.get("url")
                if not url:
                    continue
                content_type = str(item.get("content_type") or "").lower()
                is_pdf = "pdf" in content_type or str(url).lower().endswith(".pdf")
                if preferred_pdf != is_pdf:
                    continue
                return FullTextLocation(
                    provider="DOAJ",
                    url=url,
                    kind="pdf" if is_pdf else "landing_page",
                    is_open_access=True,
                )
        return None


def _build_query(query: str, title_hint: str | None, author_hint: str | None, year: int | None) -> str:
    if title_hint:
        parts = [f'bibjson.title:"{_clean_query(title_hint)}"']
        if author_hint:
            parts.append(f'bibjson.author.name:"{_clean_query(author_hint)}"')
        if year:
            parts.append(f"bibjson.year:{year}")
        return " ".join(parts)
    return query


def _candidate_from_result(data: dict) -> SourceCandidate:
    bibjson = data.get("bibjson") or {}
    doi = _identifier(bibjson, "doi")
    journal = bibjson.get("journal") or {}
    return SourceCandidate(
        id=f"doaj:{data.get('id') or doi or compact_text(bibjson.get('title')) or 'unknown'}",
        provider="DOAJ",
        title=compact_text(bibjson.get("title")),
        authors=[author.get("name") for author in bibjson.get("author", []) if author.get("name")],
        year=_year(bibjson.get("year")),
        doi=normalize_doi(doi) if doi else None,
        venue=compact_text(journal.get("title")),
        publisher=compact_text(journal.get("publisher")),
        url=_best_url(bibjson) or (f"https://doi.org/{normalize_doi(doi)}" if doi else None),
        abstract=compact_text(bibjson.get("abstract")),
        confidence=0.5,
        metadata={
            "identifier": bibjson.get("identifier"),
            "journal": journal,
            "link": bibjson.get("link") or [],
            "keywords": bibjson.get("keywords"),
            "subject": bibjson.get("subject"),
            "last_updated": data.get("last_updated"),
        },
    )


def _identifier(bibjson: dict, identifier_type: str) -> str | None:
    for item in bibjson.get("identifier", []):
        if str(item.get("type") or "").lower() == identifier_type and item.get("id"):
            return item["id"]
    return None


def _best_url(bibjson: dict) -> str | None:
    for item in bibjson.get("link", []) or []:
        if item.get("url"):
            return item["url"]
    return None


def _year(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_query(value: str) -> str:
    return (compact_text(value) or "").replace('"', " ")
