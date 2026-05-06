from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx

from app.models import SourceCandidate

from .utils import compact_text, normalize_doi


class PubMedResolver:
    def __init__(
        self,
        email: str | None = None,
        tool: str = "evidencelint",
        api_key: str | None = None,
        timeout: float = 12.0,
    ) -> None:
        self.email = email
        self.tool = tool
        self.api_key = api_key
        self.timeout = timeout

    async def resolve_doi(self, doi: str) -> SourceCandidate | None:
        normalized = normalize_doi(doi)
        candidates = await self.search(f'{normalized}[AID] OR {normalized}[DOI]', rows=1)
        return candidates[0] if candidates else None

    async def search(self, query: str, rows: int = 5) -> list[SourceCandidate]:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            search_response = await client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={
                    **self._base_params(),
                    "db": "pubmed",
                    "term": query,
                    "retmode": "json",
                    "retmax": str(rows),
                    "sort": "relevance",
                },
            )
            search_response.raise_for_status()
            pmids = search_response.json().get("esearchresult", {}).get("idlist", [])
            if not pmids:
                return []

            fetch_response = await client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params={
                    **self._base_params(),
                    "db": "pubmed",
                    "id": ",".join(pmids),
                    "retmode": "xml",
                },
            )
            fetch_response.raise_for_status()

        root = ET.fromstring(fetch_response.text)
        return [_candidate_from_article(article) for article in root.findall(".//PubmedArticle")]

    def _base_params(self) -> dict[str, str]:
        params = {"tool": self.tool}
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        return params


def _candidate_from_article(article: ET.Element) -> SourceCandidate:
    pmid = _text(article, ".//MedlineCitation/PMID") or "unknown"
    doi = _article_id(article, "doi") or _elocation_id(article, "doi")
    pmcid = _article_id(article, "pmc")
    return SourceCandidate(
        id=f"pubmed:{pmid}",
        provider="PubMed",
        title=compact_text(_inner_text(article.find(".//ArticleTitle"))),
        authors=_authors(article),
        year=_year(article),
        doi=normalize_doi(doi) if doi else None,
        venue=compact_text(_text(article, ".//Journal/Title") or _text(article, ".//Journal/ISOAbbreviation")),
        publisher=None,
        url=f"https://doi.org/{normalize_doi(doi)}" if doi else f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        abstract=_abstract(article),
        confidence=0.52,
        metadata={
            "pmid": pmid,
            "pmcid": pmcid,
            "article_ids": _article_ids(article),
            "publication_types": _publication_types(article),
            "mesh_headings": _mesh_headings(article),
            "journal_iso": _text(article, ".//Journal/ISOAbbreviation"),
            "language": [_inner_text(node) for node in article.findall(".//Article/Language")],
        },
    )


def _text(node: ET.Element, path: str) -> str | None:
    child = node.find(path)
    return _inner_text(child)


def _inner_text(node: ET.Element | None) -> str | None:
    if node is None:
        return None
    return compact_text(" ".join(node.itertext()))


def _authors(article: ET.Element) -> list[str]:
    authors: list[str] = []
    for author in article.findall(".//AuthorList/Author"):
        collective = _text(author, "CollectiveName")
        if collective:
            authors.append(collective)
            continue
        parts = [_text(author, "ForeName"), _text(author, "LastName")]
        name = compact_text(" ".join(part for part in parts if part))
        if name:
            authors.append(name)
    return authors


def _year(article: ET.Element) -> int | None:
    for path in (".//JournalIssue/PubDate/Year", ".//ArticleDate/Year"):
        value = _text(article, path)
        if value:
            return int(value)
    medline_date = _text(article, ".//JournalIssue/PubDate/MedlineDate") or ""
    match = re.search(r"\b(19|20)\d{2}\b", medline_date)
    return int(match.group(0)) if match else None


def _abstract(article: ET.Element) -> str | None:
    parts: list[str] = []
    for node in article.findall(".//Abstract/AbstractText"):
        text = _inner_text(node)
        if not text:
            continue
        label = node.attrib.get("Label")
        parts.append(f"{label}: {text}" if label else text)
    return compact_text(" ".join(parts))


def _article_id(article: ET.Element, id_type: str) -> str | None:
    for node in article.findall(".//ArticleIdList/ArticleId"):
        if node.attrib.get("IdType") == id_type:
            return _inner_text(node)
    return None


def _elocation_id(article: ET.Element, id_type: str) -> str | None:
    for node in article.findall(".//ELocationID"):
        if node.attrib.get("EIdType") == id_type:
            return _inner_text(node)
    return None


def _article_ids(article: ET.Element) -> dict[str, str]:
    ids: dict[str, str] = {}
    for node in article.findall(".//ArticleIdList/ArticleId"):
        id_type = node.attrib.get("IdType")
        value = _inner_text(node)
        if id_type and value:
            ids[id_type] = value
    return ids


def _publication_types(article: ET.Element) -> list[str]:
    return [_inner_text(node) or "" for node in article.findall(".//PublicationTypeList/PublicationType") if _inner_text(node)]


def _mesh_headings(article: ET.Element) -> list[str]:
    return [_inner_text(node) or "" for node in article.findall(".//MeshHeading/DescriptorName") if _inner_text(node)]
