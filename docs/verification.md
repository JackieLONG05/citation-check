# Verification

Repeatable checks for the current single-sample prototype.

## Backend Tests

```bash
cd backend
source .venv/bin/activate
PYTHONPATH=. python -m pytest -q
```

## Frontend Build

```bash
cd frontend
npm run build
```

## Demo Smoke Test

```bash
curl -sS -X POST http://127.0.0.1:8000/audit \
  -F "text=</Users/keeper/evidencelint/examples/sample_draft.txt" \
  -F "files=@/Users/keeper/evidencelint/examples/bloom_2015_source.txt;type=text/plain" \
  -F "files=@/Users/keeper/evidencelint/examples/piwowar_2018_source.txt;type=text/plain" \
  -F "files=@/Users/keeper/evidencelint/examples/community_life_survey_2024_25_source.txt;type=text/plain" \
  -F "online_lookup=false"
```

Expected demo result:

- 7 total sentences
- 4 `Likely Supported`
- 2 `Citation Missing`
- 2/2 quotes verified
- 3 processed sources

## Last Verified

Date: 2026-05-04

- Backend tests: 77 passed
- Frontend build: passed
- Demo smoke test: passed with `sentences=7, supported=4, missing=2, quotes=2/2, sources=3`
