from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from app.models import Source, SourceCandidate, SourceMetadata, SourcePage

from .utils import compact_text, normalize_doi


NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivResolver:
    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout

    async def search(
        self,
        query: str,
        *,
        title_hint: str | None = None,
        text_hint: str | None = None,
        rows: int = 5,
    ) -> list[SourceCandidate]:
        params = {
            "search_query": _build_search_query(query, title_hint, text_hint),
            "start": "0",
            "max_results": str(rows),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get("https://export.arxiv.org/api/query", params=params)
            response.raise_for_status()

        root = ET.fromstring(response.text)
        return [_candidate_from_entry(entry) for entry in root.findall("atom:entry", NS)]

    def source_from_candidate(self, candidate: SourceCandidate, source_id: str) -> Source | None:
        abstract = compact_text(candidate.abstract)
        if not abstract:
            return None
        return Source(
            id=source_id,
            kind="web",
            name=candidate.title or candidate.url or "arXiv source",
            metadata=SourceMetadata(
                title=candidate.title,
                authors=candidate.authors,
                year=candidate.year,
                doi=candidate.doi,
                url=candidate.url,
                publisher="arXiv",
                container=candidate.venue,
            ),
            pages=[SourcePage(page_number=None, text=abstract)],
        )


def _candidate_from_entry(entry: ET.Element) -> SourceCandidate:
    arxiv_url = _text(entry, "atom:id")
    arxiv_id = (arxiv_url or "unknown").rstrip("/").split("/")[-1]
    doi = _text(entry, "arxiv:doi")
    pdf_url = _pdf_url(entry)
    published = _text(entry, "atom:published")
    return SourceCandidate(
        id=f"arxiv:{arxiv_id}",
        provider="arXiv",
        title=compact_text(_text(entry, "atom:title")),
        authors=[
            compact_text(name.text) or ""
            for name in entry.findall("atom:author/atom:name", NS)
            if compact_text(name.text)
        ],
        year=_year(published),
        doi=normalize_doi(doi) if doi else None,
        venue="arXiv",
        publisher="arXiv",
        url=arxiv_url,
        abstract=compact_text(_text(entry, "atom:summary")),
        confidence=0.44,
        metadata={
            "arxiv_id": arxiv_id,
            "published": published,
            "updated": _text(entry, "atom:updated"),
            "primary_category": _primary_category(entry),
            "categories": _categories(entry),
            "pdf_url": pdf_url,
        },
    )


def _text(entry: ET.Element, path: str) -> str | None:
    node = entry.find(path, NS)
    return node.text if node is not None else None


def _year(value: str | None) -> int | None:
    if not value or len(value) < 4:
        return None
    try:
        return int(value[:4])
    except ValueError:
        return None


def _build_search_query(query: str, title_hint: str | None, text_hint: str | None) -> str:
    if title_hint:
        return f'ti:"{_clean_query(title_hint)}"'
    if text_hint:
        return f'all:"{_clean_query(text_hint)[:240]}"'
    return f'all:"{_clean_query(query)}"'


def _pdf_url(entry: ET.Element) -> str | None:
    for link in entry.findall("atom:link", NS):
        href = link.attrib.get("href")
        if not href:
            continue
        if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
            return href
    return None


def _primary_category(entry: ET.Element) -> str | None:
    node = entry.find("arxiv:primary_category", NS)
    return node.attrib.get("term") if node is not None else None


def _categories(entry: ET.Element) -> list[str]:
    return [node.attrib["term"] for node in entry.findall("atom:category", NS) if node.attrib.get("term")]


def _clean_query(value: str) -> str:
    return (compact_text(value) or "").replace('"', " ")
