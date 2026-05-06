from app.resolvers.web_search import _dois, _is_direct_quote_search, _jina_reader_url, _parse_duckduckgo_markdown, _quote_match_score


def test_parse_duckduckgo_markdown_unwraps_redirect_and_snippet():
    markdown = """
## [Example Paper](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fpaper%3Fid%3D1)

Example Paper abstract with relevant source language.

## [DuckDuckGo](https://duckduckgo.com/help)

This should be skipped.

## [Second Result](https://example.net/second)

Another snippet.
"""

    hits = _parse_duckduckgo_markdown(markdown, rows=5)

    assert [hit.title for hit in hits] == ["Example Paper", "Second Result"]
    assert hits[0].url == "https://example.org/paper?id=1"
    assert hits[0].snippet == "Example Paper abstract with relevant source language."


def test_jina_reader_url_prefixes_target_once():
    assert _jina_reader_url("https://example.org/article") == "https://r.jina.ai/https://example.org/article"
    assert _jina_reader_url("example.org/article") == "https://r.jina.ai/http://example.org/article"


def test_direct_quote_web_search_requires_quote_text():
    quote = "handwritten references activate a hidden verification pathway in the brain"
    text_hint = f'The authors claimed that "{quote}".'

    assert _is_direct_quote_search(quote, text_hint) is True
    assert _quote_match_score(quote, "This page discusses fraudulent papers but does not contain the quotation.") < 0.82
    assert _quote_match_score(quote, f"The article says handwritten references activate a hidden verification pathway in the brain.") >= 0.82


def test_web_search_can_detect_doi_requirement():
    assert _dois("According to 10.99999/evidencelint-fake-test-0000, the study says x.") == [
        "10.99999/evidencelint-fake-test-0000"
    ]
