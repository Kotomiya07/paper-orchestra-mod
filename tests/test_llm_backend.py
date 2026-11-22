import json
from unittest.mock import Mock

import pytest

try:
    import httpx2 as http_client_lib
except ImportError:
    import httpx as http_client_lib

from utils import llm_backend_utils as llm
from utils import openai_utils


@pytest.fixture(autouse=True)
def isolated_openai(monkeypatch, tmp_path):
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "PAPER_ORCHESTRA_CPA",
                 "PAPER_ORCHESTRA_CPA_TUNNEL", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(openai_utils, "_openai_client", None)
    factory = Mock()
    monkeypatch.setattr(openai_utils, "OpenAI", factory)
    return factory


@pytest.mark.parametrize("cpa,url,expected", [
    ("0", None, {}),
    ("0", "https://compat.invalid/v1", {"base_url": "https://compat.invalid/v1"}),
    ("1", None, {"base_url": "http://m4-mac-mini-32gb.tail5df0ca.ts.net:8317/v1"}),
    ("1", "https://compat.invalid/v1", {"base_url": "https://compat.invalid/v1"}),
])
def test_base_url(monkeypatch, isolated_openai, cpa, url, expected):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-env-value")
    monkeypatch.setenv("PAPER_ORCHESTRA_CPA", cpa)
    monkeypatch.setenv("PAPER_ORCHESTRA_CPA_TUNNEL", "0")
    if url:
        monkeypatch.setenv("OPENAI_BASE_URL", url)
    openai_utils.get_openai_client()
    isolated_openai.assert_called_once_with(api_key="fake-env-value", **expected)


@pytest.mark.parametrize("override", [False, True])
def test_cpa_key_precedence(monkeypatch, tmp_path, isolated_openai, override):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "fake-settings-value"}}))
    monkeypatch.setenv("PAPER_ORCHESTRA_CPA", "1")
    if override:
        monkeypatch.setenv("OPENAI_API_KEY", "fake-env-value")
    openai_utils.get_openai_client()
    assert isolated_openai.call_args.kwargs["api_key"] == (
        "fake-env-value" if override else "fake-settings-value"
    )


@pytest.mark.parametrize("proxy,expected", [
    ("http://proxy.invalid:8080", "http://proxy.invalid:3130"),
    ("http://user:pass@proxy.invalid:8080/", "http://user:pass@proxy.invalid:3130/"),
    ("http://proxy.invalid", "http://proxy.invalid:3130"),
    ("http://[::1]:8080", "http://[::1]:3130"),
])
def test_tunnel(monkeypatch, isolated_openai, proxy, expected):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-env-value")
    monkeypatch.setenv("PAPER_ORCHESTRA_CPA_TUNNEL", "1")
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    factory = Mock()
    monkeypatch.setattr(http_client_lib, "Client", factory)
    openai_utils.get_openai_client()
    factory.assert_called_once_with(proxy=expected)
    isolated_openai.assert_called_once_with(api_key="fake-env-value", http_client=factory.return_value)


def test_tunnel_without_proxy(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-env-value")
    monkeypatch.setenv("PAPER_ORCHESTRA_CPA_TUNNEL", "1")
    with pytest.raises(ValueError, match="HTTPS_PROXY"):
        openai_utils.get_openai_client()


def test_key_not_logged(monkeypatch, isolated_openai, capsys):
    key = "fake-sensitive-value"
    monkeypatch.setenv("OPENAI_API_KEY", key)
    client = openai_utils.get_openai_client()
    client.api_key = key
    client.chat.completions.create.side_effect = RuntimeError(f"request rejected: {key}")
    with pytest.raises(RuntimeError, match="request rejected") as exc:
        openai_utils.call_openai_models_with_content("prompt", "model", max_retries=1)
    assert key not in str(exc.value)
    output = capsys.readouterr()
    assert key not in output.out + output.err
    openai_utils.reset_openai_client()
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ValueError, match="OPENAI_API_KEY") as exc:
        openai_utils.get_openai_client()
    assert key not in str(exc.value)


@pytest.mark.parametrize("extension,mime", [("png", "image/png"), ("jpg", "image/jpeg")])
def test_image_mime(tmp_path, extension, mime):
    path = tmp_path / f"image.{extension}"
    path.write_bytes(b"test")
    part = openai_utils.get_openai_image_part_from_path(str(path))[0]
    assert part["image_url"]["url"].startswith(f"data:{mime};base64,")


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "preview/DeepSeek-V4.1-Flash-1017", "gemini-3.1-pro-preview"])
def test_openai_override(monkeypatch, model):
    monkeypatch.setenv("LLM_BACKEND", "openai")
    backend = Mock()
    monkeypatch.setattr(llm, "call_openai_models_with_content", backend)
    llm.call_llm_with_contents(["first", "second"], model, system_instruction="system")
    assert backend.call_args.kwargs["content"] == "first\n\nsecond"
    assert backend.call_args.kwargs["model_name"] == model
    assert backend.call_args.kwargs["system_prompt"] == "system"
    assert backend.call_args.kwargs["result_parsing_func"] is openai_utils.parse_openai_json_results
    with pytest.raises(NotImplementedError, match="tools"):
        llm.call_llm_with_contents(["prompt"], model, tools=[object()])


@pytest.mark.parametrize("model,expected", [("gpt-example", "openai"), ("o1", "openai"),
                                           ("o3", "openai"), ("gemini", "gemini")])
def test_legacy_and_invalid_backend(monkeypatch, model, expected):
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    assert llm.resolve_text_backend(model) == expected
    monkeypatch.setenv("LLM_BACKEND", "invalid")
    with pytest.raises(ValueError, match="LLM_BACKEND"):
        llm.resolve_text_backend(model)


def test_gemini_contents(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "gemini")
    backend = Mock()
    monkeypatch.setattr(llm, "call_gemini_with_contents", backend)
    llm.call_llm_with_contents(["a", "b"], "model", system_instruction="system")
    assert [part.text for part in backend.call_args.kwargs["contents"]] == ["a", "b"]
    assert backend.call_args.kwargs["generation_configs"]["system_instruction"] == "system"


def test_empty_images_send_text(monkeypatch, isolated_openai):
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("LLM_BACKEND_MULTIMODAL", "t3")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-env-value")
    create = isolated_openai.return_value.chat.completions.create
    create.return_value.choices = [Mock(message=Mock(content="answer"))]
    llm.call_llm_with_images("prompt", [], "model", return_json=False)
    assert create.call_args.kwargs["messages"] == [{"role": "user", "content": "prompt"}]


@pytest.mark.parametrize("backend", ["openai", "agy"])
def test_agents_use_selected_text_backend(monkeypatch, tmp_path, isolated_openai, backend):
    from autoraters.agent_review import perform_review_agentreview
    from autoraters.sxs_lit_review_quality import rate_sxs_lit_review
    from methods.agents.outline_agent import OutlineAgent
    from utils.content_parsing_utils import extract_paper_title_from_citation

    monkeypatch.setenv("LLM_BACKEND", backend)
    forbidden = Mock(side_effect=AssertionError("Gemini must not be used"))
    monkeypatch.setattr("utils.gemini_utils.get_genai_client", forbidden)
    answers = ['```json\n{"result": 1}\n```', "Paper title", '{"result": 2}', '{"result": 3}']
    if backend == "openai":
        monkeypatch.setenv("OPENAI_API_KEY", "fake-env-value")
        generate = isolated_openai.return_value.chat.completions.create
        generate.side_effect = [Mock(choices=[Mock(message=Mock(content=answer))]) for answer in answers]
    else:
        generate = Mock(side_effect=answers)
        monkeypatch.setattr(llm, "call_agy_with_text_prompt", generate)
    material = tmp_path / "test.md"
    material.write_text("smoke narrative")
    model = "custom-model"
    assert OutlineAgent(model).run(*([str(material)] * 4)) == {"result": 1}
    assert extract_paper_title_from_citation("citation", model) == "Paper title"
    assert perform_review_agentreview("paper", model, num_review_ensemble=1) == {"result": 2}
    assert rate_sxs_lit_review("paper one", "paper two", model) == {"result": 3}
    assert generate.call_count == 4
    forbidden.assert_not_called()
