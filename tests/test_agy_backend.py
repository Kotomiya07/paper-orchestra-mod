import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from utils import agy_utils
from utils import llm_backend_utils as llm


def test_agy_command(monkeypatch):
    monkeypatch.setattr(agy_utils, "resolve_agy_bin", lambda: "/fake/agy")

    def run(command, **kwargs):
        assert command == ["/fake/agy", "--model", "arbitrary-model", "--output-format",
                           "json", "--disable-slash-commands", "-p", "prompt"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        cwd = Path(kwargs["cwd"])
        assert cwd.is_dir() and not cwd.is_relative_to(Path.cwd())
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "status": "SUCCESS", "response": "  hello world\n",
        }), "")

    mock = Mock(side_effect=run)
    monkeypatch.setattr(agy_utils.subprocess, "run", mock)
    assert agy_utils.call_agy_with_text_prompt("prompt", "arbitrary-model") == "hello world"
    assert not Path(mock.call_args.kwargs["cwd"]).exists()


def test_binary_search(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AGY_BIN", raising=False)
    which = Mock(return_value=None)
    monkeypatch.setattr(agy_utils.shutil, "which", which)
    with pytest.raises(RuntimeError, match="AGY_BIN"):
        agy_utils.resolve_agy_bin()
    fallback = tmp_path / ".local/bin/agy"
    fallback.parent.mkdir(parents=True)
    fallback.touch()
    assert agy_utils.resolve_agy_bin() == str(fallback)
    which.return_value = "/path/agy"
    assert agy_utils.resolve_agy_bin() == "/path/agy"
    monkeypatch.setenv("AGY_BIN", str(fallback))
    assert agy_utils.resolve_agy_bin() == str(fallback)


@pytest.mark.parametrize("failure,output,error", [
    (0, '{"status":"ERROR"}', "status"),
    (2, "", "code 2"),
    (0, "plain text", "non-JSON"),
    (0, "", "non-JSON"),
    ("timeout", "", "timed out"),
])
def test_errors(monkeypatch, failure, output, error):
    monkeypatch.setattr(agy_utils, "resolve_agy_bin", lambda: "/fake/agy")
    stderr = "discard-me" + "x" * 500
    run = Mock()
    if failure == "timeout":
        run.side_effect = subprocess.TimeoutExpired([], 1, stderr=stderr.encode())
    else:
        run.return_value = subprocess.CompletedProcess([], failure, output, stderr)
    monkeypatch.setattr(agy_utils.subprocess, "run", run)
    with pytest.raises(RuntimeError, match=error) as exc:
        agy_utils.call_agy_with_text_prompt("prompt", "model")
    assert str(exc.value).endswith("x" * 500)
    assert "discard-me" not in str(exc.value)


def test_limit_measured_in_bytes(monkeypatch):
    run = Mock()
    monkeypatch.setattr(agy_utils.subprocess, "run", run)
    with pytest.raises(ValueError, match="bytes"):
        agy_utils.call_agy_with_text_prompt("\u3042" * 200_000, "model")
    run.assert_not_called()


def test_agy_routing(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "agy")
    monkeypatch.delenv("LLM_BACKEND_MULTIMODAL", raising=False)
    backend = Mock(return_value='```json\n{"answer": 1}\n```')
    monkeypatch.setattr(llm, "call_agy_with_text_prompt", backend)
    assert llm.call_llm_with_text_prompt("hello", "model")["parsed_response"] == {"answer": 1}
    llm.call_llm_with_contents(["a", "b"], "model", system_instruction="system")
    assert backend.call_args.args == ("System Instructions:\nsystem\n\na\n\nb", "model")
    llm.call_llm_with_images("empty", [], "model")
    assert backend.call_args.args == ("empty", "model")
    with pytest.raises(NotImplementedError):
        llm.call_llm_with_images("hello", ["image.png"], "model")
    with pytest.raises(NotImplementedError):
        llm.call_llm_with_pdf("paper.pdf", "hello", "model")
    with pytest.raises(NotImplementedError):
        llm.call_llm_with_contents(["hello"], "model", tools=[object()])
