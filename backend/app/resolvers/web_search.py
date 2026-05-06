from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from rapidfuzz import fuzz

from app.models import OnlineLookupResult, Source, SourceCandidate, SourceMetadata, SourcePage


@dataclass(frozen=True)
class WebSearchHit:
    title: str
    url: str
    snippet: str = ""


class WebSearchResolver:
    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    async def search_source(
        self,
        query: str,
        source_id: str,
        *,
        title_hint: str | None = None,
        text_hint: str | None = None,
        rows: int = 5,
    ) -> tuple[Source | None, OnlineLookupResult]:
        try:
            hits = await self.search(query, rows=rows)
        except Exception as exc:
            return None, OnlineLookupResult(
                query=query,
                status="Lookup Failed",
                notes=[f"Web search failed: {exc}"],
            )
        candidates = [_candidate_from_hit(hit, _score_hit(hit, title_hint, text_hint)) for hit in hits]
        notes = ["Automatic web search used public DuckDuckGo results through a text reader."]
        if not hits:
            return None, OnlineLookupResult(query=query, status="Lookup Failed", notes=["No web search results were found."])

        best_source: Source | None = None
        best_candidate: SourceCandidate | None = None
        best_score = 0.0

        for hit, candidate in zip(hits[:3], candidates[:3], strict=False):
            text = await self.read_url(hit.url)
            if not text:
                continue
            if _is_direct_quote_search(title_hint, text_hint) and _quote_match_score(title_hint or "", text) < 0.82:
                continue
            if _dois(text_hint) and not any(doi in text.lower() for doi in _dois(text_hint)):
                continue
            content_score = _text_score(text, title_hint, text_hint)
            score = max(content_score, candidate.confidence if content_score >= 0.12 else 0.0)
            if score > best_score:
                best_score = score
                best_candidate = candidate.model_copy(update={"confidence": round(score, 3)})
                best_source = Source(
                    id=source_id,
                    kind="web",
                    name=hit.title or hit.url,
                    metadata=SourceMetadata(title=hit.title or title_hint, url=hit.url),
                    pages=[SourcePage(page_number=None, text=text)],
                )

        if best_source and best_score >= 0.18:
            if best_candidate:
                best_candidate.metadata = {**best_candidate.metadata, "confidence_label": _confidence_label(best_score)}
            return best_source, OnlineLookupResult(
                query=query,
                status="Full Text Parsed",
                candidates=candidates,
                selected_candidate=best_candidate,
                source_id=source_id,
                notes=notes + [f"Web source text was retrieved with {round(best_score * 100)}% relevance."],
            )

        return None, OnlineLookupResult(
            query=query,
            status="Metadata Found",
            candidates=candidates,
            selected_candidate=candidates[0] if candidates else None,
            notes=notes + ["Search found candidate pages, but no parseable page had enough overlap with the draft."],
        )

    async def search(self, query: str, rows: int = 5) -> list[WebSearchHit]:
        search_url = _jina_reader_url("https://html.duckduckgo.com/html/")
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(search_url, params={"q": query})
            response.raise_for_status()
        return _parse_duckduckgo_markdown(response.text, rows)

    async def read_url(self, url: str) -> str:
        reader_url = _jina_reader_url(url)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(reader_url)
                response.raise_for_status()
        except Exception:
            return ""
        text = response.text.strip()
        if "Warning: Target URL returned error" in text[:500]:
            return ""
        return _clean_reader_text(text)


def _parse_duckduckgo_markdown(markdown: str, rows: int) -> list[WebSearchHit]:
    hits: list[WebSearchHit] = []
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^## \[(?P<title>[^\]]+)\]\((?P<url>[^)]+)\)", line)
        if not match:
            continue
        url = _unwrap_duckduckgo_url(match.group("url"))
        if not url or _skip_url(url):
            continue
        snippet_lines: list[str] = []
        for following in lines[index + 1 : index + 5]:
            if following.startswith("## "):
                break
            cleaned = re.sub(r"\[[^\]]+\]\([^)]+\)", "", following).strip()
            if cleaned and not cleaned.startswith("[!"):
                snippet_lines.append(cleaned)
        hits.append(WebSearchHit(title=unescape(match.group("title")).strip(), url=url, snippet=" ".join(snippet_lines)))
        if len(hits) >= rows:
            break
    return hits


def _jina_reader_url(url: str) -> str:
    cleaned = url.strip()
    if cleaned.startswith("//"):
        cleaned = f"https:{cleaned}"
    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"http://{cleaned}"
    return f"https://r.jina.ai/{cleaned}"


def _unwrap_duckduckgo_url(url: str) -> str:
    parsed = urlparse(unescape(url))
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        raw = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(raw)
    return url


def _skip_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return any(blocked in host for blocked in ("duckduckgo.com", "google.com", "bing.com"))


def _candidate_from_hit(hit: WebSearchHit, score: float) -> SourceCandidate:
    return SourceCandidate(
        id=f"web:{hit.url}",
        provider="Web Search",
        title=hit.title,
        authors=[],
        year=None,
        doi=None,
        venue=urlparse(hit.url).netloc,
        publisher=urlparse(hit.url).netloc,
        url=hit.url,
        abstract=hit.snippet,
        confidence=round(score, 3),
        metadata={"confidence_label": _confidence_label(score)},
    )


def _score_hit(hit: WebSearchHit, title_hint: str | None, text_hint: str | None) -> float:
    haystack = f"{hit.title} {hit.snippet} {hit.url}"
    return max(_keyword_overlap(title_hint or "", haystack), _keyword_overlap(text_hint or "", haystack))


def _text_score(text: str, title_hint: str | None, text_hint: str | None) -> float:
    return max(_keyword_overlap(title_hint or "", text[:5000]), _keyword_overlap(text_hint or "", text[:8000]))


def _is_direct_quote_search(title_hint: str | None, text_hint: str | None) -> bool:
    if not title_hint or not text_hint:
        return False
    return title_hint in text_hint and len(_terms(title_hint)) >= 5


def _quote_match_score(quote: str, text: str) -> float:
    quote_terms = " ".join(_ordered_terms(quote))
    if not quote_terms:
        return 0.0
    text_sample = " ".join(_ordered_terms(text[:12000]))
    if quote_terms in text_sample:
        return 1.0
    return fuzz.partial_ratio(quote_terms, text_sample) / 100


def _dois(text: str | None) -> list[str]:
    if not text:
        return []
    return [match.rstrip(".,;").lower() for match in re.findall(r"\b10\.\d{4,9}/[-._;()/:a-z0-9]+\b", text, re.IGNORECASE)]


def _keyword_overlap(left: str, right: str) -> float:
    left_terms = _terms(left)
    right_terms = _terms(right)
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms)


def _terms(text: str) -> set[str]:
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "have",
        "into",
        "between",
        "among",
        "according",
    }
    return {term for term in re.findall(r"[a-z0-9]{3,}", text.lower()) if term not in stopwords}


def _ordered_terms(text: str) -> list[str]:
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "have",
        "into",
        "between",
        "among",
        "according",
    }
    return [term for term in re.findall(r"[a-z0-9]{3,}", text.lower()) if term not in stopwords]


def _confidence_label(score: float) -> str:
    if score >= 0.62:
        return "High"
    if score >= 0.35:
        return "Medium"
    if score >= 0.18:
        return "Low"
    return "Very Low"


def _clean_reader_text(text: str) -> str:
    text = re.sub(r"^Title:.*?\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"^URL Source:.*?\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"^Published Time:.*?\n", "", text, flags=re.MULTILINE)
    text = text.replace("Markdown Content:", "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()
