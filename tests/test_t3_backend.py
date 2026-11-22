from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
from threading import Event
from unittest.mock import Mock

import pytest

from utils import t3_utils
from utils import llm_backend_utils as llm


@pytest.mark.parametrize("spec,default,expected", [
    ("antigravity:gemini-4-neon", None, ("antigravity", "gemini-4-neon")),
    ("acpRegistry_devin:gpt-6-2-sol-medium", None, ("acpRegistry_devin", "gpt-6-2-sol-medium")),
    ("gemini-4-neon", None, ("antigravity", "gemini-4-neon")),
    ("gemini-4-neon", "other", ("other", "gemini-4-neon")),
])
def test_model_spec(monkeypatch, spec, default, expected):
    monkeypatch.delenv("T3_DEFAULT_PROVIDER", raising=False)
    if default:
        monkeypatch.setenv("T3_DEFAULT_PROVIDER", default)
    assert t3_utils.parse_t3_model_spec(spec) == expected


def test_binary_search(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("T3_BIN", raising=False)
    which = Mock(return_value=None)
    monkeypatch.setattr(t3_utils.shutil, "which", which)
    with pytest.raises(RuntimeError, match="T3_BIN"):
        t3_utils.resolve_t3_bin()
    fallback = tmp_path / ".local/bin/t3"
    fallback.parent.mkdir(parents=True)
    fallback.touch()
    assert t3_utils.resolve_t3_bin() == str(fallback)
    which.return_value = "/path/t3"
    assert t3_utils.resolve_t3_bin() == "/path/t3"
    monkeypatch.setenv("T3_BIN", str(fallback))
    assert t3_utils.resolve_t3_bin() == str(fallback)


def result(payload):
    return subprocess.CompletedProcess([], 0, json.dumps(payload), "")


@pytest.fixture
def project(monkeypatch, tmp_path):
    monkeypatch.setenv("T3_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(t3_utils, "resolve_t3_bin", lambda: "/fake/t3")
    monkeypatch.setattr(t3_utils.time, "sleep", Mock())
    return tmp_path


@pytest.mark.parametrize("status", [
    {"thread": {"status": "completed"}}, {"status": "completed"},
    {"thread": {"status": "idle", "activeRunId": None, "runCount": 1}},
])
def test_copies_materials_and_reads_answer(monkeypatch, project, status):
    sources = []
    for directory, filename in [("a", "x.png"), ("b", "x.png"), ("c", "y.pdf")]:
        path = project / directory / filename
        path.parent.mkdir()
        path.write_text(directory)
        sources.append(path)

    def start(command, **kwargs):
        call_dir, = (project / "po_calls").iterdir()
        materials = sorted((call_dir / "materials").iterdir())
        assert sorted(path.read_text() for path in materials) == ["a", "b", "c"]
        prompt = (call_dir / "prompt.md").read_text()
        assert "original prompt" in prompt
        assert all(str(path.relative_to(project)) in prompt for path in materials)
        assert command[:4] == ["/fake/t3", "thread", "start", str(project)]
        assert f"po_calls/{call_dir.name}/out/answer.md" in command[4]
        assert command[5:] == ["--provider", "p", "--model", "m", "--json"]
        (call_dir / "out/answer.md").write_text("  actual answer\n")
        return result({"threadId": "test-thread"})

    run = Mock(side_effect=[
        None, result({"thread": {"status": "running"}}), result(status),
    ])
    results = iter(run.side_effect)
    run.side_effect = lambda cmd, **kw: next(results) or start(cmd, **kw)
    monkeypatch.setattr(t3_utils.subprocess, "run", run)
    assert t3_utils.call_t3_with_contents("original prompt", sources, "p:m") == "actual answer"
    assert run.call_args.args[0] == [
        "/fake/t3", "thread", "show", "test-thread", "--messages", "0", "--json",
    ]
    t3_utils.time.sleep.assert_called_once_with(10.0)
    assert sorted(path.read_text() for path in sources) == ["a", "b", "c"]
    assert len(list((project / "po_calls").iterdir())) == 1


@pytest.mark.parametrize("status,match", [
    ("completed", "without creating"), ("failed", "failed"), ("cancelled", "cancelled"),
    ("interrupted", "interrupted"), ("waiting", "waiting"), ("rolled_back", "rolled_back"),
])
def test_thread_errors(monkeypatch, project, status, match):
    run = Mock(side_effect=[result({"thread": {"threadId": "id"}}), result({
        "status": status, "messages": [{"text": "Do not use this as the answer"}],
    })])
    monkeypatch.setattr(t3_utils.subprocess, "run", run)
    with pytest.raises(RuntimeError, match=match):
        t3_utils.call_t3_with_contents("prompt")


@pytest.mark.parametrize("failure,match", [
    ("exit", "exited with code 1"),
    ("invalid_json", "did not return valid JSON"),
    ("timeout", "timed out"),
])
def test_cli_failure_reports_stderr_tail(monkeypatch, project, failure, match):
    tail = "\u00e9" * 480 + "check the provider!!"
    stderr = "discard this prefix\n" + tail
    if failure == "timeout":
        run = Mock(side_effect=subprocess.TimeoutExpired([], 60, stderr=stderr.encode("utf-8")))
    else:
        run = Mock(return_value=subprocess.CompletedProcess(
            [], int(failure == "exit"), "not JSON", stderr,
        ))
    monkeypatch.setattr(t3_utils.subprocess, "run", run)

    with pytest.raises(RuntimeError, match=match) as exc:
        t3_utils.call_t3_with_contents("prompt")
    assert str(exc.value).endswith(f"stderr tail: {tail}")


def test_timeout(monkeypatch, project):
    monkeypatch.setattr(t3_utils.subprocess, "run", Mock(side_effect=[
        result({"threadId": "id"}), result({"status": "running"}),
    ]))
    monkeypatch.setattr(t3_utils.time, "monotonic", Mock(side_effect=[0.0, 0.0, 2.0, 2.0]))
    with pytest.raises(RuntimeError, match="timed out"):
        t3_utils.call_t3_with_contents("prompt", timeout_secs=1)


@pytest.mark.parametrize("path", [None, "/does-not-exist"])
def test_invalid_project(monkeypatch, path):
    monkeypatch.delenv("T3_PROJECT_DIR", raising=False)
    if path:
        monkeypatch.setenv("T3_PROJECT_DIR", path)
    with pytest.raises(ValueError, match="T3_PROJECT_DIR"):
        t3_utils.call_t3_with_contents("prompt")


def test_calls_are_serialized(monkeypatch, project):
    first_polled, release, second_entered = Event(), Event(), Event()
    order = []

    def run(command, **kwargs):
        if command[2] == "start":
            directory = project / Path(command[4].split()[0]).parent
            name = (directory / "prompt.md").read_text().splitlines()[0]
            (directory / "out/answer.md").write_text(name)
            order.append(("start", name))
            return result({"threadId": name})
        name = command[3]
        if name == "one":
            first_polled.set()
            assert release.wait(5)
        order.append(("show", name))
        return result({"status": "completed"})

    def second():
        second_entered.set()
        return t3_utils.call_t3_with_contents("two")

    monkeypatch.setattr(t3_utils.subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(t3_utils.call_t3_with_contents, "one")
        assert first_polled.wait(5)
        two = pool.submit(second)
        try:
            assert second_entered.wait(5)
        finally:
            release.set()
        assert (one.result(), two.result()) == ("one", "two")
    assert order == [("start", "one"), ("show", "one"), ("start", "two"), ("show", "two")]


def test_t3_text_routing(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "t3")
    backend = Mock(return_value="answer")
    monkeypatch.setattr(llm, "call_t3_with_contents", backend)
    llm.call_llm_with_text_prompt("text", "model", return_json=False)
    backend.assert_called_with(prompt_text="text", attachments=[], model_name="model")
    llm.call_llm_with_contents(["part"], "model", return_json=False)
    backend.assert_called_with(prompt_text="part", attachments=[], model_name="model")
    with pytest.raises(NotImplementedError, match="tools"):
        llm.call_llm_with_contents(["part"], "model", tools=[object()])


@pytest.mark.parametrize("override", ["", "antigravity:gemini-4-neon"])
def test_mixed_routing(monkeypatch, override):
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("LLM_BACKEND_MULTIMODAL", "t3")
    monkeypatch.setenv("LLM_MULTIMODAL_MODEL", override)
    t3 = Mock(return_value="answer")
    openai = Mock()
    monkeypatch.setattr(llm, "call_t3_with_contents", t3)
    monkeypatch.setattr(llm, "call_openai_models_with_text_prompt", openai)
    llm.call_llm_with_text_prompt("plain", "writer", return_json=False)
    openai.assert_called_once()
    llm.call_llm_with_images("image", ["image.png"], "writer", return_json=False)
    t3.assert_called_with(prompt_text="image", attachments=["image.png"], model_name=override or "writer")
    llm.call_llm_with_pdf("paper.pdf", "pdf", "writer", return_json=False)
    t3.assert_called_with(prompt_text="pdf", attachments=["paper.pdf"], model_name=override or "writer")
