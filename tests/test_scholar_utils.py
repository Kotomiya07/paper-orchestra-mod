import importlib
from unittest.mock import Mock

import pytest

from utils import scholar_utils as scholar


def response(payload, status=200):
    return Mock(status_code=status, json=Mock(return_value=payload))


def test_import_does_not_log_s2_key(monkeypatch, capsys):
    key = "fake-scholar-key"
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", key)
    importlib.reload(scholar)
    captured = capsys.readouterr()
    assert key not in captured.out + captured.err


@pytest.mark.parametrize("index,expected", [
    ({"B": [2], "A": [0, 1]}, "A A B"), (None, ""), ({}, ""),
])
def test_abstract(index, expected):
    assert scholar.reconstruct_openalex_abstract(index) == expected


def openalex_work(title="A paper", year=2023, date="2023-05-10"):
    return {
        "title": title, "publication_year": year, "publication_date": date,
        "authorships": [{"author": {"display_name": "A. Author"}}],
        "primary_location": {"source": {"display_name": "Journal"}},
        "biblio": {"volume": "8", "first_page": "1", "last_page": "9"},
        "abstract_inverted_index": {"text": [1], "Sample": [0]}, "cited_by_count": 4,
    }


@pytest.mark.parametrize("email", [None, "reader@example.invalid"])
def test_openalex_title_search(monkeypatch, email):
    monkeypatch.delenv("OPENALEX_EMAIL", raising=False)
    if email:
        monkeypatch.setenv("OPENALEX_EMAIL", email)
    get = Mock(return_value=response({"results": [
        openalex_work(year=2025, date="2025-02-01"),
        openalex_work(year=2022, date="2022-05-10"),
        openalex_work(title="A papers"),
    ]}))
    monkeypatch.setattr(scholar.requests, "get", get)
    assert scholar.openalex_title_search("A paper", year_hint=2023, cutoff_date="2024-01") == {
        "title": "A papers", "authors": [{"name": "A. Author"}], "venue": "Journal",
        "year": 2023, "abstract": "Sample text", "citationCount": 4,
        "journal": {"name": "Journal", "volume": "8", "pages": "1-9"},
        "publicationDate": "2023-05-10",
    }
    assert get.call_args.args == ("https://api.openalex.org/works",)
    params = {"search": "A paper", "per-page": 3}
    if email:
        params["mailto"] = email
    assert get.call_args.kwargs["params"] == params
    assert scholar.openalex_title_search("Unrelated words", 2023) is None


@pytest.mark.parametrize("abstract,expected", [
    ("<jats:p>Learning <jats:italic>with</jats:italic> &amp; from data.</jats:p>",
     "Learning with & from data."),
    (None, ""),
])
def test_crossref_title_search(monkeypatch, abstract, expected):
    work = {
        "title": ["A paper"], "author": [{"given": "A.", "family": "Author"}],
        "container-title": ["Journal"], "published": {"date-parts": [[2023, 6]]},
        "abstract": abstract, "is-referenced-by-count": 2, "volume": "3", "page": "1-5",
    }
    get = Mock(return_value=response({"message": {"items": [work]}}))
    monkeypatch.setattr(scholar.requests, "get", get)
    assert scholar.crossref_title_search("A paper", cutoff_date="2023-07") == {
        "title": "A paper", "authors": [{"name": "A. Author"}], "venue": "Journal",
        "year": 2023, "abstract": expected, "citationCount": 2,
        "journal": {"name": "Journal", "volume": "3", "pages": "1-5"},
        "publicationDate": "2023-06",
    }
    assert get.call_args.args == ("https://api.crossref.org/works",)
    assert get.call_args.kwargs["params"] == {"query.title": "A paper", "rows": 3}
    assert scholar.crossref_title_search("A paper", cutoff_date="2023-05-25") is None


@pytest.mark.parametrize("matches,expected", [
    (["text", None, None], ("S2", "S2", 1)),
    (["", "text", None], ("S2", "OpenAlex", 2)),
    ([None, "text", None], ("OpenAlex", "OpenAlex", 2)),
    ([None, "", "text"], ("OpenAlex", "Crossref", 3)),
    ([None, None, "text"], ("Crossref", "Crossref", 3)),
    ([None, None, None], None),
])
def test_enrichment(monkeypatch, matches, expected):
    searchers = []
    for method, source, abstract in zip(
        ["s2_title_search", "openalex_title_search", "crossref_title_search"],
        ["S2", "OpenAlex", "Crossref"], matches,
    ):
        match = {"title": "A paper", "venue": source, "year": 2023, "abstract": abstract}
        search = Mock(return_value=match if abstract is not None else None)
        monkeypatch.setattr(scholar, method, search)
        searchers.append(search)
    found = scholar.enrich_paper_metadata("A paper", 2023, "2024")
    if expected is None:
        assert found is None
        assert sum(search.call_count for search in searchers) == 3
    else:
        source, abstract_source, calls = expected
        assert found == {"title": "A paper", "venue": source, "year": 2023, "abstract": "text",
                         "_source": source, "_abstract_source": abstract_source}
        assert sum(search.call_count for search in searchers) == calls


@pytest.mark.parametrize("name,payload,source,limit_param", [
    ("s2_search", {"data": [
        {"title": "Small venue", "year": 2023, "citationCount": 0},
        {"title": "Too new", "year": 2024, "publicationDate": "2024-06-02"},
    ]}, "S2", "limit"),
    ("openalex_search", {"results": [
        openalex_work("Small venue"), openalex_work("Too new", 2024, "2024-06-02"),
    ]}, "OpenAlex", "per-page"),
])
def test_discovery(monkeypatch, name, payload, source, limit_param):
    get = Mock(return_value=response(payload))
    monkeypatch.setattr(scholar.requests, "get", get)
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "fake-test-key")
    candidates = getattr(scholar, name)("topic", 8, cutoff_date="2024-06-01")
    assert len(candidates) == 1
    assert set(candidates[0]) == {"title", "year", "reason"}
    assert candidates[0]["title"] == "Small venue"
    assert candidates[0]["year"] == 2023
    assert source in candidates[0]["reason"]
    assert get.call_args.kwargs["params"][limit_param] == 8
    if source == "S2":
        assert get.call_args.kwargs["headers"] == {"X-API-KEY": "fake-test-key"}


@pytest.mark.parametrize("published,year,cutoff,expected", [
    ("2024-01-31", 2024, "2024-02", True),
    ("2024-02-01", 2024, "2024-02", False),
    ("2024-02-02", 2024, "2024-02-01", False),
    ("2024", 2024, "2024", True),
    (None, 2025, "2024", False),
    (None, 2024, "2024-01", False),
])
def test_cutoff(published, year, cutoff, expected):
    assert scholar.is_date_valid(published, year, cutoff) is expected
