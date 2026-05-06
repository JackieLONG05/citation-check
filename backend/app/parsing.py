from __future__ import annotations

import re

from .models import ParsedSentence

URL_RE = re.compile(r"https?://[^\s)\]]+", re.IGNORECASE)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
PAGE_SUFFIX = r"(?:,\s*(?:p{1,2}\.?\s*)?\d+(?:\s*[-–]\s*\d+)?)?"
AUTHOR_YEAR_RE = re.compile(
    rf"\((?:[A-Z][A-Za-z'`-]+(?:\s+(?:&|and)\s+[A-Z][A-Za-z'`-]+)?|[A-Z][A-Za-z'`-]+\s+et\s+al\.)\s*,?\s*(?:19|20)\d{{2}}[a-z]?{PAGE_SUFFIX}\)"
)
NARRATIVE_CITATION_RE = re.compile(
    rf"\b[A-Z][A-Za-z'`-]+(?:\s+et\s+al\.)?\s*\((?:19|20)\d{{2}}[a-z]?{PAGE_SUFFIX}\)"
)
YEAR_ONLY_CITATION_RE = re.compile(rf"\((?:19|20)\d{{2}}[a-z]?{PAGE_SUFFIX}\)")
NUMERIC_CITATION_RE = re.compile(r"\[(?:\d{1,3}(?:\s*,\s*\d{1,3})*)\]")
NAMED_REPORT_CITATION_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z&'’/-]+(?:\s+|$)){1,10}"
    r"(?:Survey|Report|Review|Dataset|Statistics|Census|Study|Guidance|Strategy|White\s+Paper|Green\s+Paper)"
    r"\s+(?:20\d{2}(?:/\d{2})?|\d{4}/\d{2})"
    r"(?=\s+(?:reports?|reported|finds?|found|shows?|showed|suggests?|states?|indicates?|estimates?))"
)
QUOTE_RE = re.compile(r'"([^"\n]{4,})"|“([^”\n]{4,})”|‘([^’\n]{4,})’')
REFERENCE_HEADING_RE = re.compile(r"(?im)^\s*(references|bibliography|works cited)\s*$")

FACTUAL_MARKERS = {
    "according to",
    "study",
    "studies",
    "found",
    "shows",
    "suggests",
    "evidence",
    "data",
    "survey",
    "experiment",
    "reported",
    "demonstrates",
    "研究",
    "数据显示",
    "数据表明",
    "根据",
    "调查",
    "实验",
    "显示",
    "表明",
}

ABBREVIATIONS = ("et al.", "e.g.", "i.e.", "pp.", "p.", "Dr.", "Prof.", "Mr.", "Mrs.", "Ms.")


def split_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []

    protected = cleaned
    replacements: dict[str, str] = {}
    ellipsis_replacements = {"__ELLIPSIS_ASCII__": "...", "__ELLIPSIS_UNICODE__": "…"}
    protected = protected.replace("...", "__ELLIPSIS_ASCII__").replace("…", "__ELLIPSIS_UNICODE__")
    for index, abbreviation in enumerate(ABBREVIATIONS):
        token = f"__ABBR_{index}__"
        replacements[token] = abbreviation
        protected = protected.replace(abbreviation, abbreviation.replace(".", token))

    parts = re.split(r"(?<=[.!?。！？])\s+(?=[A-Z0-9“\"(\[]|[\u4e00-\u9fff])", protected)
    restored: list[str] = []
    for part in parts:
        sentence = part
        for token, abbreviation in replacements.items():
            sentence = sentence.replace(token, ".")
        for token, ellipsis in ellipsis_replacements.items():
            sentence = sentence.replace(token, ellipsis)
        if sentence.strip():
            restored.append(sentence.strip())
    return restored


def extract_quotes(text: str) -> list[str]:
    quotes: list[str] = []
    for match in QUOTE_RE.finditer(text):
        quote = next(group for group in match.groups() if group)
        quote = quote.strip()
        if _looks_like_direct_quote(quote):
            quotes.append(quote)
    return quotes


def extract_citations(text: str) -> list[str]:
    citations: list[str] = []
    for regex in (AUTHOR_YEAR_RE, NARRATIVE_CITATION_RE, NUMERIC_CITATION_RE, NAMED_REPORT_CITATION_RE):
        citations.extend(match.group(0) for match in regex.finditer(text))
    existing_spans = [
        match.span()
        for regex in (AUTHOR_YEAR_RE, NARRATIVE_CITATION_RE, NUMERIC_CITATION_RE, NAMED_REPORT_CITATION_RE)
        for match in regex.finditer(text)
    ]
    for match in YEAR_ONLY_CITATION_RE.finditer(text):
        if _overlaps(match.span(), existing_spans):
            continue
        if _preceding_author_hint(text[: match.start()]):
            citations.append(match.group(0))
    return list(dict.fromkeys(citations))


def is_likely_factual_claim(sentence: str) -> bool:
    lowered = sentence.lower()
    has_marker = any(marker in lowered for marker in FACTUAL_MARKERS)
    has_number = bool(re.search(r"\b\d+(?:\.\d+)?%?\b", sentence))
    has_named_year = bool(re.search(r"\b(?:19|20)\d{2}\b", sentence))
    has_citation = bool(extract_citations(sentence) or DOI_RE.search(sentence) or URL_RE.search(sentence))
    return has_marker or has_number or has_named_year or has_citation


def parse_text(text: str) -> list[ParsedSentence]:
    sentences = split_sentences(_body_text(text))
    parsed: list[ParsedSentence] = []
    for index, sentence in enumerate(sentences, start=1):
        parsed.append(
            ParsedSentence(
                id=f"s{index}",
                text=sentence,
                citations=extract_citations(sentence),
                urls=URL_RE.findall(sentence),
                dois=[doi.rstrip(".,;") for doi in DOI_RE.findall(sentence)],
                quotes=extract_quotes(sentence),
                likely_factual_claim=is_likely_factual_claim(sentence),
            )
        )
    return parsed


def _body_text(text: str) -> str:
    match = REFERENCE_HEADING_RE.search(text)
    if not match:
        return text
    return text[: match.start()].strip()


def _preceding_author_hint(text: str) -> str | None:
    candidates = [
        token
        for token in re.findall(r"\b[A-Z][A-Za-z'`-]{2,}\b", text[-520:])
        if token.lower() not in {"however", "according", "the", "this", "that", "they", "applications"}
    ]
    return candidates[-1] if candidates else None


def _looks_like_direct_quote(quote: str) -> bool:
    tokens = re.findall(r"[A-Za-z0-9%]+|[\u4e00-\u9fff]", quote)
    if len(tokens) >= 5:
        return True
    if len(quote) >= 48 and re.search(r"\b(is|are|was|were|has|have|had|can|could|should|would|will)\b", quote, re.IGNORECASE):
        return True
    return False


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < other[1] and other[0] < span[1] for other in spans)
