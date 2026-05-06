from __future__ import annotations

import httpx

from app.models import FullTextLocation, SourceCandidate

from .utils import compact_text, normalize_doi


class OpenAlexResolver:
    def __init__(self, mailto: str | None = None, api_key: str | None = None, timeout: float = 12.0) -> None:
        self.mailto = mailto
        self.api_key = api_key
        self.timeout = timeout

    async def resolve_doi(self, doi: str) -> SourceCandidate | None:
        normalized = normalize_doi(doi)
        url = f"https://api.openalex.org/works/https://doi.org/{normalized}"
        params = self._params()
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(url, params=params)
            if response.status_code == 404:
                return None
            response.raise_for_status()

        data = response.json()
        return _candidate_from_work(normalized, data)

    async def search(self, query: str, rows: int = 5) -> list[SourceCandidate]:
        params = {"search": query, "per-page": str(rows), **self._params()}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get("https://api.openalex.org/works", params=params)
            response.raise_for_status()

        results = response.json().get("results", [])
        return [_candidate_from_work((item.get("doi") or item.get("id") or "unknown"), item) for item in results]

    def full_text_location(self, candidate: SourceCandidate) -> FullTextLocation | None:
        oa = candidate.metadata.get("open_access") or {}
        best = candidate.metadata.get("best_oa_location") or {}
        pdf_url = best.get("pdf_url")
        landing_url = best.get("landing_page_url") or oa.get("oa_url")
        license_value = best.get("license")
        if pdf_url:
            return FullTextLocation(provider="OpenAlex", url=pdf_url, kind="pdf", license=license_value)
        if landing_url:
            return FullTextLocation(provider="OpenAlex", url=landing_url, kind="landing_page", license=license_value)
        for location in candidate.metadata.get("locations") or []:
            if not location.get("is_oa"):
                continue
            pdf_url = location.get("pdf_url")
            landing_url = location.get("landing_page_url")
            license_value = location.get("license")
            if pdf_url:
                return FullTextLocation(provider="OpenAlex", url=pdf_url, kind="pdf", license=license_value)
            if landing_url:
                return FullTextLocation(provider="OpenAlex", url=landing_url, kind="landing_page", license=license_value)
        return None

    def _params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self.mailto:
            params["mailto"] = self.mailto
        if self.api_key:
            params["api_key"] = self.api_key
        return params


def _candidate_from_work(normalized_doi: str, data: dict) -> SourceCandidate:
    authorships = data.get("authorships") or []
    authors = [
        item.get("author", {}).get("display_name")
        for item in authorships
        if item.get("author", {}).get("display_name")
    ]
    primary_location = data.get("primary_location") or {}
    source = primary_location.get("source") or {}
    return SourceCandidate(
        id=data.get("id") or f"openalex:{normalized_doi}",
        provider="OpenAlex",
        title=compact_text(data.get("title") or data.get("display_name")),
        authors=authors,
        year=data.get("publication_year"),
        doi=(data.get("doi") or f"https://doi.org/{normalized_doi}").removeprefix("https://doi.org/"),
        venue=source.get("display_name"),
        publisher=source.get("host_organization_name"),
        url=data.get("doi") or data.get("id"),
        abstract=_abstract_from_inverted_index(data.get("abstract_inverted_index")),
        confidence=0.96,
        metadata={
            "cited_by_count": data.get("cited_by_count"),
            "open_access": data.get("open_access"),
            "best_oa_location": data.get("best_oa_location"),
            "primary_location": primary_location,
            "locations": data.get("locations"),
            "related_works": data.get("related_works"),
            "cited_by_api_url": data.get("cited_by_api_url"),
        },
    )


def _abstract_from_inverted_index(index: dict | None) -> str | None:
    if not isinstance(index, dict):
        return None
    positions: dict[int, str] = {}
    for word, offsets in index.items():
        for offset in offsets or []:
            positions[int(offset)] = word
    return compact_text(" ".join(positions[key] for key in sorted(positions)))
