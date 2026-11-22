import json
from unittest.mock import Mock

import httpx2
import pytest

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
    monkeypatch.setattr(httpx2, "Client", factory)
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
