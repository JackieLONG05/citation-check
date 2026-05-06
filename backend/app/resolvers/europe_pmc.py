from __future__ import annotations

import httpx

from app.models import FullTextLocation, Source, SourceCandidate, SourceMetadata, SourcePage

from .utils import compact_text, first, normalize_doi


class EuropePMCResolver:
    def __init__(self, email: str | None = None, timeout: float = 12.0) -> None:
        self.email = email
        self.timeout = timeout

    async def resolve_doi(self, doi: str) -> SourceCandidate | None:
        candidates = await self.search(f'DOI:"{normalize_doi(doi)}"', rows=1)
        return candidates[0] if candidates else None

    async def search(
        self,
        query: str,
        *,
        title_hint: str | None = None,
        text_hint: str | None = None,
        rows: int = 5,
    ) -> list[SourceCandidate]:
        params = {
            "query": _build_query(query, title_hint, text_hint),
            "format": "json",
            "resultType": "core",
            "pageSize": str(rows),
        }
        if self.email:
            params["email"] = self.email
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params=params)
            response.raise_for_status()

        results = response.json().get("resultList", {}).get("result", [])
        return [_candidate_from_result(item) for item in results]

    def full_text_location(self, candidate: SourceCandidate) -> FullTextLocation | None:
        for item in _full_text_urls(candidate.metadata):
            url = item.get("url")
            if not url:
                continue
            style = str(item.get("documentStyle") or "").lower()
            kind = "pdf" if style == "pdf" or str(url).lower().endswith(".pdf") else "landing_page"
            return FullTextLocation(
                provider="Europe PMC",
                url=url,
                kind=kind,
                license=candidate.metadata.get("license"),
                is_open_access=True,
            )
        return None

    def source_from_candidate(self, candidate: SourceCandidate, source_id: str) -> Source | None:
        abstract = compact_text(candidate.abstract)
        if not abstract:
            return None
        return Source(
            id=source_id,
            kind="web",
            name=candidate.title or candidate.url or "Europe PMC source",
            metadata=SourceMetadata(
                title=candidate.title,
                authors=candidate.authors,
                year=candidate.year,
                doi=candidate.doi,
                url=candidate.url,
                publisher=candidate.publisher,
                container=candidate.venue,
            ),
            pages=[SourcePage(page_number=None, text=abstract)],
        )


def _build_query(query: str, title_hint: str | None, text_hint: str | None) -> str:
    if title_hint:
        return f'TITLE:"{_clean_query(title_hint)}" OR ({query})'
    if text_hint:
        words = _clean_query(text_hint).split()[:12]
        if words:
            return f"{query} {' '.join(words)}"
    return query


def _candidate_from_result(data: dict) -> SourceCandidate:
    doi = first(data.get("doi") if isinstance(data.get("doi"), list) else [data.get("doi")])
    pmid = data.get("pmid")
    pmcid = data.get("pmcid")
    source_id = doi or pmcid or pmid or data.get("id") or compact_text(data.get("title")) or "unknown"
    journal_info = data.get("journalInfo") or {}
    journal = data.get("journalTitle") or (journal_info.get("journal") or {}).get("title")
    url = _result_url(doi, pmid, pmcid)
    return SourceCandidate(
        id=f"europepmc:{source_id}",
        provider="Europe PMC",
        title=compact_text(data.get("title")),
        authors=_authors(data),
        year=_year(data),
        doi=normalize_doi(doi) if doi else None,
        venue=compact_text(journal),
        publisher=None,
        url=url,
        abstract=compact_text(data.get("abstractText")),
        confidence=0.46,
        metadata={
            "pmid": pmid,
            "pmcid": pmcid,
            "source": data.get("source"),
            "isOpenAccess": data.get("isOpenAccess"),
            "inEPMC": data.get("inEPMC"),
            "inPMC": data.get("inPMC"),
            "hasPDF": data.get("hasPDF"),
            "license": data.get("license"),
            "citedByCount": data.get("citedByCount"),
            "fullTextIdList": data.get("fullTextIdList"),
            "fullTextUrlList": data.get("fullTextUrlList"),
            "pubTypeList": data.get("pubTypeList"),
        },
    )


def _authors(data: dict) -> list[str]:
    author_list = ((data.get("authorList") or {}).get("author") or [])
    authors = [item.get("fullName") for item in author_list if item.get("fullName")]
    if authors:
        return authors
    author_string = data.get("authorString")
    if not author_string:
        return []
    return [author.strip(" .") for author in author_string.split(",") if author.strip(" .")]


def _year(data: dict) -> int | None:
    value = data.get("pubYear") or data.get("firstPublicationDate", "")[:4]
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _result_url(doi: str | None, pmid: str | None, pmcid: str | None) -> str | None:
    if doi:
        return f"https://doi.org/{normalize_doi(doi)}"
    if pmcid:
        return f"https://europepmc.org/article/PMC/{pmcid.removeprefix('PMC')}"
    if pmid:
        return f"https://europepmc.org/article/MED/{pmid}"
    return None


def _full_text_urls(metadata: dict) -> list[dict]:
    container = metadata.get("fullTextUrlList") or {}
    urls = container.get("fullTextUrl") if isinstance(container, dict) else None
    if isinstance(urls, dict):
        urls = [urls]
    if not isinstance(urls, list):
        return []
    oa_urls = [item for item in urls if str(item.get("availabilityCode") or "").upper() == "OA"]
    return oa_urls or [item for item in urls if item.get("url")]


def _clean_query(value: str) -> str:
    return (compact_text(value) or "").replace('"', " ")
