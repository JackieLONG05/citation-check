from app.cache import EvidenceCache
from app.models import FullTextLocation, Source, SourceCandidate, SourcePage


def test_cache_round_trips_candidate_location_and_source(tmp_path):
    cache = EvidenceCache(tmp_path / "cache.db")
    candidate = SourceCandidate(
        id="crossref:10.1234/example",
        provider="Crossref",
        title="Example",
        doi="10.1234/example",
        confidence=0.9,
    )
    location = FullTextLocation(provider="OpenAlex", url="https://example.org/paper", kind="landing_page")
    source = Source(
        id="src1",
        kind="web",
        name="Example",
        pages=[SourcePage(text="Parsed source text")],
    )

    cache.set_candidate("10.1234/example", "Crossref", candidate)
    cache.set_full_text_location("10.1234/example", location)
    cache.set_parsed_source("source-key", source)

    assert cache.get_candidate("10.1234/example", "Crossref") == candidate
    assert cache.get_full_text_location("10.1234/example") == location
    assert cache.get_parsed_source("source-key") == source


def test_cache_tracks_recent_failures(tmp_path):
    cache = EvidenceCache(tmp_path / "cache.db")

    cache.set_failure("doi:10.404/missing", "not found", retry_after_seconds=60)

    assert cache.get_recent_failure("doi:10.404/missing") == "not found"


def test_cache_clear_removes_records(tmp_path):
    cache = EvidenceCache(tmp_path / "cache.db")
    candidate = SourceCandidate(id="c1", provider="Crossref", doi="10.1234/example")
    cache.set_candidate("10.1234/example", "Crossref", candidate)

    cache.clear()

    assert cache.get_candidate("10.1234/example", "Crossref") is None
