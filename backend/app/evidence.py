from __future__ import annotations

import math
import re
from collections import Counter

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .models import EvidenceSnippet, Source


def retrieve_evidence(claim: str, sources: list[Source], top_k: int = 3) -> list[EvidenceSnippet]:
    chunks = _source_chunks(sources)
    if not chunks:
        return []

    texts = [chunk["text"] for chunk in chunks]
    try:
        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1)
        matrix = vectorizer.fit_transform(texts + [claim])
        tfidf_scores = [float(score) for score in cosine_similarity(matrix[-1], matrix[:-1]).flatten()]
    except Exception:
        tfidf_scores = [_keyword_overlap(claim, text) for text in texts]

    bm25_scores = _bm25_scores(claim, texts)
    scores = _hybrid_scores(tfidf_scores, bm25_scores)
    normalized_bm25 = _normalize_scores(bm25_scores)

    ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)[:top_k]
    snippets: list[EvidenceSnippet] = []
    for index, raw_score in ranked:
        score = float(raw_score)
        if math.isnan(score) or score <= 0:
            continue
        chunk = chunks[index]
        snippets.append(
            EvidenceSnippet(
                source_id=chunk["source_id"],
                source_name=chunk["source_name"],
                page_number=chunk["page_number"],
                text=chunk["text"],
                relevance_score=round(score, 3),
                explanation=_score_explanation(claim, chunk["text"], tfidf_scores[index], normalized_bm25[index], score),
            )
        )
    return snippets


def classify_evidence(snippets: list[EvidenceSnippet]) -> str:
    if not snippets:
        return "No Evidence Found"
    best = snippets[0].relevance_score
    if best >= 0.34:
        return "Likely Supported"
    if best >= 0.18:
        return "Needs Review"
    if best >= 0.08:
        return "Weak Evidence"
    return "No Evidence Found"


def _source_chunks(sources: list[Source], max_chars: int = 900) -> list[dict]:
    chunks: list[dict] = []
    for source in sources:
        for page in source.pages:
            for paragraph in _paragraphs(page.text):
                if len(paragraph) <= max_chars:
                    chunks.append(_chunk(source, page.page_number, paragraph))
                    continue
                for start in range(0, len(paragraph), max_chars):
                    chunks.append(_chunk(source, page.page_number, paragraph[start : start + max_chars]))
    return chunks


def _paragraphs(text: str) -> list[str]:
    rough = re.split(r"\n{2,}|(?<=[.!?。！？])\s+(?=[A-Z0-9“\"(\[]|[\u4e00-\u9fff])", text)
    return [re.sub(r"\s+", " ", item).strip() for item in rough if len(item.strip()) >= 40]


def _chunk(source: Source, page_number: int | None, text: str) -> dict:
    return {
        "source_id": source.id,
        "source_name": source.name,
        "page_number": page_number,
        "text": text,
    }


def _keyword_overlap(claim: str, text: str) -> float:
    claim_terms = set(re.findall(r"\w+", claim.lower()))
    text_terms = set(re.findall(r"\w+", text.lower()))
    if not claim_terms or not text_terms:
        return 0.0
    return len(claim_terms & text_terms) / len(claim_terms)


def _hybrid_scores(tfidf_scores: list[float], bm25_scores: list[float]) -> list[float]:
    normalized_bm25 = _normalize_scores(bm25_scores)
    return [
        (0.65 * tfidf) + (0.35 * bm25)
        for tfidf, bm25 in zip(tfidf_scores, normalized_bm25, strict=False)
    ]


def _bm25_scores(query: str, documents: list[str], k1: float = 1.5, b: float = 0.75) -> list[float]:
    tokenized_docs = [_tokens(document) for document in documents]
    query_tokens = _tokens(query)
    if not tokenized_docs or not query_tokens:
        return [0.0 for _ in documents]

    doc_count = len(tokenized_docs)
    doc_lengths = [len(document) for document in tokenized_docs]
    avg_doc_length = sum(doc_lengths) / doc_count if doc_count else 0.0
    document_frequency: Counter[str] = Counter()
    for document in tokenized_docs:
        document_frequency.update(set(document))

    scores: list[float] = []
    for document, doc_length in zip(tokenized_docs, doc_lengths, strict=False):
        term_frequency = Counter(document)
        score = 0.0
        for term in query_tokens:
            if term not in term_frequency:
                continue
            idf = math.log(1 + ((doc_count - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)))
            numerator = term_frequency[term] * (k1 + 1)
            denominator = term_frequency[term] + k1 * (1 - b + b * (doc_length / avg_doc_length if avg_doc_length else 0))
            score += idf * (numerator / denominator)
        scores.append(score)
    return scores


def _normalize_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    max_score = max(scores)
    if max_score <= 0:
        return [0.0 for _ in scores]
    return [score / max_score for score in scores]


def _tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-zA-Z0-9%]+|[\u4e00-\u9fff]", text.lower()) if len(token) > 1 or "\u4e00" <= token <= "\u9fff"]


STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "that",
    "this",
    "it",
    "is",
    "are",
    "was",
    "were",
    "by",
    "from",
    "as",
    "at",
    "among",
    "according",
    "found",
    "study",
}


def _score_explanation(claim: str, text: str, tfidf: float, bm25: float, hybrid: float) -> str:
    shared = _shared_terms(claim, text)
    score_part = f"Hybrid relevance {hybrid:.2f} (TF-IDF {tfidf:.2f}, BM25 {bm25:.2f})."
    if not shared:
        return score_part
    return f"{score_part} Matched terms: {', '.join(shared[:8])}."


def _shared_terms(claim: str, text: str) -> list[str]:
    claim_terms = [term for term in _tokens(claim) if term not in STOPWORDS]
    text_terms = set(_tokens(text))
    shared = []
    for term in claim_terms:
        if term in text_terms and term not in shared:
            shared.append(term)
    return shared
