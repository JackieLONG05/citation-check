from __future__ import annotations

import re

import httpx

from app.models import FullTextLocation, SourceCandidate

from .utils import compact_text, normalize_doi


class OpenAireResolver:
    def __init__(self, access_token: str | None = None, timeout: float = 12.0) -> None:
        self.access_token = access_token
        self.timeout = timeout

    async def resolve_doi(self, doi: str) -> SourceCandidate | None:
        params = {
            "pid": normalize_doi(doi),
            "type": "publication",
            "page": "1",
            "pageSize": "1",
        }
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(
                "https://api.openaire.eu/graph/v2/researchProducts",
                params=params,
                headers=_headers(self.access_token),
            )
            response.raise_for_status()

        results = response.json().get("results", [])
        return _candidate_from_result(results[0]) if results else None

    async def search(
        self,
        query: str,
        *,
        title_hint: str | None = None,
        author_hint: str | None = None,
        year: int | None = None,
        rows: int = 5,
    ) -> list[SourceCandidate]:
        params = {
            "search": title_hint or query,
            "type": "publication",
            "page": "1",
            "pageSize": str(rows),
        }
        if author_hint:
            params["authorFullName"] = author_hint
        if year:
            params["fromPublicationDate"] = str(year)
            params["toPublicationDate"] = str(year)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(
                "https://api.openaire.eu/graph/v2/researchProducts",
                params=params,
                headers=_headers(self.access_token),
            )
            response.raise_for_status()

        return [_candidate_from_result(item) for item in response.json().get("results", [])]

    def full_text_location(self, candidate: SourceCandidate) -> FullTextLocation | None:
        for instance in candidate.metadata.get("instances") or []:
            access_label = ((instance.get("accessRight") or {}).get("label") or "").upper()
            if access_label and access_label not in {"OPEN", "EMBARGO", "RESTRICTED"}:
                continue
            for url in instance.get("urls") or []:
                if not url:
                    continue
                is_pdf = str(url).lower().endswith(".pdf")
                return FullTextLocation(
                    provider="OpenAIRE",
                    url=url,
                    kind="pdf" if is_pdf else "landing_page",
                    license=instance.get("license"),
                    is_open_access=access_label == "OPEN" or not access_label,
                )
        return None


def _candidate_from_result(data: dict) -> SourceCandidate:
    doi = _doi(data)
    return SourceCandidate(
        id=f"openaire:{data.get('id') or doi or compact_text(data.get('mainTitle')) or 'unknown'}",
        provider="OpenAIRE",
        title=compact_text(data.get("mainTitle")),
        authors=[author.get("fullName") for author in data.get("authors", []) if author.get("fullName")],
        year=_year(data.get("publicationDate")),
        doi=normalize_doi(doi) if doi else None,
        venue=compact_text(((data.get("container") or {}).get("name"))),
        publisher=compact_text(data.get("publisher")),
        url=(f"https://doi.org/{normalize_doi(doi)}" if doi else _first_instance_url(data)),
        abstract=_description(data),
        confidence=0.45,
        metadata={
            "pids": data.get("pids"),
            "instances": data.get("instances"),
            "bestAccessRight": data.get("bestAccessRight"),
            "openAccessColor": data.get("openAccessColor"),
            "sources": data.get("sources"),
            "indicators": data.get("indicators"),
        },
    )


def _doi(data: dict) -> str | None:
    for item in data.get("pids") or []:
        if str(item.get("scheme") or "").lower() == "doi" and item.get("value"):
            return item["value"]
    for instance in data.get("instances") or []:
        for item in instance.get("pids") or []:
            if str(item.get("scheme") or "").lower() == "doi" and item.get("value"):
                return item["value"]
    return None


def _first_instance_url(data: dict) -> str | None:
    for instance in data.get("instances") or []:
        urls = instance.get("urls") or []
        if urls:
            return urls[0]
    return None


def _description(data: dict) -> str | None:
    descriptions = data.get("descriptions") or []
    if not descriptions:
        return None
    text = re.sub(r"<[^>]+>", " ", descriptions[0])
    return compact_text(text)


def _year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return int(match.group(0)) if match else None


def _headers(access_token: str | None) -> dict[str, str] | None:
    return {"Authorization": f"Bearer {access_token}"} if access_token else None
