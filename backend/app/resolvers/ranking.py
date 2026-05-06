from __future__ import annotations

import re

from rapidfuzz import fuzz

from app.models import SourceCandidate


def rank_candidates(
    candidates: list[SourceCandidate],
    *,
    title_hint: str | None = None,
    author_hint: str | None = None,
    year: int | None = None,
    doi: str | None = None,
    text_hint: str | None = None,
) -> list[SourceCandidate]:
    ranked: list[SourceCandidate] = []
    for candidate in candidates:
        score = _candidate_score(candidate, title_hint=title_hint, author_hint=author_hint, year=year, doi=doi, text_hint=text_hint)
        copy = candidate.model_copy(deep=True)
        copy.confidence = round(score, 3)
        copy.metadata = {**copy.metadata, "confidence_label": _confidence_label(score)}
        ranked.append(copy)

    ranked.sort(key=lambda item: item.confidence, reverse=True)
    if len(ranked) >= 2 and ranked[0].confidence - ranked[1].confidence < 0.05:
        ranked[0].metadata["confidence_label"] = "Ambiguous"
        ranked[1].metadata["confidence_label"] = "Ambiguous"
    return ranked


def _candidate_score(
    candidate: SourceCandidate,
    *,
    title_hint: str | None,
    author_hint: str | None,
    year: int | None,
    doi: str | None,
    text_hint: str | None,
) -> float:
    score = 0.0
    weight = 0.0

    if doi:
        weight += 0.35
        score += 0.35 if candidate.doi and candidate.doi.lower() == doi.lower() else 0.0

    if title_hint:
        weight += 0.3
        score += 0.3 * _similarity(title_hint, candidate.title)

    if author_hint:
        weight += 0.15
        score += 0.15 * _author_score(author_hint, candidate.authors)

    if year:
        weight += 0.12
        score += 0.12 * _year_score(year, candidate.year)

    if text_hint:
        weight += 0.08
        score += 0.08 * _keyword_overlap(text_hint, " ".join(filter(None, [candidate.title, candidate.abstract, candidate.venue])))

    return score / weight if weight else candidate.confidence


def _confidence_label(score: float) -> str:
    if score >= 0.82:
        return "High"
    if score >= 0.62:
        return "Medium"
    if score >= 0.4:
        return "Low"
    return "Very Low"


def _similarity(left: str, right: str | None) -> float:
    if not right:
        return 0.0
    return fuzz.token_set_ratio(left.lower(), right.lower()) / 100


def _author_score(author_hint: str, authors: list[str]) -> float:
    if not authors:
        return 0.0
    hint = author_hint.lower()
    first_author = authors[0].lower()
    if hint in first_author:
        return 1.0
    return fuzz.partial_ratio(hint, first_author) / 100


def _year_score(expected: int, actual: int | None) -> float:
    if actual is None:
        return 0.0
    if actual == expected:
        return 1.0
    if abs(actual - expected) == 1:
        return 0.45
    return 0.0


def _keyword_overlap(left: str, right: str) -> float:
    left_terms = set(re.findall(r"\w+", left.lower()))
    right_terms = set(re.findall(r"\w+", right.lower()))
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms)

