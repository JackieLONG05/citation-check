# Citation Check

Citation Check is a low-cost citation and evidence auditing MVP for academic writing.

It checks whether a pasted draft contains citations, direct quotes, and factual claims, then compares those items against a small built-in demo source or legally discoverable online sources. This course-project prototype is intentionally scoped around a stable sample.

## MVP Features

- Paste or edit draft text.
- Load one stable demo sample.
- Automatically attach the built-in Bloom source for that demo sample.
- Automatic online lookup for custom text when no built-in source applies.
- Choose `Quick check` for faster core metadata lookup or `Deep check` for broader source discovery and full-text attempts.
- Stream Deep check progress so users can see which lookup stage is currently running.
- Detect simple citations, URLs, DOIs, direct quotes, and likely factual claims.
- Search Crossref, OpenAlex, Semantic Scholar, DataCite, Europe PMC, PubMed, DOAJ, OpenAIRE, and arXiv for source candidates.
- Retrieve legal open-access full text or public web text when available.
- Verify direct quotes with exact, fuzzy, punctuation-normalized, ellipsis, and bracket-insertion matching.
- Retrieve likely evidence snippets from source text with score explanations.
- Score source reliability and freshness with explainable rules.
- Inline sentence coloring directly inside the editable draft text.
- Show sentence-level verdicts in a browser UI.

## What It Does Not Claim

Citation Check does not prove that a claim is universally true. It audits whether the provided source material appears to support the sentence and whether quoted text can be found in the source.

Automatic web search is a convenience layer, not a paywall bypass. If the original source is behind a publisher paywall or only metadata is public, Citation Check can identify likely source candidates but may still mark quotes as not verified.

## Project Structure

```text
backend/   FastAPI audit API
frontend/  React + Vite audit interface
docs/      limits, method, and verification notes
examples/  one stable sample draft and source
```

## Run Locally

Desktop shortcuts were created for this machine:

- `Start Citation Check.command`
- `Stop Citation Check.command`

On macOS, double-click `Start Citation Check.command` from the project folder. It creates the Python virtual environment, installs backend/frontend dependencies, starts both servers, and opens the website. Use `Stop Citation Check.command` when finished.

Prerequisites for a new machine:

- Python 3.11+
- Node.js / npm

Optional environment variables are documented in `.env.example`. `CONTACT_EMAIL` enables Unpaywall and polite API usage; without it, the app still uses the public scholarly providers. `SEMANTIC_SCHOLAR_API_KEY`, `OPENALEX_API_KEY`, `NCBI_API_KEY`, and `OPENAIRE_ACCESS_TOKEN` are optional and only help with provider limits. BASE is kept as an explicit optional hook because its keyword API typically needs approved access.

Backend:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open the frontend URL shown by Vite, usually:

```text
http://localhost:5173
```

## Deploy as One Public Link

The repository includes a production `Dockerfile` and `render.yaml`. On Render, the app runs as one web service: FastAPI serves both the API and the built React interface, so users only need one public URL.

[Deploy to Render](https://render.com/deploy?repo=https://github.com/JackieLONG05/citation-check)

After the Render deploy finishes, open the generated `.onrender.com` URL. That single URL is the Citation Check website and backend API.

Notes:

- The free Render plan can take a short time to wake up after inactivity.
- Optional API keys can be added in Render environment variables: `CONTACT_EMAIL`, `SEMANTIC_SCHOLAR_API_KEY`, `OPENALEX_API_KEY`, `NCBI_API_KEY`, and `OPENAIRE_ACCESS_TOKEN`.
- No `VITE_API_URL` is needed for this deployment because the frontend calls the same origin as the backend.

Current local dev sessions can also be run detached with:

```bash
screen -dmS evidencelint-backend bash -lc 'cd /Users/keeper/evidencelint/backend && source .venv/bin/activate && uvicorn app.main:app --host 127.0.0.1 --port 8000'
screen -dmS evidencelint-frontend bash -lc 'cd /Users/keeper/evidencelint/frontend && npm run dev -- --host 127.0.0.1 --port 5173'
```

Stop them with:

```bash
screen -S evidencelint-backend -X quit
screen -S evidencelint-frontend -X quit
```

## Test

Backend:

```bash
cd backend
PYTHONPATH=. python -m pytest
```

Frontend:

```bash
cd frontend
npm run build
```

## Demo Data

The frontend includes one stable `Demo sample`. When that exact sample is loaded, Citation Check automatically attaches three built-in source excerpts so the prototype demo does not depend on live web search.

Expected demo result:

- 4 cited source-backed sentences: `Likely Supported`
- 2 direct quotes: `Quote Verified`
- 1 future-year fake citation: `Citation Missing`
- 1 uncited pilot-study sentence: `Citation Missing`

Only the stable sample draft and its three source excerpts are kept in `examples/` for the current prototype.
