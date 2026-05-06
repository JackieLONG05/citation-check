from __future__ import annotations

import re


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)


def normalize_doi(value: str) -> str:
    doi = value.strip()
    doi = doi.removeprefix("https://doi.org/")
    doi = doi.removeprefix("http://doi.org/")
    doi = doi.removeprefix("doi:")
    return doi.strip().rstrip(".,;").lower()


def extract_dois(text: str) -> list[str]:
    return list(dict.fromkeys(normalize_doi(match.group(0)) for match in DOI_RE.finditer(text)))


def first(value: list[str] | tuple[str, ...] | None) -> str | None:
    if not value:
        return None
    return str(value[0])


def compact_text(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip()

