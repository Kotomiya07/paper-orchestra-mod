"""Generate an answer through a local T3 Code thread and a file handoff."""

from collections.abc import Sequence
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from uuid import uuid4


DEFAULT_T3_PROVIDER = "antigravity"
DEFAULT_T3_TIMEOUT_SECS = 900
DEFAULT_T3_POLL_INTERVAL_SECS = 10.0
_t3_call_lock = threading.Lock()


def resolve_t3_bin() -> str:
    override = os.environ.get("T3_BIN", "").strip()
    if override:
        path = Path(override).expanduser().absolute()
        if not path.is_file():
            raise RuntimeError("T3_BIN does not point to a file.")
        return str(path)
    found = shutil.which("t3")
    if found:
        return found
    fallback = Path.home() / ".local/bin/t3"
    if fallback.is_file():
        return str(fallback)
    raise RuntimeError("T3 CLI not found. Set T3_BIN or install t3 in PATH / ~/.local/bin/t3.")


def parse_t3_model_spec(model_name: str) -> tuple[str, str]:
    provider, separator, model = model_name.partition(":")
    if not separator:
        model = provider
        provider = os.environ.get("T3_DEFAULT_PROVIDER", "").strip() or DEFAULT_T3_PROVIDER
    if not provider.strip() or not model.strip():
        raise ValueError("Expected a T3 model name or provider:model.")
    return provider.strip(), model.strip()


def _thread_data(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    thread = payload.get("thread")
    return thread if isinstance(thread, dict) else payload


def _extract_thread_status(payload: object) -> str | None:
    status = _thread_data(payload).get("status")
    return status if isinstance(status, str) else None


def _extract_thread_id(payload: object) -> str | None:
    thread_id = payload.get("threadId") if isinstance(payload, dict) else None
    thread_id = thread_id or _thread_data(payload).get("threadId")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _run_t3(command: list[str], timeout: float) -> object:
    try:
        proc = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"T3 thread {command[2]} timed out.") from None
    except OSError as exc:
        raise RuntimeError("Could not start the T3 CLI.") from exc
    if proc.returncode:
        raise RuntimeError(f"T3 thread {command[2]} exited with code {proc.returncode}.")
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(f"T3 thread {command[2]} did not return valid JSON.") from None


def call_t3_with_contents(
    prompt_text: str,
    attachments: Sequence[str | Path] | None = None,
    model_name: str = "gemini-4-neon",
    timeout_secs: int | None = None,
    poll_interval_secs: float = DEFAULT_T3_POLL_INTERVAL_SECS,
) -> str:
    configured = os.environ.get("T3_PROJECT_DIR", "").strip()
    if not configured or not Path(configured).expanduser().is_dir():
        raise ValueError("T3_PROJECT_DIR must point to a registered T3 project directory.")
    project = Path(configured).expanduser().resolve()
    timeout = timeout_secs if timeout_secs is not None else int(
        os.environ.get("T3_CALL_TIMEOUT") or DEFAULT_T3_TIMEOUT_SECS
    )
    provider, model = parse_t3_model_spec(model_name)
    command = [resolve_t3_bin(), "thread"]
    with _t3_call_lock:
        relative = Path("po_calls") / f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        directory = project / relative
        materials = directory / "materials"
        materials.mkdir(parents=True)
        (directory / "out").mkdir()
        copied = []
        for attachment in attachments or ():
            source = Path(attachment)
            if not source.is_file():
                raise RuntimeError(f"T3 attachment is not a file: {source}")
            destination = materials / source.name
            index = 1
            while destination.exists():
                destination = materials / f"{index:02d}_{source.name}"
                index += 1
            shutil.copy2(source, destination)
            copied.append(f"- {destination.relative_to(project)}")
        (directory / "prompt.md").write_text(
            prompt_text + ("\n\nAttachments:\n" + "\n".join(copied) if copied else ""),
            encoding="utf-8",
        )
        instruction = (
            f"{relative}/prompt.md を読み、materials/ のファイルを参照して、"
            f"答えの全文を {relative}/out/answer.md に書くこと。"
            "他のファイルは変更しないこと。git 操作はしないこと"
        )
        thread_id = _extract_thread_id(_run_t3(
            [*command, "start", str(project), instruction, "--provider", provider,
             "--model", model, "--json"], timeout=60,
        ))
        if not thread_id:
            raise RuntimeError("T3 thread start did not return a threadId.")
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            payload = _run_t3([*command, "show", thread_id, "--messages", "0", "--json"],
                              timeout=min(60, remaining))
            status = _extract_thread_status(payload)
            thread = _thread_data(payload)
            runs = thread.get("runCount")
            if status == "completed" or (
                status == "idle" and thread.get("activeRunId") is None
                and isinstance(runs, int) and runs >= 1
            ):
                break
            if status in {"failed", "cancelled", "interrupted", "rolled_back", "waiting"}:
                raise RuntimeError(f"T3 thread {thread_id} ended with status={status!r}.")
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(poll_interval_secs, remaining))
        else:
            raise RuntimeError(f"T3 thread {thread_id} timed out after {timeout}s.")
        answer_path = directory / "out/answer.md"
        try:
            answer = answer_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            raise RuntimeError(f"T3 thread {thread_id} finished without creating {answer_path}.") from None
        if not answer:
            raise RuntimeError(f"T3 thread {thread_id} produced an empty answer.md.")
        return answer
