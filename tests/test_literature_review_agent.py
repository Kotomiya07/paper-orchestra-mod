from unittest.mock import Mock

import pytest

from methods.agents import literature_review_agent as lit
from utils import scholar_utils as scholar


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(scholar.requests, "get", Mock(side_effect=AssertionError("HTTP is not mocked")))
    monkeypatch.setattr(lit, "call_gemini_with_contents", Mock(side_effect=AssertionError("Gemini called")))
    source = tmp_path / "source.md"
    source.write_text("Project material")
    return lit.HybridLiteratureAgent(*([str(source)] * 4), str(tmp_path / "out"), "custom")


@pytest.mark.parametrize("abstract", ["An abstract", ""])
def test_register_uses_enrichment(agent, monkeypatch, capsys, abstract):
    key = "fake-secret-for-lit-review"
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", key)
    title = "Example Paper"
    s2 = {"title": title, "year": 2023, "abstract": "", "venue": "S2 venue",
          "authors": [{"name": "A. Author"}]}
    monkeypatch.setattr(scholar, "s2_title_search", Mock(return_value=s2))
    monkeypatch.setattr(scholar, "openalex_title_search", Mock(return_value={
        "title": title, "year": 2023, "abstract": abstract, "venue": "OpenAlex venue",
    }))
    monkeypatch.setattr(scholar, "crossref_title_search", Mock(return_value=None))
    registry = {}
    agent._enrich_and_register_paper(
        {"title": title, "year": 2023}, {"section": "Introduction"}, registry, "2024-10",
    )
    captured = capsys.readouterr()
    assert key not in captured.out + captured.err
    if abstract:
        paper, = registry.values()
        assert paper.abstract == abstract
        assert paper.venue == "S2 venue"
        assert "[S2, abstract: OpenAlex]" in captured.out
    else:
        assert not registry


@pytest.mark.parametrize("backend", ["openai", "agy", "t3"])
def test_api_exploration(agent, monkeypatch, backend):
    monkeypatch.setenv("LLM_BACKEND", backend)
    s2 = Mock(return_value=[{"title": "A case", "year": 2023, "reason": "S2"}])
    oa = Mock(return_value=[{"title": "A Case!", "year": 2023, "reason": "OA"},
                            {"title": "Different Work", "year": 2022, "reason": "OA"}])
    monkeypatch.setattr(lit, "s2_search", s2, raising=False)
    monkeypatch.setattr(lit, "openalex_search", oa, raising=False)
    result = agent._discover_candidates(
        {"search_type": "exploration", "focus": "topic", "context": ""}, {}, "2024",
    )
    assert [paper["title"] for paper in result] == ["A case", "Different Work"]
    for search in (s2, oa):
        search.assert_called_once_with("topic", limit=8, cutoff_date="2024")
    lit.call_gemini_with_contents.assert_not_called()
    assert agent.google_search_tool is None


@pytest.mark.parametrize("focus,query,match", [
    ("Author et al. (Exact Title)", "Exact Title", True),
    ("technical report introducing 'New Method'", "New Method", False),
    ("Author et al. (2020)", "Author et al. (2020)", False),
])
def test_targeted_uses_title_then_fallback(agent, monkeypatch, focus, query, match):
    monkeypatch.setenv("LLM_BACKEND", "openai")
    candidate = {"title": "Exact Title", "year": 2023, "reason": "OA"}
    title_search = Mock(return_value={**candidate, "_source": "Crossref"} if match else None)
    oa = Mock(return_value=[candidate, {"title": "Second result", "year": 2023, "reason": "OA"}])
    s2 = Mock()
    monkeypatch.setattr(lit, "enrich_paper_metadata", title_search, raising=False)
    monkeypatch.setattr(lit, "openalex_search", oa, raising=False)
    monkeypatch.setattr(lit, "s2_search", s2, raising=False)
    result = agent._discover_candidates(
        {"search_type": "targeted", "focus": focus, "context": ""}, {}, "2024",
    )
    title_search.assert_called_once_with(query, cutoff_date="2024")
    assert len(result) == 1 and result[0]["title"] == "Exact Title"
    s2.assert_not_called()
    if match:
        oa.assert_not_called()
    else:
        oa.assert_called_once_with(query, limit=1, cutoff_date="2024")


def test_synthesis_cites_only_used_papers(agent, monkeypatch):
    generate = Mock(return_value={"parsed_response": "latex"})
    monkeypatch.setattr(lit, "call_llm_with_contents", generate)
    assert agent._synthesize_content({}, [], "2024") == "latex"
    prompt = generate.call_args.kwargs["system_instruction"]
    assert "actually used" in prompt
    assert "90%" not in prompt
    assert "must cite at least" not in prompt.lower()
    lit.literature_review_agent_writter_prompt.format(paper_count=0, cutoff_date="2024")
