# Methodology

## Text Parsing

The backend splits pasted text into sentence-like units, then detects:

- Author-year citations such as `(Smith, 2020)` and `Smith et al. (2020)`.
- Numeric citations such as `[1]`.
- URLs and DOIs.
- Direct quotes in straight or curly quotation marks.
- Likely factual claims based on years, numbers, citations, and research-oriented language.

## Source Processing

Uploaded PDFs are parsed page by page with PyMuPDF. Text files are decoded as UTF-8. URL extraction uses trafilatura.

Online lookup uses a DOI-first pipeline. Quick check uses the core metadata path and skips web discovery/full-text parsing; deep check uses the full provider list and open-web fallback.

1. Normalize DOI values detected in the draft, DOI input, or References section.
2. Resolve metadata with Crossref, OpenAlex, Semantic Scholar, DataCite, Europe PMC, PubMed, DOAJ, and OpenAIRE where applicable.
3. Look for legal open-access full text through optional Unpaywall, OpenAlex, Semantic Scholar, DOAJ, Europe PMC, and OpenAIRE.
4. Parse OA PDFs or landing pages when available.
5. If no DOI exists, search Crossref/OpenAlex/DataCite first, then Semantic Scholar, Europe PMC, PubMed, DOAJ, OpenAIRE, and arXiv as additional fallback providers.
6. Automatically attach high-confidence candidates with a DOI. If full text is unavailable but a high-confidence candidate exposes an abstract, attach that abstract as limited searchable source text and label the lookup as metadata-based.
7. Otherwise show candidates for review.

BASE is represented as an optional provider hook rather than a default live search source because public keyword access normally requires approved API access or IP authorization.

## Direct Quote Verification

Direct quote verification is local and deterministic:

1. Normalize whitespace, case, punctuation, hyphens, and common OCR differences.
2. Run exact matching against each source page.
3. Recognize ellipsis quotes and bracket insertions as slight modifications.
4. If exact matching fails, run fuzzy matching with rapidfuzz.
5. Penalize numeric mismatches because numbers are high-risk in academic quotes.
6. Return match score, source name, page number, and surrounding context.

## Evidence Retrieval

Source text is chunked into paragraphs or page fragments. The MVP combines character n-gram TF-IDF and BM25 to retrieve likely evidence snippets for each factual sentence. Each snippet includes a short score explanation with the hybrid score and matched terms. This is a relevance signal, not a final truth judgment.

## Reliability

Reliability scoring is rule-based. The score increases when a source has full text, DOI metadata, authors, publication year, publisher or venue, or an institutional domain. It decreases when key metadata or extracted text is missing.
