# Citation Check Project Handoff

Citation Check is a coursework prototype for low-cost citation and evidence auditing. It is not a full fact-checking system and should not be described as a replacement for Google Scholar, peer review, or manual source verification.

## Purpose

The app helps a user paste academic draft text, optionally upload source files, and check whether cited claims and direct quotes can be matched against accessible source text.

It is designed to identify:

- Sentences that appear supported by accessible source text.
- Direct quotes that can be found in source text.
- Claims that need manual review because only metadata or weak evidence was found.
- Claims with missing or clearly invalid citation markers.

## Main User Flow

1. Paste draft text.
2. Optionally upload PDF or TXT sources.
3. Choose Quick check or Deep check.
4. Run the audit.
5. Review highlighted text, sentence-level verdicts, quote checks, evidence snippets, and the source pool.

## Opening the Project

For a macOS handoff, the project includes two root-level launchers:

- `Start Citation Check.command`
- `Stop Citation Check.command`

After unzipping the project, double-click `Start Citation Check.command`. It installs dependencies if needed, starts the FastAPI backend and Vite frontend, then opens `http://127.0.0.1:5173`.

The target machine still needs Python 3.11+ and Node.js/npm installed.

## Check Modes

Quick check prioritizes speed. It uses core metadata lookup and skips deeper online source discovery and full-text parsing.

Deep check prioritizes coverage. It uses the broader provider pipeline, attempts open-access source discovery, and streams backend progress to the UI.

## Online Providers

The backend can use Crossref, OpenAlex, Semantic Scholar, DataCite, Europe PMC, PubMed, DOAJ, OpenAIRE, arXiv, DuckDuckGo/Jina Reader web discovery, and optional Unpaywall. BASE is kept as an optional hook because stable access usually requires registration or IP authorization.

## Verdict Meaning

- Likely Supported: accessible source text matched the claim or quote.
- Needs Review / Weak Evidence: a source lead, metadata, or related text was found, but the claim could not be fully confirmed.
- Citation Missing: citation marker is missing or clearly invalid.
- No Check Needed: sentence is treated as framing or non-checkable context.

## Current Verification

Latest local verification:

- Backend tests: 77 passed.
- Frontend build: passed.
- Demo smoke test: passed.
- Stream audit endpoint: passed smoke test.

## Important Limitations

Citation Check cannot guarantee that every true citation will be found or that every fake citation will be detected. Paywalled sources, noisy search results, incomplete metadata, and inaccessible full text can all lead to uncertain results. This should be framed as an evidence-matching prototype, not a truth oracle.

## Suggested Report Framing

Describe the project as a practical prototype that improves citation review workflow by combining local source parsing, quote matching, evidence retrieval, and open scholarly metadata lookup. Emphasize transparency about uncertainty and the separation between supported, uncertain, and clearly problematic citation cases.
