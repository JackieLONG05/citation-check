from app.evidence import classify_evidence, retrieve_evidence
from app.models import Source, SourcePage


def test_hybrid_retrieval_finds_relevant_chunk():
    source = Source(
        id="src1",
        kind="text",
        name="source.txt",
        pages=[
            SourcePage(
                text=(
                    "The experiment found that home working led to a 13% performance increase among call center workers.\n\n"
                    "A separate paragraph discusses unrelated office furniture procurement."
                )
            )
        ],
    )

    snippets = retrieve_evidence("Home working led to a 13% performance increase.", [source])

    assert snippets
    assert "13% performance increase" in snippets[0].text
    assert snippets[0].explanation
    assert "Matched terms" in snippets[0].explanation
    assert classify_evidence(snippets) in {"Likely Supported", "Needs Review"}


def test_hybrid_retrieval_returns_empty_for_no_source_text():
    source = Source(id="src1", kind="doi", name="metadata-only")

    assert retrieve_evidence("A factual claim.", [source]) == []
