from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.cache import EvidenceCache
from app.main import app
from app.models import FullTextLocation, OnlineLookupResult, Source, SourceCandidate, SourcePage
from app.resolvers.pipeline import ResolverPipeline


def test_online_lookup_without_doi_returns_clear_state():
    client = TestClient(app)

    response = client.post("/audit", data={"text": "This draft has no DOI.", "online_lookup": "true"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["online_lookup"][0]["status"] == "No DOI Detected"
    assert payload["online_lookup"][0]["notes"]


def test_uploaded_source_is_checked_before_online_lookup(monkeypatch):
    client = TestClient(app)

    async def fail_online_call(*_args, **_kwargs):
        raise AssertionError("online lookup should not run when the uploaded source already verifies the draft")

    monkeypatch.setattr("app.main.ResolverPipeline.resolve_doi_to_source", fail_online_call)
    monkeypatch.setattr("app.main.ResolverPipeline.search_candidates", fail_online_call)
    monkeypatch.setattr("app.main.ResolverPipeline.search_web_source", fail_online_call)

    response = client.post(
        "/audit",
        data={
            "text": 'They can include audio, video and visual materials without becoming “as complex or as potentially expensive to develop as an app” (Hall, 2013, p. 120).',
            "online_lookup": "true",
            "doi": "10.9999/should-not-be-used",
        },
        files=[
            (
                "files",
                (
                    "hall-source.txt",
                    "Enhanced ebooks can include audio, video and visual materials without becoming as complex or as potentially expensive to develop as an app.",
                    "text/plain",
                ),
            )
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["online_lookup"] == []
    assert payload["summary"]["sources_processed"] == 1
    assert payload["summary"]["missing_citations"] == 0
    assert payload["summary"]["quotes_verified"] == 1
    assert payload["sentences"][0]["quote_checks"][0]["status"] == "Quote Verified"


def test_online_lookup_detects_doi_in_references(monkeypatch):
    client = TestClient(app)

    async def fake_resolve(_self, doi: str, source_id: str, **_kwargs):
        from app.models import OnlineLookupResult

        return None, OnlineLookupResult(query=doi, status="Full Text Unavailable", source_id=source_id)

    monkeypatch.setattr("app.main.ResolverPipeline.resolve_doi_to_source", fake_resolve)
    response = client.post(
        "/audit",
        data={
            "text": """
            This claim cites a numbered reference [1].

            References
            [1] Bloom, N. (2015). Does working from home work? https://doi.org/10.1093/qje/qju032
            """,
            "online_lookup": "true",
        },
    )

    assert response.status_code == 200
    assert response.json()["online_lookup"][0]["query"] == "10.1093/qje/qju032"


def test_online_lookup_searches_author_year_when_no_doi(monkeypatch):
    client = TestClient(app)
    queries = []

    async def fake_search(_self, query: str, **_kwargs):
        queries.append(query)
        return [
            SourceCandidate(
                id="crossref:10.1093/qje/qju032",
                provider="Crossref",
                title="Does working from home work? Evidence from a Chinese experiment",
                authors=["Nicholas Bloom"],
                year=2015,
                doi="10.1093/qje/qju032",
                confidence=0.7,
                metadata={"confidence_label": "Medium"},
            )
        ]

    monkeypatch.setattr("app.main.ResolverPipeline.search_candidates", fake_search)
    monkeypatch.setattr("app.main.ResolverPipeline.search_web_source", _async_source_lookup(None))
    response = client.post(
        "/audit",
        data={"text": "Bloom et al. (2015) found remote work effects vary by job context.", "online_lookup": "true"},
    )

    assert response.status_code == 200
    assert queries[0].startswith("Bloom 2015")
    assert "remote work effects" in queries[0]
    lookup = response.json()["online_lookup"][0]
    assert lookup["status"] == "Metadata Found"
    assert lookup["candidates"][0]["metadata"]["confidence_label"] == "Medium"
    assert response.json()["summary"]["sources_processed"] == 0


def test_online_lookup_uses_web_search_when_no_candidate_doi_but_quote_has_citation(monkeypatch):
    client = TestClient(app)
    calls = []

    async def fake_search_candidates(_self, query: str, **_kwargs):
        calls.append(("candidates", query))
        return []

    async def fake_search_web_source(_self, query: str, source_id: str, **_kwargs):
        calls.append(("web", query))
        return (
            Source(
                id=source_id,
                kind="web",
                name="Web source",
                pages=[SourcePage(text="The source says remote work effects vary by job context.")],
            ),
            OnlineLookupResult(query=query, status="Full Text Parsed", source_id=source_id),
        )

    monkeypatch.setattr("app.main.ResolverPipeline.search_candidates", fake_search_candidates)
    monkeypatch.setattr("app.main.ResolverPipeline.search_web_source", fake_search_web_source)

    response = client.post(
        "/audit",
        data={
            "text": 'Smith (2020) wrote that "remote work effects vary by job context."',
            "online_lookup": "true",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert ("candidates", "Smith 2020") in calls
    assert any(kind == "web" and "remote work effects vary by job context" in query for kind, query in calls)
    assert payload["online_lookup"][-1]["status"] == "Full Text Parsed"
    assert payload["summary"]["sources_processed"] == 1


def test_fast_online_lookup_skips_web_search_when_candidate_search_fails(monkeypatch):
    client = TestClient(app)
    search_modes = []

    async def fake_search_candidates(_self, _query: str, **kwargs):
        search_modes.append(kwargs.get("check_mode"))
        return []

    async def fail_web_search(*_args, **_kwargs):
        raise AssertionError("fast mode should not run web source discovery")

    monkeypatch.setattr("app.main.ResolverPipeline.search_candidates", fake_search_candidates)
    monkeypatch.setattr("app.main.ResolverPipeline.search_web_source", fail_web_search)

    response = client.post(
        "/audit",
        data={
            "text": 'Smith (2020) wrote that "remote work effects vary by job context."',
            "online_lookup": "true",
            "check_mode": "fast",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert search_modes == ["fast", "fast"]
    assert payload["online_lookup"]
    assert all(result["status"] == "Lookup Failed" for result in payload["online_lookup"])
    assert payload["summary"]["sources_processed"] == 0


def test_invalid_check_mode_returns_422():
    client = TestClient(app)

    response = client.post(
        "/audit",
        data={"text": "This draft has no DOI.", "online_lookup": "true", "check_mode": "quick"},
    )

    assert response.status_code == 422
    assert "check_mode" in response.json()["detail"]


def test_audit_stream_returns_progress_and_result():
    client = TestClient(app)

    with client.stream(
        "POST",
        "/audit/stream",
        data={
            "text": "This classroom note does not need checking.",
            "online_lookup": "false",
            "check_mode": "deep",
        },
    ) as response:
        assert response.status_code == 200
        events = [json.loads(line) for line in response.iter_lines() if line]

    assert any(event["type"] == "progress" and event["task"] == "Parse draft" for event in events)
    result_events = [event for event in events if event["type"] == "result"]
    assert len(result_events) == 1
    assert result_events[0]["result"]["summary"]["total_sentences"] == 1


def test_online_lookup_resolves_high_confidence_reference_candidate(monkeypatch):
    client = TestClient(app)

    async def fake_search(_self, query: str, **_kwargs):
        return [
            SourceCandidate(
                id="crossref:10.1093/qje/qju032",
                provider="Crossref",
                title="Does working from home work? Evidence from a Chinese experiment",
                authors=["Nicholas Bloom"],
                year=2015,
                doi="10.1093/qje/qju032",
                confidence=0.91,
                metadata={"confidence_label": "High"},
            )
        ]

    async def fake_resolve(_self, doi: str, source_id: str, **_kwargs):
        candidate = SourceCandidate(
            id=f"crossref:{doi}",
            provider="Crossref",
            title="Does working from home work? Evidence from a Chinese experiment",
            doi=doi,
            confidence=0.98,
        )
        return (
            Source(id=source_id, kind="web", name="Resolved source", pages=[SourcePage(text="Remote work effects vary.")]),
            OnlineLookupResult(query=doi, status="Full Text Parsed", selected_candidate=candidate, source_id=source_id),
        )

    monkeypatch.setattr("app.main.ResolverPipeline.search_candidates", fake_search)
    monkeypatch.setattr("app.main.ResolverPipeline.resolve_doi_to_source", fake_resolve)
    response = client.post(
        "/audit",
        data={
            "text": """
            This claim cites a reference [1].

            References
            [1] Bloom, N. (2015). Does working from home work? Evidence from a Chinese experiment. Quarterly Journal of Economics.
            """,
            "online_lookup": "true",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    lookup = payload["online_lookup"][0]
    assert lookup["status"] == "Full Text Parsed"
    assert "Does working from home work" in lookup["query"]
    assert lookup["candidates"][0]["metadata"]["confidence_label"] == "High"
    assert payload["summary"]["sources_processed"] == 1


def test_pipeline_returns_full_text_parsed_with_mocked_providers(tmp_path):
    async def run():
        pipeline = _pipeline(tmp_path)
        candidate = _candidate()
        pipeline.crossref.resolve_doi = _async_value(candidate)  # type: ignore[method-assign]
        pipeline.openalex.resolve_doi = _async_value(candidate)  # type: ignore[method-assign]
        pipeline.openalex.full_text_location = lambda _: FullTextLocation(  # type: ignore[method-assign]
            provider="OpenAlex",
            url="https://example.org/article",
            kind="landing_page",
            license="cc-by",
        )
        pipeline._source_from_location = _async_value(  # type: ignore[method-assign]
            Source(
                id="src1",
                kind="web",
                name="Example Paper",
                pages=[SourcePage(text="The source full text says at least 28% of articles are OA.")],
            )
        )

        source, lookup = await pipeline.resolve_doi_to_source("10.1234/example", "src1")
        assert source is not None
        assert lookup.status == "Full Text Parsed"
        assert lookup.full_text_location is not None
        assert lookup.source_id == "src1"

    asyncio.run(run())


def test_pipeline_returns_lookup_failed_when_no_provider_finds_metadata(tmp_path):
    async def run():
        pipeline = _pipeline(tmp_path)
        pipeline.crossref.resolve_doi = _async_value(None)  # type: ignore[method-assign]
        pipeline.openalex.resolve_doi = _async_value(None)  # type: ignore[method-assign]

        source, lookup = await pipeline.resolve_doi_to_source("10.404/missing", "src1")
        assert source is None
        assert lookup.status == "Lookup Failed"

    asyncio.run(run())


def test_pipeline_uses_europe_pmc_doi_metadata_fallback(tmp_path):
    async def run():
        pipeline = _pipeline(tmp_path)
        candidate = SourceCandidate(
            id="europepmc:10.1234/example",
            provider="Europe PMC",
            title="Example Europe PMC Paper",
            authors=["Ada Lovelace"],
            year=2024,
            doi="10.1234/example",
            abstract="The Europe PMC abstract contains searchable evidence.",
        )
        pipeline.crossref.resolve_doi = _async_value(None)  # type: ignore[method-assign]
        pipeline.openalex.resolve_doi = _async_value(None)  # type: ignore[method-assign]
        pipeline.europe_pmc.resolve_doi = _async_value(candidate)  # type: ignore[method-assign]
        pipeline.search_web_source = _async_source_lookup(None)  # type: ignore[method-assign]

        source, lookup = await pipeline.resolve_doi_to_source("10.1234/example", "src1")

        assert source is not None
        assert source.pages[0].text == "The Europe PMC abstract contains searchable evidence."
        assert lookup.status == "Metadata Found"
        assert lookup.selected_candidate is not None
        assert lookup.selected_candidate.provider == "Europe PMC"

    asyncio.run(run())


def test_pipeline_records_provider_timeout_note(tmp_path):
    async def run():
        pipeline = _pipeline(tmp_path)
        pipeline.crossref.resolve_doi = _async_raise(TimeoutError("slow provider"))  # type: ignore[method-assign]
        pipeline.openalex.resolve_doi = _async_value(None)  # type: ignore[method-assign]

        _, lookup = await pipeline.resolve_doi_to_source("10.1234/slow", "src1")
        assert lookup.status == "Lookup Failed"
        assert any("Crossref lookup failed" in note for note in lookup.notes)

    asyncio.run(run())


def test_pipeline_returns_metadata_only_when_full_text_unavailable(tmp_path):
    async def run():
        pipeline = _pipeline(tmp_path)
        candidate = _candidate()
        pipeline.crossref.resolve_doi = _async_value(candidate)  # type: ignore[method-assign]
        pipeline.openalex.resolve_doi = _async_value(None)  # type: ignore[method-assign]
        pipeline.search_web_source = _async_source_lookup(None)  # type: ignore[method-assign]

        source, lookup = await pipeline.resolve_doi_to_source("10.1234/metadata", "src1")
        assert source is not None
        assert source.pages == []
        assert lookup.status == "Full Text Unavailable"
        assert lookup.selected_candidate is not None

    asyncio.run(run())


def test_pipeline_returns_location_status_when_full_text_parse_fails(tmp_path):
    async def run():
        pipeline = _pipeline(tmp_path)
        candidate = _candidate()
        pipeline.crossref.resolve_doi = _async_value(candidate)  # type: ignore[method-assign]
        pipeline.openalex.resolve_doi = _async_value(candidate)  # type: ignore[method-assign]
        pipeline.openalex.full_text_location = lambda _: FullTextLocation(  # type: ignore[method-assign]
            provider="OpenAlex",
            url="https://example.org/article",
            kind="landing_page",
        )
        pipeline._source_from_location = _async_raise(RuntimeError("parse failed"))  # type: ignore[method-assign]
        pipeline.search_web_source = _async_source_lookup(None)  # type: ignore[method-assign]

        source, lookup = await pipeline.resolve_doi_to_source("10.1234/parse", "src1")
        assert source is not None
        assert source.pages == []
        assert lookup.status == "Open Access Landing Page Found"
        assert any("Full-text download or parsing failed" in note for note in lookup.notes)

    asyncio.run(run())


def test_pipeline_fast_doi_lookup_skips_deep_providers_and_full_text_parsing(tmp_path):
    async def run():
        pipeline = _pipeline(tmp_path)
        candidate = _candidate()

        async def fail_deep_provider(*_args, **_kwargs):
            raise AssertionError("fast mode should not call deep DOI providers")

        async def fail_full_text_parse(*_args, **_kwargs):
            raise AssertionError("fast mode should not parse provider full text")

        pipeline.crossref.resolve_doi = _async_value(candidate)  # type: ignore[method-assign]
        pipeline.openalex.resolve_doi = _async_value(candidate)  # type: ignore[method-assign]
        pipeline.openalex.full_text_location = lambda _: FullTextLocation(  # type: ignore[method-assign]
            provider="OpenAlex",
            url="https://example.org/article",
            kind="landing_page",
        )
        pipeline.semantic_scholar.resolve_doi = fail_deep_provider  # type: ignore[method-assign]
        pipeline.datacite.resolve_doi = fail_deep_provider  # type: ignore[method-assign]
        pipeline.europe_pmc.resolve_doi = fail_deep_provider  # type: ignore[method-assign]
        pipeline.pubmed.resolve_doi = fail_deep_provider  # type: ignore[method-assign]
        pipeline.doaj.resolve_doi = fail_deep_provider  # type: ignore[method-assign]
        pipeline.openaire.resolve_doi = fail_deep_provider  # type: ignore[method-assign]
        pipeline._source_from_location = fail_full_text_parse  # type: ignore[method-assign]

        source, lookup = await pipeline.resolve_doi_to_source("10.1234/example", "src1", check_mode="fast")

        assert source is not None
        assert source.pages == []
        assert lookup.status == "Open Access Landing Page Found"
        assert lookup.full_text_location is not None
        assert any("Fast check mode" in note for note in lookup.notes)

    asyncio.run(run())


def _candidate() -> SourceCandidate:
    return SourceCandidate(
        id="crossref:10.1234/example",
        provider="Crossref",
        title="Example Paper",
        authors=["Ada Lovelace"],
        year=2020,
        doi="10.1234/example",
        venue="Journal of Tests",
        confidence=0.98,
    )


def _pipeline(tmp_path) -> ResolverPipeline:
    pipeline = ResolverPipeline(cache=EvidenceCache(tmp_path / "cache.db"))
    pipeline.semantic_scholar.resolve_doi = _async_value(None)  # type: ignore[method-assign]
    pipeline.datacite.resolve_doi = _async_value(None)  # type: ignore[method-assign]
    pipeline.europe_pmc.resolve_doi = _async_value(None)  # type: ignore[method-assign]
    pipeline.pubmed.resolve_doi = _async_value(None)  # type: ignore[method-assign]
    pipeline.doaj.resolve_doi = _async_value(None)  # type: ignore[method-assign]
    pipeline.openaire.resolve_doi = _async_value(None)  # type: ignore[method-assign]
    return pipeline


def _async_value(value):
    async def inner(*_args, **_kwargs):
        return value

    return inner


def _async_raise(exc: Exception):
    async def inner(*_args, **_kwargs):
        raise exc

    return inner


def _async_source_lookup(source: Source | None):
    async def inner(*args, **_kwargs):
        query = args[-2]
        source_id = args[-1]
        return source, OnlineLookupResult(
            query=query,
            status="Full Text Parsed" if source else "Metadata Found",
            source_id=source_id if source else None,
        )

    return inner
