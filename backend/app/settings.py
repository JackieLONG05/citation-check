from __future__ import annotations

import os
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_PATH = BACKEND_ROOT / "data" / "evidencelint.db"


def cache_path() -> Path:
    return Path(os.getenv("EVIDENCELINT_CACHE_PATH", str(DEFAULT_CACHE_PATH))).expanduser()


def contact_email() -> str | None:
    return os.getenv("CONTACT_EMAIL") or os.getenv("UNPAYWALL_EMAIL")


def semantic_scholar_api_key() -> str | None:
    return os.getenv("SEMANTIC_SCHOLAR_API_KEY")


def ncbi_api_key() -> str | None:
    return os.getenv("NCBI_API_KEY")


def openaire_access_token() -> str | None:
    return os.getenv("OPENAIRE_ACCESS_TOKEN")


def openalex_api_key() -> str | None:
    return os.getenv("OPENALEX_API_KEY")


def base_api_enabled() -> bool:
    return os.getenv("BASE_API_ENABLED", "").lower() in {"1", "true", "yes"}


def frontend_origins() -> list[str]:
    raw = os.getenv("FRONTEND_ORIGINS", "")
    configured = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return configured or ["http://localhost:5173", "http://127.0.0.1:5173"]
