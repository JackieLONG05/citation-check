from app.citations import extract_citation_mentions, extract_reference_entries, link_numeric_citations


def test_extract_author_year_mentions():
    text = "Smith et al. (2020) found one pattern, while other work disagrees (Jones, 2021; Lee & Kim, 2022)."

    mentions = extract_citation_mentions(text)

    assert [mention.author_hint for mention in mentions[:3]] == ["Smith", "Jones", "Lee"]
    assert [mention.year for mention in mentions[:3]] == [2020, 2021, 2022]


def test_extract_author_from_year_only_parenthetical():
    text = "Hall argues that enhanced e-books occupy a middle ground (2013, p. 120)."

    mentions = extract_citation_mentions(text)

    assert mentions[0].author_hint == "Hall"
    assert mentions[0].year == 2013


def test_extract_numeric_citation_ranges():
    mentions = extract_citation_mentions("Prior work supports this claim [1, 3-5].")

    assert mentions[0].kind == "numeric"
    assert mentions[0].reference_numbers == [1, 3, 4, 5]


def test_parse_numbered_references_and_link_numeric_citations():
    text = """
    This is supported by prior work [1].

    References
    [1] Piwowar, H., Priem, J. (2018). The state of OA: a large-scale analysis. PeerJ. doi:10.7717/peerj.4375
    [2] Smith, A. (2020). Another paper. Journal of Tests.
    """

    mentions = extract_citation_mentions(text)
    references = extract_reference_entries(text)
    linked = link_numeric_citations(mentions, references)

    assert references[0].index == 1
    assert references[0].year == 2018
    assert references[0].doi == "10.7717/peerj.4375"
    assert "The state of OA" in (references[0].title or "")
    assert linked[1].doi == "10.7717/peerj.4375"


def test_parse_apa_like_reference_without_numeric_index():
    text = """
    References
    Bloom, N., Liang, J., Roberts, J., & Ying, Z. J. (2015). Does working from home work? Evidence from a Chinese experiment. Quarterly Journal of Economics. https://doi.org/10.1093/qje/qju032
    """

    references = extract_reference_entries(text)

    assert references[0].index is None
    assert references[0].year == 2015
    assert references[0].doi == "10.1093/qje/qju032"
    assert references[0].title == "Does working from home work? Evidence from a Chinese experiment"
