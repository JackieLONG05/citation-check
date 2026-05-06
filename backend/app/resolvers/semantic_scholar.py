from __future__ import annotations

import httpx

from app.models import FullTextLocation, SourceCandidate

from .utils import compact_text, normalize_doi


class SemanticScholarResolver:
    def __init__(self, api_key: str | None = None, timeout: float = 12.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    async def resolve_doi(self, doi: str) -> SourceCandidate | None:
        fields = "paperId,title,abstract,year,venue,url,authors,externalIds,openAccessPdf,citationCount,publicationTypes"
        headers = {"x-api-key": self.api_key} if self.api_key else None
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(
                f"https://api.semanticscholar.org/graph/v1/paper/DOI:{normalize_doi(doi)}",
                params={"fields": fields},
                headers=headers,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()

        return _candidate_from_paper(response.json())

    async def search(self, query: str, rows: int = 5) -> list[SourceCandidate]:
        params = {
            "query": query,
            "limit": str(rows),
            "fields": "paperId,title,abstract,year,venue,url,authors,externalIds,openAccessPdf,citationCount,publicationTypes",
        }
        headers = {"x-api-key": self.api_key} if self.api_key else None
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params=params,
                headers=headers,
            )
            response.raise_for_status()

        return [_candidate_from_paper(item) for item in response.json().get("data", [])]

    def full_text_location(self, candidate: SourceCandidate) -> FullTextLocation | None:
        open_pdf = candidate.metadata.get("openAccessPdf")
        if not isinstance(open_pdf, dict):
            return None
        url = open_pdf.get("url")
        if not url:
            return None
        return FullTextLocation(
            provider="Semantic Scholar",
            url=url,
            kind="pdf" if str(url).lower().endswith(".pdf") else "landing_page",
            license=open_pdf.get("license"),
        )


def _candidate_from_paper(data: dict) -> SourceCandidate:
    external_ids = data.get("externalIds") or {}
    doi = external_ids.get("DOI")
    paper_id = data.get("paperId") or "unknown"
    return SourceCandidate(
        id=f"semanticscholar:{paper_id}",
        provider="Semantic Scholar",
        title=compact_text(data.get("title")),
        authors=[author.get("name") for author in data.get("authors", []) if author.get("name")],
        year=data.get("year"),
        doi=normalize_doi(doi) if doi else None,
        venue=data.get("venue"),
        publisher=None,
        url=data.get("url"),
        abstract=compact_text(data.get("abstract")),
        confidence=0.48,
        metadata={
            "paperId": paper_id,
            "externalIds": external_ids,
            "openAccessPdf": data.get("openAccessPdf"),
            "citationCount": data.get("citationCount"),
            "publicationTypes": data.get("publicationTypes"),
        },
    )
