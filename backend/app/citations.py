from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from .resolvers.utils import extract_dois


class CitationMention(BaseModel):
    raw: str
    kind: Literal["author_year", "numeric"]
    author_hint: str | None = None
    year: int | None = None
    reference_numbers: list[int] = Field(default_factory=list)


class ReferenceEntry(BaseModel):
    raw: str
    index: int | None = None
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    url: str | None = None


AUTHOR_YEAR_PAREN_RE = re.compile(
    r"\((?P<body>[^)]*(?:19|20)\d{2}[a-z]?[^)]*)\)"
)
NARRATIVE_RE = re.compile(
    r"\b(?P<author>[A-Z][A-Za-z'`-]+(?:\s+et\s+al\.)?)\s*\((?P<year>(?:19|20)\d{2})[a-z]?\)"
)
YEAR_ONLY_PAREN_RE = re.compile(
    r"\((?P<year>(?:19|20)\d{2})[a-z]?(?:,\s*(?:p{1,2}\.?\s*)?\d+(?:\s*[-–]\s*\d+)?)?\)"
)
NUMERIC_RE = re.compile(r"\[(?P<body>\d{1,3}(?:\s*(?:,|-|–)\s*\d{1,3})*)\]")
REFERENCE_HEADING_RE = re.compile(r"(?im)^\s*(references|bibliography|works cited)\s*$")
URL_RE = re.compile(r"https?://[^\s)\]]+", re.IGNORECASE)


def extract_citation_mentions(text: str) -> list[CitationMention]:
    mentions: list[CitationMention] = []
    spans: list[tuple[int, int]] = []

    for match in NARRATIVE_RE.finditer(text):
        mentions.append(
            CitationMention(
                raw=match.group(0),
                kind="author_year",
                author_hint=_clean_author(match.group("author")),
                year=int(match.group("year")),
            )
        )
        spans.append(match.span())

    for match in AUTHOR_YEAR_PAREN_RE.finditer(text):
        if _overlaps(match.span(), spans):
            continue
        year_only_author = _preceding_author_hint(text[: match.start()])
        if year_only_author and _looks_year_only(match.group("body")):
            mentions.append(
                CitationMention(
                    raw=match.group(0),
                    kind="author_year",
                    author_hint=year_only_author,
                    year=int(re.search(r"(?:19|20)\d{2}", match.group("body")).group(0)),  # type: ignore[union-attr]
                )
            )
            spans.append(match.span())
            continue
        parenthetical_mentions = _mentions_from_parenthetical(match.group(0), match.group("body"))
        mentions.extend(parenthetical_mentions)
        if parenthetical_mentions:
            spans.append(match.span())

    for match in NUMERIC_RE.finditer(text):
        mentions.append(
            CitationMention(
                raw=match.group(0),
                kind="numeric",
                reference_numbers=_parse_reference_numbers(match.group("body")),
            )
        )

    return mentions


def extract_reference_entries(text: str) -> list[ReferenceEntry]:
    reference_text = _reference_section(text)
    if not reference_text:
        return []
    entries = _split_reference_entries(reference_text)
    return [parse_reference_entry(entry) for entry in entries]


def link_numeric_citations(
    mentions: list[CitationMention], references: list[ReferenceEntry]
) -> dict[int, ReferenceEntry]:
    by_index = {entry.index: entry for entry in references if entry.index is not None}
    linked: dict[int, ReferenceEntry] = {}
    for mention in mentions:
        for number in mention.reference_numbers:
            if number in by_index:
                linked[number] = by_index[number]
    return linked


def parse_reference_entry(raw: str) -> ReferenceEntry:
    cleaned = re.sub(r"\s+", " ", raw).strip()
    index = _reference_index(cleaned)
    without_index = re.sub(r"^\s*(?:\[\d+\]|\d+\.)\s*", "", cleaned)
    year = _first_year(without_index)
    doi = extract_dois(without_index)
    url_match = URL_RE.search(without_index)
    authors = _extract_reference_authors(without_index, year)
    title = _extract_reference_title(without_index, year)
    return ReferenceEntry(
        raw=cleaned,
        index=index,
        title=title,
        authors=authors,
        year=year,
        doi=doi[0] if doi else None,
        url=url_match.group(0).rstrip(".,;") if url_match else None,
    )


def _mentions_from_parenthetical(raw: str, body: str) -> list[CitationMention]:
    mentions: list[CitationMention] = []
    for part in re.split(r";", body):
        year_match = re.search(r"(?:19|20)\d{2}", part)
        if not year_match:
            continue
        author_part = part[: year_match.start()].strip(" ,")
        if not author_part:
            continue
        first_author = re.split(r"\s*(?:,|&|and)\s*", author_part)[0].strip()
        mentions.append(
            CitationMention(
                raw=raw if len(mentions) == 0 else part.strip(),
                kind="author_year",
                author_hint=_clean_author(first_author),
                year=int(year_match.group(0)),
            )
        )
    return mentions


def _parse_reference_numbers(body: str) -> list[int]:
    numbers: list[int] = []
    for part in re.split(r"\s*,\s*", body):
        if "-" in part or "–" in part:
            start_text, end_text = re.split(r"-|–", part, maxsplit=1)
            start, end = int(start_text), int(end_text)
            numbers.extend(range(start, end + 1))
        elif part.strip():
            numbers.append(int(part))
    return list(dict.fromkeys(numbers))


def _reference_section(text: str) -> str:
    match = REFERENCE_HEADING_RE.search(text)
    if not match:
        return ""
    return text[match.end() :].strip()


def _split_reference_entries(reference_text: str) -> list[str]:
    lines = [line.rstrip() for line in reference_text.splitlines() if line.strip()]
    entries: list[str] = []
    current: list[str] = []
    for line in lines:
        if re.match(r"^\s*(?:\[\d+\]|\d+\.)\s+", line) and current:
            entries.append(" ".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        entries.append(" ".join(current))
    if len(entries) == 1:
        entries = re.split(r"\n\s*\n", reference_text)
    return [entry.strip() for entry in entries if entry.strip()]


def _reference_index(entry: str) -> int | None:
    match = re.match(r"^\s*(?:\[(\d+)\]|(\d+)\.)", entry)
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _first_year(text: str) -> int | None:
    match = re.search(r"\b(?:19|20)\d{2}\b", text)
    return int(match.group(0)) if match else None


def _extract_reference_authors(text: str, year: int | None) -> list[str]:
    if not year:
        return []
    before_year = text.split(str(year), 1)[0]
    before_year = before_year.strip(" .(")
    if not before_year:
        return []
    parts = re.split(r"\s*,\s*(?=[A-Z][A-Za-z'`-]+)|\s+&\s+|\s+and\s+", before_year)
    return [part.strip(" .") for part in parts if part.strip(" .")]


def _extract_reference_title(text: str, year: int | None) -> str | None:
    if not year or str(year) not in text:
        return None
    after_year = text.split(str(year), 1)[1]
    after_year = after_year.strip("). ")
    if not after_year:
        return None
    parts = [part.strip() for part in re.split(r"\.\s+", after_year) if part.strip()]
    if not parts:
        return None
    title = parts[0]
    if title.lower().startswith("doi") or title.startswith("10."):
        return None
    return title.rstrip(".")


def _clean_author(author: str) -> str:
    return author.replace(" et al.", "").strip(" ,")


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < other[1] and other[0] < span[1] for other in spans)


def _looks_year_only(body: str) -> bool:
    return bool(YEAR_ONLY_PAREN_RE.fullmatch(f"({body})"))


def _preceding_author_hint(text: str) -> str | None:
    candidates = [
        token
        for token in re.findall(r"\b[A-Z][A-Za-z'`-]{2,}\b", text[-520:])
        if token.lower() not in {"however", "according", "the", "this", "that", "they", "applications"}
    ]
    return _clean_author(candidates[-1]) if candidates else None
