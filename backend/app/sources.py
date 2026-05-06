from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import fitz
import httpx
import trafilatura
from fastapi import UploadFile

from .models import ReliabilityScore, Source, SourceMetadata, SourcePage

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)


async def source_from_upload(file: UploadFile, source_id: str) -> Source:
    content = await file.read()
    filename = file.filename or f"source-{source_id}"
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf" or file.content_type == "application/pdf":
        return source_from_pdf_bytes(content, source_id, filename)

    text = content.decode("utf-8", errors="ignore")
    metadata = _metadata_from_text(text)
    return Source(id=source_id, kind="text", name=filename, metadata=metadata, pages=[SourcePage(page_number=None, text=text)])


def source_from_text(text: str, source_id: str, name: str = "pasted_source.txt") -> Source:
    metadata = _metadata_from_text(text)
    return Source(id=source_id, kind="text", name=name, metadata=metadata, pages=[SourcePage(page_number=None, text=text)])


def source_from_pdf_bytes(content: bytes, source_id: str, name: str) -> Source:
    pages = _extract_pdf_pages(content)
    metadata = _metadata_from_text("\n".join(page.text for page in pages[:3]))
    return Source(id=source_id, kind="pdf", name=name, metadata=metadata, pages=pages)


async def source_from_url(url: str, source_id: str) -> Source:
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()

    extracted = trafilatura.extract(response.text) or response.text
    title = _extract_html_title(response.text)
    metadata = SourceMetadata(title=title, url=url)
    return Source(
        id=source_id,
        kind="web",
        name=title or url,
        metadata=metadata,
        pages=[SourcePage(page_number=None, text=extracted)],
    )


async def source_from_doi(doi: str, source_id: str) -> Source:
    doi = doi.strip().removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    url = f"https://api.crossref.org/works/{doi}"
    metadata = SourceMetadata(doi=doi, url=f"https://doi.org/{doi}")
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        message = response.json().get("message", {})
        metadata = SourceMetadata(
            title=_first(message.get("title")),
            authors=_format_authors(message.get("author", [])),
            year=_extract_year(message),
            doi=message.get("DOI", doi),
            url=message.get("URL") or f"https://doi.org/{doi}",
            publisher=message.get("publisher"),
            container=_first(message.get("container-title")),
        )
    except Exception:
        pass

    name = metadata.title or metadata.doi or doi
    return Source(id=source_id, kind="doi", name=name, metadata=metadata, pages=[])


def score_reliability(source: Source, current_year: int = 2026) -> ReliabilityScore:
    score = 20
    reasons: list[str] = []
    metadata = source.metadata

    if source.full_text:
        score += 20
        reasons.append("Full source text is available.")
    if source.kind == "doi" or metadata.doi:
        score += 25
        reasons.append("Source has a DOI.")
    if metadata.authors:
        score += 10
        reasons.append("Author metadata is available.")
    if metadata.year:
        score += 10
        reasons.append(f"Publication year detected: {metadata.year}.")
    if metadata.container or metadata.publisher:
        score += 10
        reasons.append("Publication venue or publisher metadata is available.")
    if metadata.url and _looks_institutional(metadata.url):
        score += 15
        reasons.append("URL appears to be from an institutional or scholarly domain.")
    if source.kind == "web" and not metadata.year:
        score -= 10
        reasons.append("Web source has no detected publication year.")
    if not source.full_text and source.kind != "doi":
        score -= 15
        reasons.append("No source text was extracted.")

    score = max(0, min(100, score))
    level = "Unknown"
    if score >= 75:
        level = "High"
    elif score >= 45:
        level = "Medium"
    elif score > 0:
        level = "Low"

    freshness = "Unknown"
    if metadata.year:
        age = current_year - metadata.year
        if age <= 3:
            freshness = "Fresh"
        elif age <= 8:
            freshness = "Acceptable"
        else:
            freshness = "Possibly Outdated"
            reasons.append(f"Source is {age} years old; review freshness for fast-moving topics.")

    if not reasons:
        reasons.append("Insufficient metadata for a confident reliability score.")

    return ReliabilityScore(
        source_id=source.id,
        level=level,  # type: ignore[arg-type]
        score=score,
        freshness=freshness,  # type: ignore[arg-type]
        reasons=reasons,
    )


def _extract_pdf_pages(content: bytes) -> list[SourcePage]:
    pages: list[SourcePage] = []
    with fitz.open(stream=content, filetype="pdf") as doc:
        for page_index, page in enumerate(doc, start=1):
            pages.append(SourcePage(page_number=page_index, text=page.get_text("text")))
    return pages


def _first(value: list[str] | tuple[str, ...] | None) -> str | None:
    if not value:
        return None
    return str(value[0])


def _format_authors(authors: Iterable[dict]) -> list[str]:
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


def _extract_html_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _metadata_from_text(text: str) -> SourceMetadata:
    metadata = SourceMetadata()
    if not text:
        return metadata

    patterns = {
        "title": r"(?im)^\s*title\s*:\s*(.+)$",
        "authors": r"(?im)^\s*authors?\s*:\s*(.+)$",
        "year": r"(?im)^\s*year\s*:\s*((?:19|20)\d{2})\b",
        "url": r"(?im)^\s*url\s*:\s*(https?://\S+)\s*$",
    }

    title_match = re.search(patterns["title"], text)
    if title_match:
        metadata.title = title_match.group(1).strip()

    authors_match = re.search(patterns["authors"], text)
    if authors_match:
        metadata.authors = [author.strip() for author in re.split(r",| and ", authors_match.group(1)) if author.strip()]

    year_match = re.search(patterns["year"], text)
    if year_match:
        metadata.year = int(year_match.group(1))
    else:
        any_year = re.search(r"\b(?:19|20)\d{2}\b", text[:4000])
        if any_year:
            metadata.year = int(any_year.group(0))

    url_match = re.search(patterns["url"], text)
    if url_match:
        metadata.url = url_match.group(1).strip().rstrip(".,;")

    doi_match = DOI_RE.search(text)
    if doi_match:
        metadata.doi = doi_match.group(0).rstrip(".,;")
        metadata.url = f"https://doi.org/{metadata.doi}"

    return metadata


def _looks_institutional(url: str) -> bool:
    return any(token in url.lower() for token in (".edu", ".gov", ".ac.", "who.int", "oecd.org", "un.org", "nih.gov"))
