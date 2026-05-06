import asyncio

from app.cache import EvidenceCache
from app.models import SourceCandidate
from app.resolvers.pipeline import ResolverPipeline
from app.resolvers.ranking import rank_candidates


def test_rank_candidates_prefers_title_author_year_match():
    candidates = [
        SourceCandidate(
            id="crossref:10.1/b",
            provider="Crossref",
            title="Unrelated remote work paper",
            authors=["Other Author"],
            year=2015,
            doi="10.1/b",
        ),
        SourceCandidate(
            id="crossref:10.1093/qje/qju032",
            provider="Crossref",
            title="Does working from home work? Evidence from a Chinese experiment",
            authors=["Nicholas Bloom"],
            year=2015,
            doi="10.1093/qje/qju032",
        ),
    ]

    ranked = rank_candidates(
        candidates,
        title_hint="Does working from home work? Evidence from a Chinese experiment",
        author_hint="Bloom",
        year=2015,
    )

    assert ranked[0].doi == "10.1093/qje/qju032"
    assert ranked[0].metadata["confidence_label"] == "High"


def test_rank_candidates_marks_close_matches_ambiguous():
    candidates = [
        SourceCandidate(
            id="crossref:10.1/a",
            provider="Crossref",
            title="Remote work and productivity",
            authors=["Smith"],
            year=2020,
            doi="10.1/a",
        ),
        SourceCandidate(
            id="crossref:10.1/b",
            provider="Crossref",
            title="Remote work productivity",
            authors=["Smith"],
            year=2020,
            doi="10.1/b",
        ),
    ]

    ranked = rank_candidates(candidates, title_hint="Remote work productivity", author_hint="Smith", year=2020)

    assert ranked[0].metadata["confidence_label"] == "Ambiguous"
    assert ranked[1].metadata["confidence_label"] == "Ambiguous"


def test_pipeline_search_candidates_dedupes_and_ranks(tmp_path):
    async def run():
        pipeline = ResolverPipeline(cache=EvidenceCache(tmp_path / "cache.db"))
        crossref_candidate = SourceCandidate(
            id="crossref:10.1093/qje/qju032",
            provider="Crossref",
            title="Does working from home work? Evidence from a Chinese experiment",
            authors=["Nicholas Bloom"],
            year=2015,
            doi="10.1093/qje/qju032",
        )
        duplicate_openalex = crossref_candidate.model_copy(update={"id": "openalex:w1", "provider": "OpenAlex"})
        pipeline.crossref.search_bibliographic = _async_value([crossref_candidate])  # type: ignore[method-assign]
        pipeline.openalex.search = _async_value([duplicate_openalex])  # type: ignore[method-assign]
        pipeline.datacite.search = _async_value([])  # type: ignore[method-assign]

        ranked = await pipeline.search_candidates(
            "Bloom 2015 working from home",
            title_hint="Does working from home work? Evidence from a Chinese experiment",
            author_hint="Bloom",
            year=2015,
        )

        assert len(ranked) == 1
        assert ranked[0].doi == "10.1093/qje/qju032"

    asyncio.run(run())


def test_pipeline_search_candidates_uses_semantic_scholar_fallback(tmp_path):
    async def run():
        pipeline = ResolverPipeline(cache=EvidenceCache(tmp_path / "cache.db"))
        semantic_candidate = SourceCandidate(
            id="semanticscholar:w1",
            provider="Semantic Scholar",
            title="Does working from home work? Evidence from a Chinese experiment",
            authors=["Nicholas Bloom"],
            year=2015,
            doi="10.1093/qje/qju032",
        )
        pipeline.crossref.search_bibliographic = _async_value([])  # type: ignore[method-assign]
        pipeline.openalex.search = _async_value([])  # type: ignore[method-assign]
        pipeline.datacite.search = _async_value([])  # type: ignore[method-assign]
        pipeline.semantic_scholar.search = _async_value([semantic_candidate])  # type: ignore[method-assign]

        ranked = await pipeline.search_candidates(
            "Bloom 2015 working from home",
            title_hint="Does working from home work? Evidence from a Chinese experiment",
            author_hint="Bloom",
            year=2015,
        )

        assert ranked[0].provider == "Semantic Scholar"
        assert ranked[0].metadata["confidence_label"] == "High"

    asyncio.run(run())


def test_pipeline_search_candidates_uses_europe_pmc_and_arxiv_fallback(tmp_path):
    async def run():
        pipeline = ResolverPipeline(cache=EvidenceCache(tmp_path / "cache.db"))
        europe_candidate = SourceCandidate(
            id="europepmc:123",
            provider="Europe PMC",
            title="Example biomedical study",
            authors=["Ada Lovelace"],
            year=2024,
            doi="10.1234/example",
            abstract="Example biomedical study reports a measurable result.",
        )
        arxiv_candidate = SourceCandidate(
            id="arxiv:2401.00001",
            provider="arXiv",
            title="Less relevant preprint",
            authors=["Other Author"],
            year=2024,
            abstract="A different result.",
        )
        pipeline.crossref.search_bibliographic = _async_value([])  # type: ignore[method-assign]
        pipeline.openalex.search = _async_value([])  # type: ignore[method-assign]
        pipeline.datacite.search = _async_value([])  # type: ignore[method-assign]
        pipeline.semantic_scholar.search = _async_value([])  # type: ignore[method-assign]
        pipeline.europe_pmc.search = _async_value([europe_candidate])  # type: ignore[method-assign]
        pipeline.pubmed.search = _async_value([])  # type: ignore[method-assign]
        pipeline.doaj.search = _async_value([])  # type: ignore[method-assign]
        pipeline.openaire.search = _async_value([])  # type: ignore[method-assign]
        pipeline.arxiv.search = _async_value([arxiv_candidate])  # type: ignore[method-assign]

        ranked = await pipeline.search_candidates(
            "Example biomedical study",
            title_hint="Example biomedical study",
            author_hint="Lovelace",
            year=2024,
        )

        assert ranked[0].provider == "Europe PMC"
        assert ranked[0].metadata["confidence_label"] == "High"
        assert {candidate.provider for candidate in ranked} == {"Europe PMC", "arXiv"}

    asyncio.run(run())


def test_pipeline_fast_search_only_uses_core_metadata_providers(tmp_path):
    async def run():
        pipeline = ResolverPipeline(cache=EvidenceCache(tmp_path / "cache.db"))
        crossref_candidate = SourceCandidate(
            id="crossref:10.1093/qje/qju032",
            provider="Crossref",
            title="Does working from home work? Evidence from a Chinese experiment",
            authors=["Nicholas Bloom"],
            year=2015,
            doi="10.1093/qje/qju032",
        )
        calls = []

        async def fail_deep_provider(*_args, **_kwargs):
            raise AssertionError("fast mode should not call deep search providers")

        pipeline.crossref.search_bibliographic = _async_value([crossref_candidate])  # type: ignore[method-assign]
        pipeline.openalex.search = _async_value([])  # type: ignore[method-assign]
        pipeline.datacite.search = fail_deep_provider  # type: ignore[method-assign]
        pipeline.semantic_scholar.search = fail_deep_provider  # type: ignore[method-assign]
        pipeline.europe_pmc.search = fail_deep_provider  # type: ignore[method-assign]
        pipeline.pubmed.search = fail_deep_provider  # type: ignore[method-assign]
        pipeline.doaj.search = fail_deep_provider  # type: ignore[method-assign]
        pipeline.openaire.search = fail_deep_provider  # type: ignore[method-assign]
        pipeline.arxiv.search = fail_deep_provider  # type: ignore[method-assign]

        ranked = await pipeline.search_candidates(
            "Bloom 2015 working from home",
            author_hint="Bloom",
            year=2015,
            check_mode="fast",
        )
        calls.extend(candidate.provider for candidate in ranked)

        assert calls == ["Crossref"]

    asyncio.run(run())


def _async_value(value):
    async def inner(*_args, **_kwargs):
        return value

    return inner
