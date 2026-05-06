from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .audit import ANALYSIS_SCOPE_VALUES, DEFAULT_ANALYSIS_SCOPE, run_audit
from .citations import extract_citation_mentions, extract_reference_entries, parse_reference_entry
from .models import AuditResponse, OnlineLookupResult, ParsedSentence, Source, SourceCandidate, SourceMetadata, SourcePage, Verdict
from .parsing import parse_text
from .resolvers.pipeline import CHECK_MODE_DEEP, CHECK_MODE_FAST, CHECK_MODES, ResolverPipeline
from .resolvers.utils import extract_dois, normalize_doi
from .settings import frontend_origins
from .sources import source_from_doi, source_from_text, source_from_upload, source_from_url

app = FastAPI(title="Citation Check API", version="0.1.0")
ProgressCallback = Callable[[str, str, str | None], Awaitable[None]]
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

SEARCH_STOPWORDS = {
    "according",
    "adults",
    "after",
    "also",
    "among",
    "around",
    "before",
    "being",
    "between",
    "could",
    "criteria",
    "designed",
    "during",
    "england",
    "experience",
    "found",
    "government",
    "indicating",
    "into",
    "other",
    "report",
    "reported",
    "reports",
    "result",
    "results",
    "source",
    "study",
    "survey",
    "their",
    "there",
    "these",
    "those",
    "through",
    "united",
    "while",
    "with",
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/parse", response_model=list[ParsedSentence])
async def parse_draft(text: str = Form(...)) -> list[ParsedSentence]:
    if not text.strip():
        raise HTTPException(status_code=400, detail="Text is required.")
    return parse_text(text)


@app.post("/audit", response_model=AuditResponse)
async def audit(
    text: str = Form(...),
    source_text: str | None = Form(None),
    source_name: str | None = Form(None),
    source_url: str | None = Form(None),
    doi: str | None = Form(None),
    online_lookup: bool = Form(False),
    check_mode: str = Form(CHECK_MODE_DEEP),
    analysis_scope: list[str] | None = Form(None),
    reference_query: str | None = Form(None),
    files: list[UploadFile] | None = File(None),
) -> AuditResponse:
    return await _run_audit_request(
        text=text,
        source_text=source_text,
        source_name=source_name,
        source_url=source_url,
        doi=doi,
        online_lookup=online_lookup,
        check_mode=check_mode,
        analysis_scope=analysis_scope,
        reference_query=reference_query,
        files=files,
    )


@app.post("/audit/stream")
async def audit_stream(
    text: str = Form(...),
    source_text: str | None = Form(None),
    source_name: str | None = Form(None),
    source_url: str | None = Form(None),
    doi: str | None = Form(None),
    online_lookup: bool = Form(False),
    check_mode: str = Form(CHECK_MODE_DEEP),
    analysis_scope: list[str] | None = Form(None),
    reference_query: str | None = Form(None),
    files: list[UploadFile] | None = File(None),
) -> StreamingResponse:
    return StreamingResponse(
        _audit_stream_events(
            text=text,
            source_text=source_text,
            source_name=source_name,
            source_url=source_url,
            doi=doi,
            online_lookup=online_lookup,
            check_mode=check_mode,
            analysis_scope=analysis_scope,
            reference_query=reference_query,
            files=files,
        ),
        media_type="application/x-ndjson",
    )


async def _run_audit_request(
    *,
    text: str,
    source_text: str | None,
    source_name: str | None,
    source_url: str | None,
    doi: str | None,
    online_lookup: bool,
    check_mode: str,
    analysis_scope: list[str] | None,
    reference_query: str | None,
    files: list[UploadFile] | None,
    progress: ProgressCallback | None = None,
) -> AuditResponse:
    if not text.strip():
        raise HTTPException(status_code=400, detail="Text is required.")

    await _emit_progress(progress, "Parse draft", "active", "Parsing draft text.")
    parsed_sentences = parse_text(text)
    await _emit_progress(progress, "Parse draft", "complete", f"Parsed {len(parsed_sentences)} sentence(s).")
    selected_analysis_scope = _parse_analysis_scope(analysis_scope)
    selected_check_mode = _parse_check_mode(check_mode)
    sources = []
    lookup_results = []
    counter = 1

    for file in files or []:
        if file.filename:
            await _emit_progress(progress, "Uploaded sources", "active", f"Parsing {file.filename}.")
            sources.append(await source_from_upload(file, f"src{counter}"))
            await _emit_progress(progress, "Uploaded sources", "complete", f"Parsed {file.filename}.")
            counter += 1

    if source_text and source_text.strip():
        await _emit_progress(progress, "Uploaded sources", "active", "Parsing pasted source text.")
        sources.append(source_from_text(source_text, f"src{counter}", source_name or "pasted_source.txt"))
        await _emit_progress(progress, "Uploaded sources", "complete", "Parsed pasted source text.")
        counter += 1

    if source_url:
        try:
            await _emit_progress(progress, "Uploaded sources", "active", f"Fetching source URL {source_url}.")
            sources.append(await source_from_url(source_url, f"src{counter}"))
            await _emit_progress(progress, "Uploaded sources", "complete", "Fetched source URL.")
            counter += 1
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not fetch source_url: {exc}") from exc

    if online_lookup:
        pipeline = ResolverPipeline(
            timeout=8.0 if selected_check_mode == CHECK_MODE_FAST else 15.0,
            progress_callback=progress,
        )
        lookup_text = text
        has_parseable_source = _has_parseable_source(sources)
        if has_parseable_source:
            await _emit_progress(progress, "Evidence match", "active", "Checking uploaded sources before online lookup.")
            initial_audit = run_audit(
                text=text,
                sources=sources,
                parsed_sentences=parsed_sentences,
                analysis_scope=selected_analysis_scope,
            )
            unresolved_sentences = _sentences_needing_online(initial_audit)
            lookup_text = "\n".join(sentence.text for sentence in unresolved_sentences)
            await _emit_progress(
                progress,
                "Evidence match",
                "complete",
                f"{len(unresolved_sentences)} sentence(s) still need online lookup.",
            )

        if lookup_text.strip():
            detected_dois = extract_dois(lookup_text)
            detected_dois.extend(entry.doi for entry in extract_reference_entries(lookup_text) if entry.doi)
            if doi:
                detected_dois.insert(0, normalize_doi(doi))
            unique_dois = list(dict.fromkeys(detected_dois))
            candidate_jobs = _candidate_search_jobs(
                lookup_text,
                limit=3 if selected_check_mode == CHECK_MODE_FAST else 5,
                include_author_year=not bool(unique_dois),
            )
            if reference_query and reference_query.strip():
                candidate_jobs.insert(0, _reference_query_job(reference_query.strip(), lookup_text))
            web_jobs = _web_source_search_jobs(parse_text(lookup_text)) if selected_check_mode == CHECK_MODE_DEEP else []
            resolved_dois: set[str] = set()
            resolved_citation_keys: set[str] = set()
            online_sources_added = 0
            if not unique_dois and not candidate_jobs and not web_jobs:
                lookup_results.append(
                    OnlineLookupResult(
                        query="",
                        status="No DOI Detected",
                        notes=[
                            "Online lookup was enabled, but no DOI or searchable citation/reference was detected in the draft."
                        ],
                    )
                )
            for detected_doi in unique_dois:
                await _emit_progress(progress, "Provider lookup", "active", f"Resolving DOI {detected_doi}.")
                source, lookup = await pipeline.resolve_doi_to_source(
                    detected_doi,
                    f"src{counter}",
                    check_mode=selected_check_mode,
                )
                await _emit_progress(progress, "Provider lookup", "complete", f"DOI {detected_doi}: {lookup.status}.")
                lookup_results.append(lookup)
                normalized = normalize_doi(detected_doi)
                if lookup.selected_candidate or source:
                    resolved_dois.add(normalized)
                if source:
                    sources.append(source)
                    counter += 1
                    online_sources_added += 1
            for job in candidate_jobs:
                citation_key = _job_citation_key(job)
                if citation_key and citation_key in resolved_citation_keys:
                    continue
                await _emit_progress(progress, "Provider search", "active", f"Searching candidates for: {job.query}")
                source, lookup = await _search_and_resolve_candidate(
                    pipeline,
                    job,
                    f"src{counter}",
                    resolved_dois,
                    check_mode=selected_check_mode,
                )
                await _emit_progress(progress, "Provider search", "complete", f"Candidate search result: {lookup.status}.")
                lookup_results.append(lookup)
                if source:
                    sources.append(source)
                    if source.metadata.doi:
                        resolved_dois.add(normalize_doi(source.metadata.doi))
                    if citation_key:
                        resolved_citation_keys.add(citation_key)
                    counter += 1
                    online_sources_added += 1
            resolved_source_ids = {
                _source_dedupe_key(source): source.id
                for source in sources
                if _source_dedupe_key(source)
            }
            if online_sources_added == 0:
                for job in web_jobs:
                    await _emit_progress(progress, "Web discovery", "active", f"Searching web source for: {job.query}")
                    source, lookup = await pipeline.search_web_source(
                        job.query,
                        f"src{counter}",
                        title_hint=job.title_hint,
                        text_hint=job.text_hint,
                    )
                    lookup_results.append(lookup)
                    if source:
                        source_key = _source_dedupe_key(source)
                        added_source = False
                        if source_key and source_key in resolved_source_ids:
                            lookup.source_id = resolved_source_ids[source_key]
                        else:
                            sources.append(source)
                            added_source = True
                            if source_key:
                                resolved_source_ids[source_key] = source.id
                            counter += 1
                        if added_source:
                            online_sources_added += 1
                        if online_sources_added >= 5:
                            break

    if doi and not online_lookup:
        await _emit_progress(progress, "Provider lookup", "active", f"Fetching DOI source {doi}.")
        sources.append(await source_from_doi(doi, f"src{counter}"))
        await _emit_progress(progress, "Provider lookup", "complete", f"Fetched DOI source {doi}.")
    await _emit_progress(progress, "Evidence match", "active", "Matching evidence snippets.")
    await _emit_progress(progress, "Quote check", "active", "Checking direct quotes.")
    result = run_audit(
        text=text,
        sources=sources,
        online_lookup=lookup_results,
        parsed_sentences=parsed_sentences,
        analysis_scope=selected_analysis_scope,
    )
    await _emit_progress(progress, "Evidence match", "complete", "Evidence matching complete.")
    await _emit_progress(progress, "Quote check", "complete", "Quote checking complete.")
    return result


async def _audit_stream_events(**audit_kwargs) -> AsyncIterator[str]:
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def progress(task: str, status: str, message: str | None = None) -> None:
        event = {"type": "progress", "task": task, "status": status}
        if message:
            event["message"] = message
        await queue.put(event)

    async def run() -> None:
        try:
            result = await _run_audit_request(**audit_kwargs, progress=progress)
            await queue.put({"type": "result", "result": result.model_dump(mode="json")})
        except HTTPException as exc:
            await queue.put({"type": "error", "message": str(exc.detail)})
        except Exception as exc:
            await queue.put({"type": "error", "message": str(exc)})
        finally:
            await queue.put(None)

    task = asyncio.create_task(run())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield json.dumps(event) + "\n"
        await task
    finally:
        if not task.done():
            task.cancel()


async def _emit_progress(
    progress: ProgressCallback | None,
    task: str,
    status: str,
    message: str | None = None,
) -> None:
    if progress:
        await progress(task, status, message)


@dataclass(frozen=True)
class CandidateSearchJob:
    query: str
    title_hint: str | None = None
    author_hint: str | None = None
    year: int | None = None
    text_hint: str | None = None


def _reference_query_job(query: str, text: str) -> CandidateSearchJob:
    entry = parse_reference_entry(query)
    return CandidateSearchJob(
        query=query,
        title_hint=entry.title or query,
        author_hint=_author_hint(entry.authors[0]) if entry.authors else None,
        year=entry.year,
        text_hint=f"{query}\n{text}",
    )


def _candidate_search_jobs(text: str, limit: int = 5, include_author_year: bool = True) -> list[CandidateSearchJob]:
    references = [entry for entry in extract_reference_entries(text) if not entry.doi]
    jobs: list[CandidateSearchJob] = []
    seen: set[str] = set()

    for entry in references:
        query = entry.raw
        if not query:
            continue
        _add_job(
            jobs,
            seen,
            CandidateSearchJob(
                query=query,
                title_hint=entry.title,
                author_hint=_author_hint(entry.authors[0]) if entry.authors else None,
                year=entry.year,
                text_hint=entry.raw,
            ),
            limit,
        )

    if include_author_year and not references:
        parsed_sentences = parse_text(text)
        for sentence in parsed_sentences:
            context = sentence.text
            for mention in extract_citation_mentions(sentence.text):
                if mention.kind != "author_year" or not mention.author_hint or not mention.year:
                    continue
                context_terms = _source_search_query_from_sentence(context)
                query = f"{mention.author_hint} {mention.year} {context_terms}".strip()
                _add_job(
                    jobs,
                    seen,
                    CandidateSearchJob(
                        query=query,
                        author_hint=mention.author_hint,
                        year=mention.year,
                        text_hint=context,
                    ),
                    limit,
                )
        for mention in extract_citation_mentions(text):
            if mention.kind != "author_year" or not mention.author_hint or not mention.year:
                continue
            query = f"{mention.author_hint} {mention.year}"
            _add_job(
                jobs,
                seen,
                CandidateSearchJob(
                    query=query,
                    author_hint=mention.author_hint,
                    year=mention.year,
                    text_hint=text,
                ),
                limit,
            )

    return jobs


def _sentence_context(parsed_sentences, index: int) -> str:
    start = max(0, index - 2)
    end = min(len(parsed_sentences), index + 2)
    return " ".join(sentence.text for sentence in parsed_sentences[start:end])


def _has_parseable_source(sources: list[Source]) -> bool:
    return any(source.full_text.strip() for source in sources)


def _source_dedupe_key(source: Source) -> str:
    if source.metadata.url:
        return source.metadata.url.strip().lower()
    if source.metadata.doi:
        return normalize_doi(source.metadata.doi)
    return source.name.strip().lower()


def _sentences_needing_online(initial_audit: AuditResponse):
    unresolved_verdicts = {Verdict.NO_EVIDENCE, Verdict.NEEDS_REVIEW, Verdict.WEAK_EVIDENCE}
    sentences = []
    for item in initial_audit.sentences:
        if item.verdict == Verdict.NO_CHECK_NEEDED:
            continue
        has_searchable_marker = bool(item.sentence.citations or item.sentence.dois or item.sentence.urls or item.sentence.quotes)
        quote_not_verified = any(check.score < 98 for check in item.quote_checks)
        evidence_unresolved = item.verdict in unresolved_verdicts
        missing_cited_quote = item.verdict == Verdict.CITATION_MISSING and bool(item.sentence.quotes) and quote_not_verified
        if has_searchable_marker and (quote_not_verified or evidence_unresolved or missing_cited_quote):
            sentences.append(item.sentence)
    return sentences


def _web_source_search_jobs(parsed_sentences, limit: int = 8) -> list[CandidateSearchJob]:
    jobs: list[CandidateSearchJob] = []
    seen: set[str] = set()
    for sentence in parsed_sentences:
        citation_hint = " ".join(sentence.citations + sentence.dois + sentence.urls)
        for quote in sentence.quotes:
            query = f'"{quote}" {citation_hint}'.strip()
            _add_job(
                jobs,
                seen,
                CandidateSearchJob(query=query, title_hint=quote, text_hint=f"{sentence.text}\n{quote}"),
                limit,
            )
        if sentence.citations or sentence.dois or sentence.urls:
            for query in _web_query_variants(sentence):
                _add_job(
                    jobs,
                    seen,
                    CandidateSearchJob(
                        query=query,
                        title_hint=sentence.citations[0] if sentence.citations else None,
                        text_hint=sentence.text,
                    ),
                    limit,
                )
    return jobs


def _web_query_variants(sentence: ParsedSentence) -> list[str]:
    variants: list[str] = []
    citation_hints = sentence.citations + sentence.dois + sentence.urls
    short_phrases = _quoted_search_phrases(sentence.text)
    keyword_terms = _important_search_terms(sentence.text, max_terms=6)
    numeric_terms = _numeric_search_terms(sentence.text, max_terms=3)

    for citation in citation_hints:
        searchable_citation = _searchable_citation(citation)
        if not searchable_citation:
            continue
        _append_query(variants, f"{searchable_citation} {' '.join(short_phrases[:2])} {' '.join(numeric_terms[:2])}")
        _append_query(variants, f"{searchable_citation} {' '.join(keyword_terms[:5])} {' '.join(numeric_terms[:3])}")
        if not short_phrases and not keyword_terms:
            _append_query(variants, searchable_citation)

    fallback = _source_search_query_from_sentence(sentence.text)
    if fallback:
        _append_query(variants, fallback)
    return variants


def _append_query(queries: list[str], query: str) -> None:
    cleaned = _normalize_web_query(query)
    if cleaned and cleaned not in queries:
        queries.append(cleaned)


def _searchable_citation(citation: str) -> str:
    cleaned = re.sub(r"^\s*the\s+", "", citation, flags=re.IGNORECASE).strip(" .")
    return cleaned


def _quoted_search_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for match in re.finditer(r"\"([^\"\n]{3,80})\"|“([^”\n]{3,80})”|‘([^’\n]{3,80})’", text):
        phrase = next(group for group in match.groups() if group)
        if phrase.strip():
            phrases.append(phrase.strip())
    return phrases[:3]


def _important_search_terms(text: str, max_terms: int = 6) -> list[str]:
    without_citations = re.sub(r"\([^)]*(?:19|20)\d{2}[^)]*\)", " ", text)
    words = re.findall(r"[A-Za-z][A-Za-z-]{3,}", without_citations.lower())
    terms: list[str] = []
    for word in words:
        if word in SEARCH_STOPWORDS:
            continue
        normalized = word.strip("-")
        if normalized and normalized not in terms:
            terms.append(normalized)
        if len(terms) >= max_terms:
            break
    return terms


def _numeric_search_terms(text: str, max_terms: int = 3) -> list[str]:
    terms: list[str] = []
    for match in re.finditer(r"\b\d+(?:\.\d+)?%?\b", text):
        raw = match.group(0).rstrip("%")
        if re.fullmatch(r"(?:19|20)\d{2}", raw):
            continue
        if match.start() > 0 and text[match.start() - 1] == "/" and re.search(r"(?:19|20)\d{2}/$", text[: match.start()]):
            continue
        if raw not in terms:
            terms.append(raw)
        if len(terms) >= max_terms:
            break
    return terms


def _source_search_query_from_sentence(sentence: str) -> str:
    cleaned = re.sub(r"\([^)]*(?:19|20)\d{2}[^)]*\)", " ", sentence)
    cleaned = re.sub(r"\b[A-Z][A-Za-z'`-]+(?:\s+et\s+al\.)?\s*\((?:19|20)\d{2}[^)]*\)", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if len(cleaned) > 160:
        cleaned = cleaned[:160]
    return _normalize_web_query(cleaned)


def _normalize_web_query(query: str) -> str:
    cleaned = query.replace("“", " ").replace("”", " ").replace("‘", " ").replace("’", "'")
    cleaned = re.sub(r"\b(20\d{2})/(\d{2})\b", _expand_short_year, cleaned)
    cleaned = re.sub(r"[%]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,;:-")
    if len(cleaned) > 150:
        cleaned = cleaned[:150].rsplit(" ", 1)[0]
    return cleaned


def _expand_short_year(match: re.Match[str]) -> str:
    start = match.group(1)
    suffix = int(match.group(2))
    century = int(start[:2]) * 100
    expanded = century + suffix
    if expanded < int(start):
        expanded += 100
    return f"{start} {expanded}"


def _add_job(jobs: list[CandidateSearchJob], seen: set[str], job: CandidateSearchJob, limit: int) -> None:
    if len(jobs) >= limit:
        return
    key = re.sub(r"\W+", " ", f"{job.title_hint or ''} {job.author_hint or ''} {job.year or ''} {job.query}".lower()).strip()
    if not key or key in seen:
        return
    seen.add(key)
    jobs.append(job)


if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str) -> FileResponse:
        requested_file = FRONTEND_DIST / full_path
        if full_path and requested_file.is_file():
            return FileResponse(requested_file)
        return FileResponse(FRONTEND_DIST / "index.html")


def _job_citation_key(job: CandidateSearchJob) -> str | None:
    if job.title_hint or not job.author_hint or not job.year:
        return None
    return f"{job.author_hint.lower()}:{job.year}"


def _parse_analysis_scope(raw_values: list[str] | None) -> set[str]:
    if not raw_values:
        return set(DEFAULT_ANALYSIS_SCOPE)

    values: list[str] = []
    for raw in raw_values:
        if not raw or not raw.strip():
            continue
        stripped = raw.strip()
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, list):
            values.extend(item for item in decoded if isinstance(item, str))
        elif isinstance(decoded, str):
            values.append(decoded)
        else:
            values.extend(part.strip() for part in stripped.split(","))

    selected = {value for value in values if value}
    invalid = selected - ANALYSIS_SCOPE_VALUES
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid analysis_scope value(s): {', '.join(sorted(invalid))}.",
        )
    return selected


def _parse_check_mode(raw_value: str | None) -> str:
    if not raw_value or not raw_value.strip():
        return CHECK_MODE_DEEP
    selected = raw_value.strip().lower()
    if selected not in CHECK_MODES:
        raise HTTPException(status_code=422, detail="Invalid check_mode value. Use 'fast' or 'deep'.")
    return selected


async def _search_and_resolve_candidate(
    pipeline: ResolverPipeline,
    job: CandidateSearchJob,
    source_id: str,
    resolved_dois: set[str],
    *,
    check_mode: str = CHECK_MODE_DEEP,
) -> tuple[Source | None, OnlineLookupResult]:
    search_kwargs = {
        "title_hint": job.title_hint,
        "author_hint": job.author_hint,
        "year": job.year,
        "text_hint": job.text_hint,
    }
    if check_mode == CHECK_MODE_FAST:
        search_kwargs["check_mode"] = check_mode
    candidates = await pipeline.search_candidates(job.query, **search_kwargs)
    if not candidates:
        return None, OnlineLookupResult(
            query=job.query,
            status="Lookup Failed",
            notes=["No source candidate was found from citation/reference search."],
        )

    selected = candidates[0]
    label = selected.metadata.get("confidence_label", "Unknown")
    notes = [f"Candidate search confidence: {label} ({round(selected.confidence * 100)}%)."]
    candidate_doi = normalize_doi(selected.doi) if selected.doi else None

    auto_resolve = label == "High" or (label == "Medium" and selected.confidence >= 0.78)
    if candidate_doi and auto_resolve and candidate_doi not in resolved_dois:
        if check_mode == CHECK_MODE_FAST:
            source, lookup = await pipeline.resolve_doi_to_source(candidate_doi, source_id, check_mode=check_mode)
        else:
            source, lookup = await pipeline.resolve_doi_to_source(candidate_doi, source_id)
        lookup.query = job.query
        lookup.candidates = candidates
        lookup.selected_candidate = _selected_with_search_metadata(selected, lookup.selected_candidate)
        lookup.notes = notes + ["A sufficiently strong candidate was selected automatically from citation search."] + lookup.notes
        resolved_dois.add(candidate_doi)
        return source, lookup

    abstract_source = _source_from_candidate_abstract(selected, source_id) if auto_resolve else None
    if abstract_source and not (candidate_doi and candidate_doi in resolved_dois):
        if candidate_doi:
            resolved_dois.add(candidate_doi)
        notes.append("The top candidate was strong enough to attach its accessible abstract, but no full text was retrieved.")
        return abstract_source, OnlineLookupResult(
            query=job.query,
            status="Metadata Found",
            candidates=candidates,
            selected_candidate=selected,
            source_id=abstract_source.id,
            notes=notes,
        )

    if candidate_doi and candidate_doi in resolved_dois:
        notes.append("The top candidate DOI was already resolved elsewhere in this audit.")
    else:
        notes.append("Candidate search returned metadata, but confidence was not high enough to attach it as an evidence source.")

    return None, OnlineLookupResult(
        query=job.query,
        status="Metadata Found",
        candidates=candidates,
        selected_candidate=selected,
        notes=notes,
    )


def _source_from_candidate_abstract(candidate: SourceCandidate, source_id: str) -> Source | None:
    abstract = re.sub(r"<[^>]+>", " ", candidate.abstract or "")
    abstract = re.sub(r"\s+", " ", abstract).strip()
    if not abstract:
        return None
    return Source(
        id=source_id,
        kind="web",
        name=candidate.title or candidate.url or candidate.id,
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


def _selected_with_search_metadata(
    searched: SourceCandidate, resolved: SourceCandidate | None
) -> SourceCandidate:
    if not resolved:
        return searched
    return resolved.model_copy(
        update={
            "confidence": searched.confidence,
            "metadata": {
                **resolved.metadata,
                "confidence_label": searched.metadata.get("confidence_label"),
                "search_provider": searched.provider,
            },
        }
    )


def _author_hint(author: str) -> str:
    cleaned = re.sub(r"\bet\s+al\.?", "", author, flags=re.IGNORECASE).strip(" ,.")
    if "," in cleaned:
        return cleaned.split(",", 1)[0].strip(" ,.")
    parts = [part for part in re.split(r"\s+", cleaned) if part]
    return parts[-1].strip(" ,.") if parts else cleaned
