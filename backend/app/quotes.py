from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from .models import QuoteCheck, QuoteStatus, Source


def verify_quote(quote: str, sources: list[Source]) -> QuoteCheck:
    normalized_quote = _normalize_quote(quote)
    has_quote_modifier = _has_quote_modifier(quote)
    best: QuoteCheck | None = None

    for source in sources:
        for page in source.pages:
            normalized_page = _normalize(page.text)
            if normalized_quote and normalized_quote in normalized_page:
                score = 94.0 if has_quote_modifier else 100.0
                return QuoteCheck(
                    quote=quote,
                    status=_status_from_score(score),
                    score=score,
                    source_id=source.id,
                    source_name=source.name,
                    page_number=page.page_number,
                    context=_context_for_quote(quote, page.text),
                )
            if _ellipsis_match(quote, normalized_page):
                return QuoteCheck(
                    quote=quote,
                    status=QuoteStatus.SLIGHTLY_MODIFIED,
                    score=94.0,
                    source_id=source.id,
                    source_name=source.name,
                    page_number=page.page_number,
                    context=_context_for_quote(quote, page.text),
                )

            context = _best_fuzzy_context(quote, page.text)
            score = float(fuzz.partial_ratio(normalized_quote, normalized_page)) if normalized_page else 0.0
            score = _apply_numeric_penalty(score, quote, context)
            if best is None or score > best.score:
                best = QuoteCheck(
                    quote=quote,
                    status=_status_from_score(score),
                    score=round(score, 1),
                    source_id=source.id,
                    source_name=source.name,
                    page_number=page.page_number,
                    context=context,
                )

    if best:
        return best

    return QuoteCheck(quote=quote, status=QuoteStatus.NOT_FOUND, score=0.0)


def _status_from_score(score: float) -> QuoteStatus:
    if score >= 98:
        return QuoteStatus.VERIFIED
    if score >= 88:
        return QuoteStatus.SLIGHTLY_MODIFIED
    if score >= 70:
        return QuoteStatus.MISMATCH
    return QuoteStatus.NOT_FOUND


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(
        str.maketrans(
            {
                "“": '"',
                "”": '"',
                "‘": "'",
                "’": "'",
                "‐": " ",
                "‑": " ",
                "‒": " ",
                "–": " ",
                "—": " ",
                "-": " ",
            }
        )
    )
    text = re.sub(r"[^\w.%]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _normalize_quote(text: str) -> str:
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"(?:\.{3}|…)", " ", text)
    return _normalize(text)


def _has_quote_modifier(text: str) -> bool:
    return bool(re.search(r"\[[^\]]+\]|(?:\.{3}|…)", text))


def _ellipsis_match(quote: str, normalized_page: str) -> bool:
    if not re.search(r"(?:\.{3}|…)", quote):
        return False
    parts = [_normalize(part) for part in re.split(r"(?:\.{3}|…)", re.sub(r"\[[^\]]+\]", " ", quote)) if _normalize(part)]
    if len(parts) < 2:
        return False
    cursor = 0
    for part in parts:
        index = normalized_page.find(part, cursor)
        if index == -1:
            return False
        cursor = index + len(part)
    return True


def _apply_numeric_penalty(score: float, quote: str, context: str | None) -> float:
    if not context:
        return score
    quote_numbers = _numbers(quote)
    context_numbers = _numbers(context)
    if quote_numbers and context_numbers and not quote_numbers.issubset(context_numbers):
        return min(score, 84.0)
    return score


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\b\d+(?:\.\d+)?%?\b", text))


def _context_for_quote(quote: str, text: str, radius: int = 220) -> str:
    lowered_text = _normalize(text)
    lowered_quote = _normalize_quote(quote)
    index = lowered_text.find(lowered_quote)
    if index == -1:
        return _best_fuzzy_context(quote, text, radius)
    return _best_fuzzy_context(quote, text, radius)


def _best_fuzzy_context(quote: str, text: str, radius: int = 220) -> str | None:
    if not text:
        return None
    words = text.split()
    quote_word_count = max(8, len(quote.split()))
    best_score = -1
    best_window = ""
    stride = max(1, quote_word_count // 2)
    for start in range(0, len(words), stride):
        window = " ".join(words[start : start + quote_word_count + 12])
        score = fuzz.partial_ratio(_normalize_quote(quote), _normalize(window))
        if score > best_score:
            best_score = score
            best_window = window
    return best_window[: radius * 2] if best_window else None
