from app.audit import run_audit
from app.main import _web_source_search_jobs
from app.models import OnlineLookupResult, QuoteStatus, Source, SourceMetadata, SourcePage, Verdict
from app.parsing import parse_text
from app.sources import source_from_text


def test_parse_text_ignores_short_title_quotes():
    parsed = parse_text('The joystick or keyboard can move “Little Red-Cap” through the route.')

    assert len(parsed) == 1
    assert parsed[0].quotes == []


def test_parse_text_detects_citation_quote_and_claim():
    parsed = parse_text('Bloom et al. (2015) found that "home working led to a 13% performance increase."')

    assert len(parsed) == 1
    assert parsed[0].citations == ["Bloom et al. (2015)"]
    assert parsed[0].quotes == ["home working led to a 13% performance increase."]
    assert parsed[0].likely_factual_claim is True


def test_parse_text_keeps_ellipsis_inside_quotes():
    parsed = parse_text('Bloom et al. (2015) found that "home working ... 13% performance increase".')

    assert len(parsed) == 1
    assert parsed[0].quotes == ["home working ... 13% performance increase"]


def test_parse_text_keeps_page_citation_in_one_sentence():
    parsed = parse_text(
        'They can include audio, video and visual materials without becoming “as complex or as potentially expensive to develop as an app” (Hall, 2013, p. 120).'
    )

    assert len(parsed) == 1
    assert parsed[0].citations == ["(Hall, 2013, p. 120)"]
    assert parsed[0].quotes == ["as complex or as potentially expensive to develop as an app"]


def test_parse_text_detects_author_with_year_only_parenthetical():
    parsed = parse_text(
        "Hall argues that enhanced e-books occupy “a middle ground between the e-book on one side and the app on the other” (2013, p. 120)."
    )

    assert len(parsed) == 1
    assert parsed[0].citations == ["(2013, p. 120)"]
    assert parsed[0].quotes == ["a middle ground between the e-book on one side and the app on the other"]


def test_parse_text_detects_long_author_year_parenthetical():
    parsed = parse_text(
        "Kucirkova argues that children’s digital books need to be evaluated in relation to parent-child interaction, the child’s age, and the fit between narrative content and interactive or multimedia features (2019)."
    )

    assert parsed[0].citations == ["(2019)"]


def test_parse_text_detects_named_survey_year_citation():
    parsed = parse_text(
        "The government’s Community Life Survey 2024/25 reports that 9% of adults in England experience “high loneliness” (indirect loneliness score 8–9), indicating a sizeable population-level risk."
    )

    assert parsed[0].citations == ["Community Life Survey 2024/25"]
    assert parsed[0].quotes == []
    assert parsed[0].likely_factual_claim is True


def test_parse_text_detects_named_health_survey_year_citation():
    parsed = parse_text(
        "The NHS Adult Psychiatric Morbidity Survey 2023/24 reports that 20.2% of adults in England meet criteria for a “common mental health condition”."
    )

    assert parsed[0].citations == ["The NHS Adult Psychiatric Morbidity Survey 2023/24"]
    assert parsed[0].likely_factual_claim is True


def test_parse_text_ignores_reference_section_as_body_sentences():
    parsed = parse_text(
        """
        Rendall and Voss (2099) found an impossible effect.

        References
        Rendall, P., & Voss, M. (2099). Notebook color and hidden citation pathways. Journal of Imaginary Academic Metrics.
        """
    )

    assert len(parsed) == 1
    assert "References" not in parsed[0].text


def test_audit_verifies_quote_and_retrieves_evidence():
    source = Source(
        id="src1",
        kind="pdf",
        name="sample.pdf",
        pages=[
            SourcePage(
                page_number=1,
                text="The experiment found that home working led to a 13% performance increase among call center workers.",
            )
        ],
    )
    result = run_audit(
        text='Bloom et al. (2015) found that "home working led to a 13% performance increase" among call center workers.',
        sources=[source],
    )

    assert result.summary.total_sentences == 1
    assert result.summary.quotes_verified == 1
    assert result.sentences[0].verdict in {Verdict.SUPPORTED, Verdict.NEEDS_REVIEW}
    assert result.sentences[0].evidence


def test_audit_does_not_mark_page_citation_as_missing():
    source = Source(
        id="src1",
        kind="pdf",
        name="ebook.pdf",
        pages=[
            SourcePage(
                page_number=120,
                text="Enhanced ebooks can include audio, video and visual materials without becoming as complex or as potentially expensive to develop as an app.",
            )
        ],
    )
    result = run_audit(
        text='They can include audio, video and visual materials without becoming “as complex or as potentially expensive to develop as an app” (Hall, 2013, p. 120).',
        sources=[source],
    )

    assert result.summary.total_sentences == 1
    assert result.summary.missing_citations == 0
    assert result.summary.cited_sentences == 1
    assert result.sentences[0].verdict != Verdict.CITATION_MISSING
    assert result.sentences[0].quote_checks[0].status == QuoteStatus.VERIFIED


def test_audit_flags_missing_citation_for_factual_claim():
    result = run_audit("A 2020 study found that remote work increased productivity by 22%.", sources=[])

    assert result.summary.missing_citations == 1
    assert result.sentences[0].verdict == Verdict.CITATION_MISSING


def test_audit_does_not_mark_named_survey_report_as_missing_citation():
    result = run_audit(
        "The government’s Community Life Survey 2024/25 reports that 9% of adults in England experience “high loneliness” (indirect loneliness score 8–9), indicating a sizeable population-level risk.",
        sources=[],
    )

    assert result.summary.cited_sentences == 1
    assert result.summary.missing_citations == 0
    assert result.sentences[0].verdict != Verdict.CITATION_MISSING


def test_audit_does_not_support_named_report_with_unrelated_source():
    source = Source(
        id="src1",
        kind="web",
        name="Community Life Survey 2024/25",
        metadata=SourceMetadata(title="Community Life Survey 2024/25 annual publication"),
        pages=[SourcePage(text="Adults in England reported high levels of indirect loneliness in 2024/25.")],
    )
    result = run_audit(
        "The Zephyr Companion Safety Report 2024 reports that 88% of prototype chatbots can detect sadness from keyboard colour.",
        sources=[source],
        online_lookup=[OnlineLookupResult(query="Zephyr Companion Safety Report 2024", status="Full Text Parsed", source_id="src1")],
    )

    assert result.sentences[0].verdict == Verdict.NEEDS_REVIEW
    assert result.sentences[0].evidence == []


def test_audit_allows_author_year_web_source_with_marker_in_text():
    source = Source(
        id="src1",
        kind="web",
        name="Does Working from Home Work? Evidence from a Chinese Experiment",
        metadata=SourceMetadata(title="Does Working from Home Work? Evidence from a Chinese Experiment"),
        pages=[
            SourcePage(
                text="Nicholas Bloom and colleagues published the 2015 study. Home working led to a 13% performance increase among call center workers.",
            )
        ],
    )
    result = run_audit(
        'Bloom et al. (2015) found that "home working led to a 13% performance increase" among call center workers.',
        sources=[source],
        online_lookup=[OnlineLookupResult(query="Bloom 2015 home working", status="Full Text Parsed", source_id="src1")],
    )

    assert result.sentences[0].verdict != Verdict.CITATION_MISSING
    assert result.sentences[0].quote_checks[0].status == QuoteStatus.VERIFIED


def test_web_jobs_for_named_report_use_short_searchable_queries():
    parsed = parse_text(
        "The government’s Community Life Survey 2024/25 reports that 9% of adults in England experience “high loneliness” (indirect loneliness score 8–9), indicating a sizeable population-level risk. "
        "The NHS Adult Psychiatric Morbidity Survey 2023/24 reports that 20.2% of adults in England meet criteria for a “common mental health condition”."
    )

    queries = [job.query for job in _web_source_search_jobs(parsed)]

    assert "Community Life Survey 2024 2025 high loneliness 9 8" in queries
    assert any(
        query.startswith("NHS Adult Psychiatric Morbidity Survey 2023 2024 common mental health condition")
        and "20.2" in query
        for query in queries
    )


def test_audit_flags_future_citation_year_as_invalid_even_with_matching_text():
    source = Source(
        id="src1",
        kind="text",
        name="future-source.txt",
        pages=[SourcePage(text="Students who used blue notebooks experienced a 73.4% increase in citation accuracy.")],
    )
    result = run_audit(
        "Rendall and Voss (2099) found that students who used blue notebooks experienced a 73.4% increase in citation accuracy.",
        sources=[source],
    )

    assert result.sentences[0].verdict == Verdict.CITATION_MISSING
    assert result.summary.missing_citations == 1
    assert "future" in result.sentences[0].notes[0]


def test_online_unconfirmed_cited_claim_needs_review_instead_of_supported_by_wrong_source():
    wrong_online_source = Source(
        id="src2",
        kind="web",
        name="GPT-fabricated scientific papers on Google Scholar",
        metadata=SourceMetadata(title="GPT-fabricated scientific papers on Google Scholar", url="https://example.org/wrong"),
        pages=[SourcePage(text="Our study focused on a selection of papers that were easily recognizable as fraudulent.")],
    )
    result = run_audit(
        "According to 10.99999/evidencelint-fake-test-0000, the study was published in the Journal of Imaginary Academic Metrics.",
        sources=[wrong_online_source],
        online_lookup=[OnlineLookupResult(query="fake", status="Full Text Parsed", source_id="src2")],
    )

    assert result.sentences[0].verdict == Verdict.NEEDS_REVIEW
    assert result.sentences[0].evidence == []
    assert "Review this source manually" in result.sentences[0].notes[-1]


def test_uncited_claim_does_not_use_automatic_online_source_as_evidence():
    online_source = Source(
        id="src2",
        kind="web",
        name="Little Red-Cap",
        metadata=SourceMetadata(title="Little Red-Cap", url="https://example.org/red-cap"),
        pages=[SourcePage(text="This page says digital books improve parent-child reading outcomes by 42 percent.")],
    )
    result = run_audit(
        "Children's digital books improve parent-child reading outcomes by 42%.",
        sources=[online_source],
        online_lookup=[OnlineLookupResult(query="Little Red-Cap", status="Full Text Parsed", source_id="src2")],
    )

    assert result.sentences[0].verdict == Verdict.CITATION_MISSING
    assert result.sentences[0].evidence == []


def test_analysis_scope_quoted_only_skips_unquoted_data_claim():
    result = run_audit(
        'Bloom et al. (2015) found that "home working led to a 13% performance increase". A pilot study reduced referrals by 41%.',
        sources=[],
        analysis_scope={"quoted"},
    )

    assert result.summary.likely_claims == 1
    assert result.sentences[0].verdict == Verdict.NO_EVIDENCE
    assert result.sentences[1].verdict == Verdict.NO_CHECK_NEEDED


def test_analysis_scope_uncited_only_skips_cited_sentence():
    result = run_audit(
        "Bloom et al. (2015) found a remote work effect. A small pilot study reduced referrals by 41%.",
        sources=[],
        analysis_scope={"uncited"},
    )

    assert result.summary.likely_claims == 1
    assert result.summary.cited_sentences == 0
    assert result.sentences[0].verdict == Verdict.NO_CHECK_NEEDED
    assert result.sentences[1].verdict == Verdict.CITATION_MISSING


def test_analysis_scope_cited_only_checks_citation_markers():
    result = run_audit(
        "Bloom et al. (2015) found a remote work effect. A small pilot study reduced referrals by 41%.",
        sources=[],
        analysis_scope={"cited"},
    )

    assert result.summary.likely_claims == 1
    assert result.summary.cited_sentences == 1
    assert result.sentences[0].verdict == Verdict.NO_EVIDENCE
    assert result.sentences[1].verdict == Verdict.NO_CHECK_NEEDED


def test_uploaded_source_support_takes_precedence_over_failed_online_lookup():
    uploaded_source = Source(
        id="src1",
        kind="text",
        name="uploaded.txt",
        pages=[SourcePage(text="The uploaded source says custom source text works.")],
    )
    result = run_audit(
        "Smith et al. (2020) found that custom source text works.",
        sources=[uploaded_source],
        online_lookup=[OnlineLookupResult(query="Smith 2020", status="Lookup Failed")],
    )

    assert result.sentences[0].verdict == Verdict.SUPPORTED
    assert result.sentences[0].evidence[0].source_id == "src1"


def test_quote_numeric_mismatch_is_not_treated_as_verified():
    source = Source(
        id="src1",
        kind="text",
        name="sample.txt",
        pages=[SourcePage(text="The experiment found that home working led to a 13% performance increase.")],
    )
    result = run_audit(
        text='Bloom et al. (2015) found that "home working led to a 31% performance increase".',
        sources=[source],
    )

    assert result.sentences[0].quote_checks[0].status == QuoteStatus.MISMATCH


def test_quote_allows_punctuation_and_ocr_normalization():
    source = Source(
        id="src1",
        kind="text",
        name="sample.txt",
        pages=[SourcePage(text="The experiment found that home-working led to a 13% performance increase.")],
    )
    result = run_audit(
        text='Bloom et al. (2015) found that "home working led to a 13% performance increase".',
        sources=[source],
    )

    assert result.sentences[0].quote_checks[0].status == QuoteStatus.VERIFIED


def test_quote_detects_ellipsis_as_slight_modification():
    source = Source(
        id="src1",
        kind="text",
        name="sample.txt",
        pages=[SourcePage(text="The experiment found that home working led to a 13% performance increase.")],
    )
    result = run_audit(
        text='Bloom et al. (2015) found that "home working ... 13% performance increase".',
        sources=[source],
    )

    assert result.sentences[0].quote_checks[0].status == QuoteStatus.SLIGHTLY_MODIFIED


def test_quote_detects_bracket_insertions_as_slight_modification():
    source = Source(
        id="src1",
        kind="text",
        name="sample.txt",
        pages=[SourcePage(text="The experiment found that home working led to a 13% performance increase.")],
    )
    result = run_audit(
        text='Bloom et al. (2015) found that "home working [from home] led to a 13% performance increase".',
        sources=[source],
    )

    assert result.sentences[0].quote_checks[0].status == QuoteStatus.SLIGHTLY_MODIFIED


def test_source_from_text_extracts_demo_metadata():
    source = source_from_text(
        """
        Title: Does Working from Home Work? Evidence from a Chinese Experiment
        Authors: Nicholas Bloom, James Liang
        Year: 2015
        DOI: 10.1093/qje/qju032
        """,
        "src1",
        "demo_source.txt",
    )

    assert source.metadata.title == "Does Working from Home Work? Evidence from a Chinese Experiment"
    assert source.metadata.year == 2015
    assert source.metadata.doi == "10.1093/qje/qju032"
