from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    SUPPORTED = "Likely Supported"
    NEEDS_REVIEW = "Needs Review"
    WEAK_EVIDENCE = "Weak Evidence"
    NO_EVIDENCE = "No Evidence Found"
    CITATION_MISSING = "Citation Missing"
    NO_CHECK_NEEDED = "No Check Needed"


class QuoteStatus(str, Enum):
    VERIFIED = "Quote Verified"
    SLIGHTLY_MODIFIED = "Quote Slightly Modified"
    MISMATCH = "Quote Mismatch"
    NOT_FOUND = "Quote Not Found"


class ParsedSentence(BaseModel):
    id: str
    text: str
    citations: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    dois: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)
    likely_factual_claim: bool = False


class SourcePage(BaseModel):
    page_number: int | None = None
    text: str


class SourceMetadata(BaseModel):
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    url: str | None = None
    publisher: str | None = None
    container: str | None = None


class Source(BaseModel):
    id: str
    kind: Literal["pdf", "web", "doi", "text"]
    name: str
    metadata: SourceMetadata = Field(default_factory=SourceMetadata)
    pages: list[SourcePage] = Field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text)


class EvidenceSnippet(BaseModel):
    source_id: str
    source_name: str
    page_number: int | None = None
    text: str
    relevance_score: float
    explanation: str | None = None


class QuoteCheck(BaseModel):
    quote: str
    status: QuoteStatus
    score: float
    source_id: str | None = None
    source_name: str | None = None
    page_number: int | None = None
    context: str | None = None


class ReliabilityScore(BaseModel):
    source_id: str
    level: Literal["High", "Medium", "Low", "Unknown"]
    score: int
    freshness: Literal["Fresh", "Acceptable", "Possibly Outdated", "Unknown"]
    reasons: list[str] = Field(default_factory=list)


class SourceCandidate(BaseModel):
    id: str
    provider: str
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    venue: str | None = None
    publisher: str | None = None
    url: str | None = None
    abstract: str | None = None
    confidence: float = 0.0
    metadata: dict = Field(default_factory=dict)


class FullTextLocation(BaseModel):
    provider: str
    url: str
    kind: Literal["pdf", "html", "landing_page"]
    license: str | None = None
    is_open_access: bool = True


class OnlineLookupResult(BaseModel):
    query: str
    status: Literal[
        "Metadata Found",
        "Open Access PDF Found",
        "Open Access Landing Page Found",
        "Full Text Parsed",
        "Full Text Unavailable",
        "No DOI Detected",
        "Lookup Failed",
    ]
    candidates: list[SourceCandidate] = Field(default_factory=list)
    selected_candidate: SourceCandidate | None = None
    full_text_location: FullTextLocation | None = None
    source_id: str | None = None
    notes: list[str] = Field(default_factory=list)


class SentenceAudit(BaseModel):
    sentence: ParsedSentence
    verdict: Verdict
    evidence: list[EvidenceSnippet] = Field(default_factory=list)
    quote_checks: list[QuoteCheck] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AuditSummary(BaseModel):
    total_sentences: int
    likely_claims: int
    cited_sentences: int
    missing_citations: int
    quotes_found: int
    quotes_verified: int
    sources_processed: int


class AuditResponse(BaseModel):
    summary: AuditSummary
    sentences: list[SentenceAudit]
    sources: list[Source]
    reliability: list[ReliabilityScore]
    online_lookup: list[OnlineLookupResult] = Field(default_factory=list)
