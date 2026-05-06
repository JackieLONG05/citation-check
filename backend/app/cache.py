from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .models import FullTextLocation, Source, SourceCandidate
from .settings import cache_path

T = TypeVar("T", bound=BaseModel)


class EvidenceCache:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or cache_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def get_candidate(self, doi: str, provider: str) -> SourceCandidate | None:
        payload = self._get_payload("metadata_cache", "doi = ? AND provider = ?", (doi, provider))
        return _parse_model(SourceCandidate, payload)

    def set_candidate(self, doi: str, provider: str, candidate: SourceCandidate, ttl_seconds: int = 30 * 24 * 3600) -> None:
        self._execute(
            """
            INSERT OR REPLACE INTO metadata_cache (doi, provider, payload, expires_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (doi, provider, candidate.model_dump_json(), _expires(ttl_seconds), _now()),
        )

    def get_full_text_location(self, doi: str) -> FullTextLocation | None:
        payload = self._get_payload("full_text_cache", "doi = ?", (doi,))
        return _parse_model(FullTextLocation, payload)

    def set_full_text_location(self, doi: str, location: FullTextLocation, ttl_seconds: int = 30 * 24 * 3600) -> None:
        self._execute(
            """
            INSERT OR REPLACE INTO full_text_cache (doi, payload, expires_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (doi, location.model_dump_json(), _expires(ttl_seconds), _now()),
        )

    def get_parsed_source(self, key: str) -> Source | None:
        payload = self._get_payload("parsed_source_cache", "cache_key = ?", (key,))
        return _parse_model(Source, payload)

    def set_parsed_source(self, key: str, source: Source, ttl_seconds: int = 180 * 24 * 3600) -> None:
        self._execute(
            """
            INSERT OR REPLACE INTO parsed_source_cache (cache_key, payload, expires_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (key, source.model_dump_json(), _expires(ttl_seconds), _now()),
        )

    def get_recent_failure(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT reason FROM lookup_failures WHERE cache_key = ? AND retry_after > ?",
                (key, _now()),
            ).fetchone()
        return str(row[0]) if row else None

    def set_failure(self, key: str, reason: str, retry_after_seconds: int = 24 * 3600) -> None:
        self._execute(
            """
            INSERT OR REPLACE INTO lookup_failures (cache_key, reason, retry_after, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (key, reason, _now() + retry_after_seconds, _now()),
        )

    def clear(self) -> None:
        with self._connect() as conn:
            for table in ("metadata_cache", "full_text_cache", "parsed_source_cache", "lookup_failures"):
                conn.execute(f"DELETE FROM {table}")

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata_cache (
                    doi TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (doi, provider)
                );

                CREATE TABLE IF NOT EXISTS full_text_cache (
                    doi TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS parsed_source_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS lookup_failures (
                    cache_key TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    retry_after REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    def _get_payload(self, table: str, where: str, params: tuple) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT payload FROM {table} WHERE {where} AND expires_at > ?",
                (*params, _now()),
            ).fetchone()
        return str(row[0]) if row else None

    def _execute(self, sql: str, params: tuple) -> None:
        with self._connect() as conn:
            conn.execute(sql, params)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


def _parse_model(model: type[T], payload: str | None) -> T | None:
    if not payload:
        return None
    return model.model_validate_json(payload)


def _now() -> float:
    return time.time()


def _expires(ttl_seconds: int) -> float:
    return _now() + ttl_seconds

