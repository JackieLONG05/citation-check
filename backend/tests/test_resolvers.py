import xml.etree.ElementTree as ET

from app.resolvers.arxiv import _candidate_from_entry as _arxiv_candidate_from_entry
from app.resolvers.crossref import _candidate_from_message
from app.resolvers.datacite import _candidate_from_item as _datacite_candidate_from_item
from app.resolvers.doaj import _candidate_from_result as _doaj_candidate_from_result
from app.resolvers.europe_pmc import _candidate_from_result as _europe_pmc_candidate_from_result
from app.resolvers.openalex import OpenAlexResolver, _candidate_from_work
from app.resolvers.openaire import _candidate_from_result as _openaire_candidate_from_result
from app.resolvers.pubmed import _candidate_from_article as _pubmed_candidate_from_article
from app.resolvers.semantic_scholar import _candidate_from_paper
from app.resolvers.utils import extract_dois, normalize_doi


def test_extract_dois_normalizes_unique_values():
    text = "See https://doi.org/10.1093/QJE/QJU032 and doi:10.1093/qje/qju032."

    assert extract_dois(text) == ["10.1093/qje/qju032"]


def test_openalex_candidate_normalization():
    candidate = _candidate_from_work(
        "10.1234/example",
        {
            "id": "https://openalex.org/W123",
            "doi": "https://doi.org/10.1234/example",
            "title": "Example Work",
            "publication_year": 2020,
            "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
            "primary_location": {"source": {"display_name": "Journal of Tests"}},
            "open_access": {"is_oa": True},
            "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
            "abstract_inverted_index": {"This": [0], "works": [1]},
        },
    )

    assert candidate.provider == "OpenAlex"
    assert candidate.doi == "10.1234/example"
    assert candidate.authors == ["Ada Lovelace"]
    assert candidate.venue == "Journal of Tests"
    assert candidate.abstract == "This works"


def test_openalex_full_text_falls_back_to_locations():
    candidate = _candidate_from_work(
        "10.1234/example",
        {
            "id": "https://openalex.org/W123",
            "doi": "https://doi.org/10.1234/example",
            "title": "Example Work",
            "locations": [
                {"is_oa": False, "pdf_url": "https://example.org/closed.pdf"},
                {"is_oa": True, "landing_page_url": "https://example.org/open", "license": "cc-by"},
            ],
        },
    )

    location = OpenAlexResolver().full_text_location(candidate)

    assert location is not None
    assert location.provider == "OpenAlex"
    assert location.url == "https://example.org/open"
    assert location.kind == "landing_page"


def test_normalize_doi_strips_prefix_and_punctuation():
    assert normalize_doi("https://doi.org/10.1234/ABC.") == "10.1234/abc"


def test_crossref_search_candidate_without_doi_does_not_invent_unknown_doi():
    candidate = _candidate_from_message(
        None,
        {
            "title": ["No DOI Paper"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "issued": {"date-parts": [[2022]]},
            "URL": "https://example.org/no-doi",
        },
        confidence=0.5,
    )

    assert candidate.doi is None
    assert candidate.url == "https://example.org/no-doi"
    assert candidate.id == "crossref:No DOI Paper"


def test_semantic_scholar_candidate_normalization():
    candidate = _candidate_from_paper(
        {
            "paperId": "abc123",
            "title": "Example Semantic Scholar Paper",
            "year": 2024,
            "venue": "Test Venue",
            "url": "https://www.semanticscholar.org/paper/abc123",
            "authors": [{"name": "Ada Lovelace"}],
            "externalIds": {"DOI": "https://doi.org/10.5555/Example"},
            "openAccessPdf": {"url": "https://example.org/paper.pdf", "license": "cc-by"},
        }
    )

    assert candidate.provider == "Semantic Scholar"
    assert candidate.doi == "10.5555/example"
    assert candidate.authors == ["Ada Lovelace"]
    assert candidate.metadata["openAccessPdf"]["url"] == "https://example.org/paper.pdf"


def test_europe_pmc_candidate_normalization():
    candidate = _europe_pmc_candidate_from_result(
        {
            "id": "12345",
            "source": "MED",
            "pmid": "12345",
            "pmcid": "PMC123456",
            "title": "Example Europe PMC Paper",
            "authorList": {"author": [{"fullName": "Ada Lovelace"}]},
            "pubYear": "2024",
            "doi": "https://doi.org/10.1234/Example",
            "journalTitle": "Journal of Tests",
            "abstractText": "This paper reports a useful test result.",
            "isOpenAccess": "Y",
            "fullTextUrlList": {
                "fullTextUrl": [
                    {
                        "url": "https://example.org/paper.pdf",
                        "documentStyle": "pdf",
                        "availabilityCode": "OA",
                    }
                ]
            },
        }
    )

    assert candidate.provider == "Europe PMC"
    assert candidate.doi == "10.1234/example"
    assert candidate.authors == ["Ada Lovelace"]
    assert candidate.venue == "Journal of Tests"
    assert candidate.metadata["pmcid"] == "PMC123456"
    assert candidate.metadata["fullTextUrlList"]["fullTextUrl"][0]["availabilityCode"] == "OA"


def test_arxiv_candidate_normalization():
    xml = """\
    <entry xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <id>http://arxiv.org/abs/2401.00001v2</id>
      <published>2024-01-01T00:00:00Z</published>
      <updated>2024-01-02T00:00:00Z</updated>
      <title>Example arXiv Paper</title>
      <summary>
        This paper reports an example result.
      </summary>
      <author><name>Ada Lovelace</name></author>
      <arxiv:doi>10.48550/arXiv.2401.00001</arxiv:doi>
      <arxiv:primary_category term="cs.CL" />
      <category term="cs.CL" />
      <link href="http://arxiv.org/pdf/2401.00001v2" title="pdf" type="application/pdf" />
    </entry>
    """

    candidate = _arxiv_candidate_from_entry(ET.fromstring(xml))

    assert candidate.provider == "arXiv"
    assert candidate.id == "arxiv:2401.00001v2"
    assert candidate.doi == "10.48550/arxiv.2401.00001"
    assert candidate.authors == ["Ada Lovelace"]
    assert candidate.year == 2024
    assert candidate.abstract == "This paper reports an example result."
    assert candidate.metadata["primary_category"] == "cs.CL"
    assert candidate.metadata["pdf_url"] == "http://arxiv.org/pdf/2401.00001v2"


def test_datacite_candidate_normalization():
    candidate = _datacite_candidate_from_item(
        {
            "id": "10.1234/example",
            "attributes": {
                "doi": "10.1234/Example",
                "titles": [{"title": "Example Dataset"}],
                "creators": [{"name": "Lovelace, Ada"}],
                "publicationYear": 2024,
                "publisher": "Example Repository",
                "url": "https://example.org/dataset",
                "descriptions": [{"description": "<p>Dataset abstract text.</p>", "descriptionType": "Abstract"}],
                "types": {"resourceTypeGeneral": "Dataset"},
            },
        }
    )

    assert candidate.provider == "DataCite"
    assert candidate.doi == "10.1234/example"
    assert candidate.title == "Example Dataset"
    assert candidate.authors == ["Lovelace, Ada"]
    assert candidate.abstract == "Dataset abstract text."


def test_doaj_candidate_normalization():
    candidate = _doaj_candidate_from_result(
        {
            "id": "abc",
            "bibjson": {
                "title": "Example Open Access Article",
                "year": "2025",
                "author": [{"name": "Ada Lovelace"}],
                "identifier": [{"type": "doi", "id": "10.5555/Example"}],
                "journal": {"title": "Open Journal", "publisher": "OA Press"},
                "abstract": "Article abstract.",
                "link": [{"url": "https://example.org/article.pdf", "content_type": "PDF", "type": "fulltext"}],
            },
        }
    )

    assert candidate.provider == "DOAJ"
    assert candidate.doi == "10.5555/example"
    assert candidate.venue == "Open Journal"
    assert candidate.url == "https://example.org/article.pdf"


def test_pubmed_candidate_normalization():
    xml = """\
    <PubmedArticle>
      <MedlineCitation>
        <PMID>123456</PMID>
        <Article>
          <ArticleTitle>Example PubMed Article</ArticleTitle>
          <Abstract><AbstractText Label="RESULTS">Useful biomedical result.</AbstractText></Abstract>
          <Journal>
            <Title>Journal of Medicine</Title>
            <ISOAbbreviation>J Med</ISOAbbreviation>
            <JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue>
          </Journal>
          <AuthorList>
            <Author><ForeName>Ada</ForeName><LastName>Lovelace</LastName></Author>
          </AuthorList>
          <ELocationID EIdType="doi">10.7777/example</ELocationID>
        </Article>
      </MedlineCitation>
      <PubmedData>
        <ArticleIdList>
          <ArticleId IdType="pubmed">123456</ArticleId>
          <ArticleId IdType="doi">10.7777/example</ArticleId>
          <ArticleId IdType="pmc">PMC123</ArticleId>
        </ArticleIdList>
      </PubmedData>
    </PubmedArticle>
    """

    candidate = _pubmed_candidate_from_article(ET.fromstring(xml))

    assert candidate.provider == "PubMed"
    assert candidate.doi == "10.7777/example"
    assert candidate.authors == ["Ada Lovelace"]
    assert candidate.abstract == "RESULTS: Useful biomedical result."
    assert candidate.metadata["pmcid"] == "PMC123"


def test_openaire_candidate_normalization():
    candidate = _openaire_candidate_from_result(
        {
            "id": "openaire-id",
            "mainTitle": "Example Repository Article",
            "authors": [{"fullName": "Ada Lovelace"}],
            "publicationDate": "2024-02-03",
            "publisher": "Repository Press",
            "descriptions": ["<jats:p>Repository abstract.</jats:p>"],
            "pids": [{"scheme": "doi", "value": "10.8888/example"}],
            "instances": [
                {
                    "license": "CC BY",
                    "accessRight": {"label": "OPEN"},
                    "urls": ["https://example.org/repository.pdf"],
                }
            ],
        }
    )

    assert candidate.provider == "OpenAIRE"
    assert candidate.doi == "10.8888/example"
    assert candidate.year == 2024
    assert candidate.url == "https://doi.org/10.8888/example"
    assert candidate.abstract == "Repository abstract."
