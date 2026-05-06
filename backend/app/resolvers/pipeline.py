from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable

import httpx
import trafilatura

from app.cache import EvidenceCache
from app.models import FullTextLocation, OnlineLookupResult, Source, SourceCandidate, SourceMetadata, SourcePage
from app.settings import base_api_enabled, contact_email, ncbi_api_key, openaire_access_token, openalex_api_key, semantic_scholar_api_key
from app.sources import source_from_pdf_bytes

from .arxiv import ArxivResolver
from .base import BaseResolver
from .crossref import CrossrefResolver
from .datacite import DataCiteResolver
from .doaj import DOAJResolver
from .europe_pmc import EuropePMCResolver
from .openalex import OpenAlexResolver
from .openaire import OpenAireResolver
from .pubmed import PubMedResolver
from .ranking import rank_candidates
from .semantic_scholar import SemanticScholarResolver
from .unpaywall import UnpaywallResolver
from .utils import normalize_doi
from .web_search import WebSearchResolver


CHECK_MODE_FAST = "fast"
CHECK_MODE_DEEP = "deep"
CHECK_MODES = {CHECK_MODE_FAST, CHECK_MODE_DEEP}
ProgressCallback = Callable[[str, str, str | None], Awaitable[None]]


class ResolverPipeline:
    def __init__(
        self,
        contact_email_value: str | None = None,
        timeout: float = 15.0,
        cache: EvidenceCache | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.contact_email = contact_email_value or contact_email()
        self.timeout = timeout
        self.cache = cache or EvidenceCache()
        self.progress_callback = progress_callback
        self.crossref = CrossrefResolver(mailto=self.contact_email, timeout=timeout)
        user_agent = f"CitationCheck/0.1 (mailto:{self.contact_email})" if self.contact_email else "CitationCheck/0.1"
        self.openalex = OpenAlexResolver(mailto=self.contact_email, api_key=openalex_api_key(), timeout=timeout)
        self.semantic_scholar = SemanticScholarResolver(api_key=semantic_scholar_api_key(), timeout=timeout)
        self.datacite = DataCiteResolver(user_agent=user_agent, timeout=timeout)
        self.doaj = DOAJResolver(timeout=timeout)
        self.europe_pmc = EuropePMCResolver(email=self.contact_email, timeout=timeout)
        self.pubmed = PubMedResolver(email=self.contact_email, api_key=ncbi_api_key(), timeout=timeout)
        self.openaire = OpenAireResolver(access_token=openaire_access_token(), timeout=timeout)
        self.arxiv = ArxivResolver(timeout=timeout)
        self.base = BaseResolver(enabled=base_api_enabled(), timeout=timeout)
        self.unpaywall = UnpaywallResolver(email=self.contact_email, timeout=timeout) if self.contact_email else None
        self.web_search = WebSearchResolver(timeout=timeout + 5)

    async def _emit_progress(self, task: str, status: str, message: str | None = None) -> None:
        if self.progress_callback:
            await self.progress_callback(task, status, message)

    async def resolve_doi_to_source(
        self,
        doi: str,
        source_id: str,
        *,
        check_mode: str = CHECK_MODE_DEEP,
    ) -> tuple[Source | None, OnlineLookupResult]:
        check_mode = _normalize_check_mode(check_mode)
        normalized = normalize_doi(doi)
        candidates: list[SourceCandidate] = []
        notes: list[str] = []

        recent_failure = self.cache.get_recent_failure(f"doi:{normalized}")
        if recent_failure:
            return None, OnlineLookupResult(
                query=normalized,
                status="Lookup Failed",
                notes=[f"Recent lookup failure is cached; retry later. Reason: {recent_failure}"],
            )

        crossref_candidate = await self._cached_candidate(normalized, "Crossref", self.crossref.resolve_doi, notes)
        if crossref_candidate:
            candidates.append(crossref_candidate)

        openalex_candidate = await self._cached_candidate(normalized, "OpenAlex", self.openalex.resolve_doi, notes)
        if openalex_candidate:
            candidates.append(openalex_candidate)

        semantic_candidate = None
        datacite_candidate = None
        europe_pmc_candidate = None
        pubmed_candidate = None
        doaj_candidate = None
        openaire_candidate = None
        if check_mode == CHECK_MODE_DEEP:
            semantic_candidate = await self._cached_candidate(
                normalized,
                "Semantic Scholar",
                self.semantic_scholar.resolve_doi,
                notes,
            )
            if semantic_candidate:
                candidates.append(semantic_candidate)

            datacite_candidate = await self._cached_candidate(normalized, "DataCite", self.datacite.resolve_doi, notes)
            if datacite_candidate:
                candidates.append(datacite_candidate)

            europe_pmc_candidate = await self._cached_candidate(normalized, "Europe PMC", self.europe_pmc.resolve_doi, notes)
            if europe_pmc_candidate:
                candidates.append(europe_pmc_candidate)

            pubmed_candidate = await self._cached_candidate(normalized, "PubMed", self.pubmed.resolve_doi, notes)
            if pubmed_candidate:
                candidates.append(pubmed_candidate)

            doaj_candidate = await self._cached_candidate(normalized, "DOAJ", self.doaj.resolve_doi, notes)
            if doaj_candidate:
                candidates.append(doaj_candidate)

            openaire_candidate = await self._cached_candidate(normalized, "OpenAIRE", self.openaire.resolve_doi, notes)
            if openaire_candidate:
                candidates.append(openaire_candidate)

        selected = _merge_candidates(normalized, candidates)
        if not selected:
            self.cache.set_failure(f"doi:{normalized}", "No provider returned source metadata.")
            return None, OnlineLookupResult(query=normalized, status="Lookup Failed", candidates=candidates, notes=notes)

        location = await self._find_full_text(
            normalized,
            notes=notes,
            check_mode=check_mode,
            openalex_candidate=openalex_candidate,
            semantic_candidate=semantic_candidate,
            europe_pmc_candidate=europe_pmc_candidate,
            doaj_candidate=doaj_candidate,
            datacite_candidate=datacite_candidate,
            openaire_candidate=openaire_candidate,
        )
        metadata_source = _source_from_candidate(source_id, selected)
        if check_mode == CHECK_MODE_FAST:
            if location:
                notes.append("Fast check mode found an open-access location but skipped full-text parsing.")
                return metadata_source, OnlineLookupResult(
                    query=normalized,
                    status="Open Access PDF Found" if location.kind == "pdf" else "Open Access Landing Page Found",
                    candidates=candidates,
                    selected_candidate=selected,
                    full_text_location=location,
                    source_id=metadata_source.id,
                    notes=notes,
                )
            notes.append("Fast check mode skipped deep metadata providers, web discovery, and full-text parsing.")
            return metadata_source, OnlineLookupResult(
                query=normalized,
                status="Full Text Unavailable",
                candidates=candidates,
                selected_candidate=selected,
                source_id=metadata_source.id,
                notes=notes,
            )
        if not location:
            web_source, web_lookup = await self.search_web_source(
                f'"{selected.title}" pdf' if selected.title else normalized,
                source_id,
                title_hint=selected.title,
                text_hint=selected.abstract,
            )
            if web_source:
                web_lookup.query = normalized
                web_lookup.candidates = candidates + web_lookup.candidates
                web_lookup.selected_candidate = selected
                web_lookup.notes = notes + ["No provider OA location was found; automatic web source discovery found parseable text."] + web_lookup.notes
                return web_source, web_lookup
            europe_pmc_source = self.europe_pmc.source_from_candidate(selected, source_id)
            if europe_pmc_source and selected.provider == "Europe PMC":
                notes.append("Only the Europe PMC abstract was accessible; no full text was retrieved.")
                return europe_pmc_source, OnlineLookupResult(
                    query=normalized,
                    status="Metadata Found",
                    candidates=candidates,
                    selected_candidate=selected,
                    source_id=europe_pmc_source.id,
                    notes=notes,
                )
            notes.append("Metadata was found, but no legal open-access full text location was found.")
            return metadata_source, OnlineLookupResult(
                query=normalized,
                status="Full Text Unavailable",
                candidates=candidates,
                selected_candidate=selected,
                source_id=metadata_source.id,
                notes=notes,
            )

        parsed_key = _parsed_source_key(normalized, location)
        parsed_source = self.cache.get_parsed_source(parsed_key)
        if parsed_source:
            parsed_source.id = source_id
            notes.append("Parsed source cache hit.")
        else:
            parsed_source = await _safe(self._source_from_location(location, source_id, selected), notes, "Full-text download or parsing failed")
            if parsed_source:
                self.cache.set_parsed_source(parsed_key, parsed_source)
        if not parsed_source:
            web_source, web_lookup = await self.search_web_source(
                f'"{selected.title}" pdf' if selected.title else normalized,
                source_id,
                title_hint=selected.title,
                text_hint=selected.abstract,
            )
            if web_source:
                web_lookup.query = normalized
                web_lookup.candidates = candidates + web_lookup.candidates
                web_lookup.selected_candidate = selected
                web_lookup.notes = notes + ["Provider full-text parsing failed; automatic web source discovery found parseable text."] + web_lookup.notes
                return web_source, web_lookup
            notes.append("Full-text location was found, but the content could not be parsed.")
            return metadata_source, OnlineLookupResult(
                query=normalized,
                status="Open Access PDF Found" if location.kind == "pdf" else "Open Access Landing Page Found",
                candidates=candidates,
                selected_candidate=selected,
                full_text_location=location,
                source_id=metadata_source.id,
                notes=notes,
            )

        return parsed_source, OnlineLookupResult(
            query=normalized,
            status="Full Text Parsed",
            candidates=candidates,
            selected_candidate=selected,
            full_text_location=location,
            source_id=parsed_source.id,
            notes=notes,
        )

    async def search_web_source(
        self,
        query: str,
        source_id: str,
        *,
        title_hint: str | None = None,
        text_hint: str | None = None,
    ) -> tuple[Source | None, OnlineLookupResult]:
        await self._emit_progress("Web discovery", "active", f"Searching web sources for: {query}")
        source, lookup = await self.web_search.search_source(
            query,
            source_id,
            title_hint=title_hint,
            text_hint=text_hint or query,
        )
        await self._emit_progress(
            "Web discovery",
            "complete",
            "Found parseable web source." if source else "No parseable web source found.",
        )
        return source, lookup

    async def search_candidates(
        self,
        query: str,
        *,
        title_hint: str | None = None,
        author_hint: str | None = None,
        year: int | None = None,
        doi: str | None = None,
        text_hint: str | None = None,
        rows: int = 5,
        check_mode: str = CHECK_MODE_DEEP,
    ) -> list[SourceCandidate]:
        check_mode = _normalize_check_mode(check_mode)
        notes: list[str] = []
        candidates: list[SourceCandidate] = []
        crossref = await self._search_provider(
            "Crossref",
            query,
            lambda: self.crossref.search_bibliographic(query, rows=rows),
            notes,
            "Crossref search failed",
        )
        openalex = await self._search_provider(
            "OpenAlex",
            query,
            lambda: self.openalex.search(query, rows=rows),
            notes,
            "OpenAlex search failed",
        )
        datacite = (
            await self._search_provider(
                "DataCite",
                query,
                lambda: self.datacite.search(query, rows=rows),
                notes,
                "DataCite search failed",
            )
            if check_mode == CHECK_MODE_DEEP
            else None
        )
        if crossref:
            candidates.extend(crossref)
        if openalex:
            candidates.extend(openalex)
        if datacite:
            candidates.extend(datacite)
        ranked = rank_candidates(
            _dedupe_candidates(candidates),
            title_hint=title_hint,
            author_hint=author_hint,
            year=year,
            doi=doi,
            text_hint=text_hint,
        )
        if check_mode == CHECK_MODE_DEEP and (not ranked or ranked[0].metadata.get("confidence_label") != "High"):
            semantic = await self._search_provider(
                "Semantic Scholar",
                query,
                lambda: self.semantic_scholar.search(query, rows=rows),
                notes,
                "Semantic Scholar search failed",
            )
            if semantic:
                candidates.extend(semantic)
                ranked = rank_candidates(
                    _dedupe_candidates(candidates),
                    title_hint=title_hint,
                    author_hint=author_hint,
                    year=year,
                    doi=doi,
                    text_hint=text_hint,
                )
        if check_mode == CHECK_MODE_DEEP and (not ranked or ranked[0].metadata.get("confidence_label") != "High"):
            europe_pmc = await self._search_provider(
                "Europe PMC",
                query,
                lambda: self.europe_pmc.search(query, title_hint=title_hint, text_hint=text_hint, rows=rows),
                notes,
                "Europe PMC search failed",
            )
            pubmed = await self._search_provider(
                "PubMed",
                query,
                lambda: self.pubmed.search(query, rows=rows),
                notes,
                "PubMed search failed",
            )
            doaj = await self._search_provider(
                "DOAJ",
                query,
                lambda: self.doaj.search(query, title_hint=title_hint, author_hint=author_hint, year=year, rows=rows),
                notes,
                "DOAJ search failed",
            )
            openaire = await self._search_provider(
                "OpenAIRE",
                query,
                lambda: self.openaire.search(query, title_hint=title_hint, author_hint=author_hint, year=year, rows=rows),
                notes,
                "OpenAIRE search failed",
            )
            arxiv = await self._search_provider(
                "arXiv",
                query,
                lambda: self.arxiv.search(query, title_hint=title_hint, text_hint=text_hint, rows=rows),
                notes,
                "arXiv search failed",
            )
            if europe_pmc:
                candidates.extend(europe_pmc)
            if pubmed:
                candidates.extend(pubmed)
            if doaj:
                candidates.extend(doaj)
            if openaire:
                candidates.extend(openaire)
            if arxiv:
                candidates.extend(arxiv)
            if europe_pmc or pubmed or doaj or openaire or arxiv:
                ranked = rank_candidates(
                    _dedupe_candidates(candidates),
                    title_hint=title_hint,
                    author_hint=author_hint,
                    year=year,
                    doi=doi,
                    text_hint=text_hint,
                )
        return ranked

    async def _search_provider(self, provider: str, query: str, factory, notes: list[str], message: str):
        await self._emit_progress(provider, "active", f"Searching {provider} for: {query}")
        result = await _provider_call(factory, notes, message)
        await self._emit_progress(
            provider,
            "complete",
            f"{provider} returned {len(result)} candidate(s)." if result else f"{provider} returned no candidates.",
        )
        return result

    async def _cached_candidate(self, doi: str, provider: str, resolver, notes: list[str]) -> SourceCandidate | None:
        cached = self.cache.get_candidate(doi, provider)
        if cached:
            notes.append(f"{provider} metadata cache hit.")
            await self._emit_progress(provider, "complete", f"{provider} metadata cache hit.")
            return cached

        await self._emit_progress(provider, "active", f"Looking up DOI {doi}.")
        candidate = await _provider_call(lambda: resolver(doi), notes, f"{provider} lookup failed")
        if candidate:
            self.cache.set_candidate(doi, provider, candidate)
        await self._emit_progress(
            provider,
            "complete",
            f"{provider} returned metadata." if candidate else f"{provider} returned no metadata.",
        )
        return candidate

    async def _find_full_text(
        self,
        doi: str,
        *,
        notes: list[str],
        check_mode: str,
        openalex_candidate: SourceCandidate | None,
        semantic_candidate: SourceCandidate | None,
        europe_pmc_candidate: SourceCandidate | None,
        doaj_candidate: SourceCandidate | None,
        datacite_candidate: SourceCandidate | None,
        openaire_candidate: SourceCandidate | None,
    ) -> FullTextLocation | None:
        cached = self.cache.get_full_text_location(doi)
        if cached:
            notes.append("Full-text location cache hit.")
            return cached

        location: FullTextLocation | None = None
        if check_mode == CHECK_MODE_DEEP and self.unpaywall:
            await self._emit_progress("Unpaywall", "active", f"Looking for open-access full text for {doi}.")
            location = await _provider_call(lambda: self.unpaywall.find_full_text(doi), notes, "Unpaywall lookup failed")
            await self._emit_progress(
                "Unpaywall",
                "complete",
                "Found open-access full text." if location else "No open-access full text returned.",
            )
            if location:
                self.cache.set_full_text_location(doi, location)
                return location
        if openalex_candidate:
            location = self.openalex.full_text_location(openalex_candidate)
        if location:
            self.cache.set_full_text_location(doi, location)
            return location

        if semantic_candidate:
            location = self.semantic_scholar.full_text_location(semantic_candidate)
        if location:
            self.cache.set_full_text_location(doi, location)
            return location

        if doaj_candidate:
            location = self.doaj.full_text_location(doaj_candidate)
        if location:
            self.cache.set_full_text_location(doi, location)
            return location

        if europe_pmc_candidate:
            location = self.europe_pmc.full_text_location(europe_pmc_candidate)
        if location:
            self.cache.set_full_text_location(doi, location)
            return location

        if openaire_candidate:
            location = self.openaire.full_text_location(openaire_candidate)
        if location:
            self.cache.set_full_text_location(doi, location)
        return location

    async def _source_from_location(self, location: FullTextLocation, source_id: str, candidate: SourceCandidate) -> Source:
        await self._emit_progress("Full-text parsing", "active", f"Fetching {location.url}")
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(location.url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")

        if location.kind == "pdf" or "pdf" in content_type.lower() or location.url.lower().endswith(".pdf"):
            source = source_from_pdf_bytes(response.content, source_id, _source_name(candidate, "online_source.pdf"))
        else:
            extracted = trafilatura.extract(response.text) or response.text
            source = Source(
                id=source_id,
                kind="web",
                name=_source_name(candidate, location.url),
                metadata=_metadata_from_candidate(candidate),
                pages=[SourcePage(page_number=None, text=extracted)],
            )
        source.metadata = _metadata_from_candidate(candidate)
        await self._emit_progress("Full-text parsing", "complete", "Parsed provider full text.")
        return source


def _normalize_check_mode(check_mode: str | None) -> str:
    if not check_mode:
        return CHECK_MODE_DEEP
    normalized = check_mode.strip().lower()
    if normalized in CHECK_MODES:
        return normalized
    return CHECK_MODE_DEEP


async def _safe(awaitable, notes: list[str], message: str):
    try:
        return await awaitable
    except Exception as exc:
        notes.append(f"{message}: {exc}")
        return None


async def _provider_call(factory, notes: list[str], message: str, attempts: int = 2, backoff_seconds: float = 0.25):
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await factory()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts:
                await asyncio.sleep(backoff_seconds * attempt)
    if last_exc:
        notes.append(f"{message}: {last_exc}")
    return None


def _merge_candidates(doi: str, candidates: list[SourceCandidate]) -> SourceCandidate | None:
    if not candidates:
        return None
    crossref = next((candidate for candidate in candidates if candidate.provider == "Crossref"), None)
    openalex = next((candidate for candidate in candidates if candidate.provider == "OpenAlex"), None)
    semantic = next((candidate for candidate in candidates if candidate.provider == "Semantic Scholar"), None)
    datacite = next((candidate for candidate in candidates if candidate.provider == "DataCite"), None)
    europe_pmc = next((candidate for candidate in candidates if candidate.provider == "Europe PMC"), None)
    pubmed = next((candidate for candidate in candidates if candidate.provider == "PubMed"), None)
    doaj = next((candidate for candidate in candidates if candidate.provider == "DOAJ"), None)
    openaire = next((candidate for candidate in candidates if candidate.provider == "OpenAIRE"), None)
    selected = crossref or openalex or semantic or datacite or europe_pmc or pubmed or doaj or openaire
    if selected and openalex:
        selected.metadata = {**selected.metadata, "openalex": openalex.metadata}
        if not selected.title:
            selected.title = openalex.title
        if not selected.authors:
            selected.authors = openalex.authors
        if not selected.year:
            selected.year = openalex.year
        if not selected.venue:
            selected.venue = openalex.venue
        if not selected.url:
            selected.url = openalex.url
    for provider_key, candidate in (
        ("semantic_scholar", semantic),
        ("datacite", datacite),
        ("europe_pmc", europe_pmc),
        ("pubmed", pubmed),
        ("doaj", doaj),
        ("openaire", openaire),
    ):
        if not selected or not candidate:
            continue
        selected.metadata = {**selected.metadata, provider_key: candidate.metadata}
        if not selected.title:
            selected.title = candidate.title
        if not selected.authors:
            selected.authors = candidate.authors
        if not selected.year:
            selected.year = candidate.year
        if not selected.venue:
            selected.venue = candidate.venue
        if not selected.url:
            selected.url = candidate.url
        if not selected.abstract:
            selected.abstract = candidate.abstract
    if selected and not selected.doi:
        selected.doi = doi
    return selected


def _source_from_candidate(source_id: str, candidate: SourceCandidate) -> Source:
    abstract = _clean_abstract(candidate.abstract)
    return Source(
        id=source_id,
        kind="doi",
        name=_source_name(candidate, candidate.doi or source_id),
        metadata=_metadata_from_candidate(candidate),
        pages=[SourcePage(page_number=None, text=abstract)] if abstract else [],
    )


def _metadata_from_candidate(candidate: SourceCandidate) -> SourceMetadata:
    return SourceMetadata(
        title=candidate.title,
        authors=candidate.authors,
        year=candidate.year,
        doi=candidate.doi,
        url=candidate.url,
        publisher=candidate.publisher,
        container=candidate.venue,
    )


def _source_name(candidate: SourceCandidate, fallback: str) -> str:
    return candidate.title or candidate.doi or fallback


def _parsed_source_key(doi: str, location: FullTextLocation) -> str:
    return f"{doi}:{location.provider}:{location.kind}:{location.url}"


def _clean_abstract(abstract: str | None) -> str:
    if not abstract:
        return ""
    text = re.sub(r"<[^>]+>", " ", abstract)
    return re.sub(r"\s+", " ", text).strip()


def _dedupe_candidates(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    seen: set[str] = set()
    deduped: list[SourceCandidate] = []
    for candidate in candidates:
        key = (candidate.doi or candidate.id).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped
