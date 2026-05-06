from __future__ import annotations

from datetime import datetime
import re

from .citations import extract_citation_mentions
from .evidence import classify_evidence, retrieve_evidence
from .models import (
    AuditResponse,
    AuditSummary,
    OnlineLookupResult,
    ParsedSentence,
    SentenceAudit,
    Source,
    Verdict,
)
from .parsing import parse_text
from .quotes import verify_quote
from .sources import score_reliability

ANALYSIS_SCOPE_VALUES = {"quoted", "cited", "data", "uncited"}
DEFAULT_ANALYSIS_SCOPE = set(ANALYSIS_SCOPE_VALUES)


def run_audit(
    text: str,
    sources: list[Source],
    online_lookup: list[OnlineLookupResult] | None = None,
    parsed_sentences: list[ParsedSentence] | None = None,
    analysis_scope: set[str] | None = None,
) -> AuditResponse:
    parsed = parsed_sentences or parse_text(text)
    current_year = datetime.now().year
    reliability = [score_reliability(source, current_year=current_year) for source in sources]
    sentence_audits: list[SentenceAudit] = []
    selected_scope = DEFAULT_ANALYSIS_SCOPE if analysis_scope is None else analysis_scope
    online_lookup_results = online_lookup or []
    online_source_ids = {lookup.source_id for lookup in online_lookup_results if lookup.source_id}
    online_lookup_attempted = bool(online_lookup_results)

    for sentence in parsed:
        needs_verification = sentence_matches_analysis_scope(sentence, selected_scope)
        eligible_sources = _eligible_sources_for_sentence(sentence, sources, online_source_ids, current_year)
        quote_checks = (
            [verify_quote(quote, eligible_sources) for quote in sentence.quotes]
            if needs_verification and sentence.quotes and "quoted" in selected_scope
            else []
        )
        evidence = retrieve_evidence(sentence.text, eligible_sources) if needs_verification else []
        evidence_label = classify_evidence(evidence)
        notes: list[str] = []

        has_citation = bool(sentence.citations or sentence.urls or sentence.dois)
        citation_issues = _citation_integrity_issues(sentence, current_year)
        notes.extend(citation_issues)

        if not needs_verification:
            verdict = Verdict.NO_CHECK_NEEDED
        elif citation_issues:
            verdict = Verdict.CITATION_MISSING
        elif needs_verification and not has_citation:
            verdict = Verdict.CITATION_MISSING
            notes.append("This sentence looks like a factual claim but no citation, URL, or DOI was detected.")
        elif evidence_label == "Likely Supported":
            verdict = Verdict.SUPPORTED
        elif evidence_label == "Needs Review":
            verdict = Verdict.NEEDS_REVIEW
        elif evidence_label == "Weak Evidence":
            verdict = Verdict.WEAK_EVIDENCE
        elif online_lookup_attempted and has_citation:
            verdict = Verdict.NEEDS_REVIEW
            notes.append("The citation was detected, but no matching source text could verify it. Review this source manually.")
        else:
            verdict = Verdict.NO_EVIDENCE
            notes.append("No strong evidence snippet was retrieved from the provided source text.")

        if quote_checks:
            notes.append("Direct quote verification is based on exact and fuzzy text matching.")

        sentence_audits.append(
            SentenceAudit(
                sentence=sentence,
                verdict=verdict,
                evidence=evidence,
                quote_checks=quote_checks,
                notes=notes,
            )
        )

    missing_citations = sum(1 for item in sentence_audits if item.verdict == Verdict.CITATION_MISSING)
    quote_checks_all = [check for item in sentence_audits for check in item.quote_checks]
    quotes_verified = sum(1 for check in quote_checks_all if check.score >= 98)
    summary = AuditSummary(
        total_sentences=len(parsed),
        likely_claims=sum(1 for sentence in parsed if sentence_matches_analysis_scope(sentence, selected_scope)),
        cited_sentences=sum(
            1
            for sentence in parsed
            if sentence_matches_analysis_scope(sentence, selected_scope)
            and (sentence.citations or sentence.urls or sentence.dois)
        ),
        missing_citations=missing_citations,
        quotes_found=len(quote_checks_all),
        quotes_verified=quotes_verified,
        sources_processed=len(sources),
    )

    return AuditResponse(
        summary=summary,
        sentences=sentence_audits,
        sources=sources,
        reliability=reliability,
        online_lookup=online_lookup_results,
    )


def sentence_matches_analysis_scope(sentence: ParsedSentence, analysis_scope: set[str] | None = None) -> bool:
    selected = DEFAULT_ANALYSIS_SCOPE if analysis_scope is None else analysis_scope
    return _needs_verification(sentence, selected)


def _needs_verification(sentence: ParsedSentence, analysis_scope: set[str]) -> bool:
    if not analysis_scope:
        return False
    if "quoted" in analysis_scope and sentence.quotes:
        return True
    if "cited" in analysis_scope and (sentence.citations or sentence.urls or sentence.dois):
        return True
    if "data" in analysis_scope and _looks_like_data_claim(sentence.text):
        return True
    if "uncited" in analysis_scope and sentence.likely_factual_claim and not (
        sentence.citations or sentence.urls or sentence.dois
    ):
        return True
    return False


def _looks_like_data_claim(text: str) -> bool:
    return bool(re.search(r"\b\d+(?:\.\d+)?\s?%|\b(?:19|20)\d{2}\b|\b\d+(?:\.\d+)?\b", text))


def _eligible_sources_for_sentence(
    sentence: ParsedSentence,
    sources: list[Source],
    online_source_ids: set[str],
    current_year: int,
) -> list[Source]:
    if not sources:
        return []
    if not sentence.citations and not sentence.dois and not sentence.urls:
        return [source for source in sources if source.id not in online_source_ids]

    user_supplied = [source for source in sources if source.id not in online_source_ids]
    compatible_online = [
        source
        for source in sources
        if source.id in online_source_ids and _source_matches_sentence_markers(sentence, source, current_year)
    ]
    return user_supplied + compatible_online


def _source_matches_sentence_markers(sentence: ParsedSentence, source: Source, current_year: int) -> bool:
    if sentence.dois:
        source_doi = _normalize_doi(source.metadata.doi)
        sentence_dois = {_normalize_doi(doi) for doi in sentence.dois}
        if source_doi and source_doi in sentence_dois:
            return True
        source_url = (source.metadata.url or "").lower()
        if any(doi and doi in source_url for doi in sentence_dois):
            return True
        source_text = source.full_text.lower()
        if any(doi and doi in source_text for doi in sentence_dois):
            return True
        return False

    mentions = [mention for mention in extract_citation_mentions(sentence.text) if mention.kind == "author_year"]
    if mentions:
        source_marker_text = _source_marker_text(source)
        for mention in mentions:
            if not mention.year or mention.year > current_year:
                continue
            if source.metadata.year and source.metadata.year != mention.year:
                continue
            if mention.author_hint and source.metadata.authors:
                if not any(mention.author_hint.lower() in author.lower() for author in source.metadata.authors):
                    continue
            elif mention.author_hint and mention.author_hint.lower() not in source_marker_text:
                continue
            if source.metadata.year is None and str(mention.year) not in source_marker_text:
                continue
            return True
        return False

    named_citations = [citation for citation in sentence.citations if _named_citation_terms(citation)]
    if named_citations:
        return any(_source_matches_named_citation(citation, source) for citation in named_citations)

    return True


def _citation_integrity_issues(sentence: ParsedSentence, current_year: int) -> list[str]:
    issues: list[str] = []
    for mention in extract_citation_mentions(sentence.text):
        if mention.year and mention.year > current_year:
            issues.append(
                f"Citation year {mention.year} is in the future relative to {current_year}, so this citation is not plausible."
            )
    return list(dict.fromkeys(issues))


def _normalize_doi(value: str | None) -> str:
    if not value:
        return ""
    cleaned = value.strip().lower()
    cleaned = cleaned.removeprefix("https://doi.org/").removeprefix("http://doi.org/").removeprefix("doi:")
    return re.sub(r"[.,;]+$", "", cleaned)


def _source_marker_text(source: Source) -> str:
    return " ".join(
        filter(
            None,
            [
                source.name,
                source.metadata.title or "",
                source.metadata.container or "",
                source.metadata.publisher or "",
                source.metadata.url or "",
                " ".join(source.metadata.authors),
                source.full_text[:30000],
            ],
        )
    ).lower()


def _source_matches_named_citation(citation: str, source: Source) -> bool:
    terms = _named_citation_terms(citation)
    if not terms:
        return True
    haystack = _source_marker_text(source)
    matched = [term for term in terms if term in haystack]
    return len(matched) >= min(2, len(terms))


def _named_citation_terms(citation: str) -> list[str]:
    if extract_citation_mentions(citation):
        return []
    stopwords = {
        "the",
        "report",
        "survey",
        "study",
        "review",
        "dataset",
        "statistics",
        "census",
        "guidance",
        "strategy",
        "white",
        "green",
        "paper",
    }
    terms = [
        term
        for term in re.findall(r"[a-z][a-z-]{2,}", citation.lower())
        if term not in stopwords
    ]
    return list(dict.fromkeys(terms))
