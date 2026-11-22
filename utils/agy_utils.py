"""Text generation through the Antigravity CLI print mode."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


MAX_AGY_PROMPT_BYTES = 500 * 1024
DEFAULT_AGY_TIMEOUT_SECS = 300
MAX_STDERR_TAIL_CHARS = 500


def resolve_agy_bin() -> str:
    override = os.environ.get("AGY_BIN", "").strip()
    if override:
        path = Path(override).expanduser().absolute()
        if not path.is_file():
            raise RuntimeError("AGY_BIN does not point to a file.")
        return str(path)
    found = shutil.which("agy")
    if found:
        return str(Path(found).absolute())
    fallback = Path.home() / ".local/bin/agy"
    if fallback.is_file():
        return str(fallback)
    raise RuntimeError("agy CLI not found. Set AGY_BIN or install agy in PATH / ~/.local/bin/agy.")


def _format_stderr_tail(stderr: str | None) -> str:
    return (stderr or "")[-MAX_STDERR_TAIL_CHARS:]


def call_agy_with_text_prompt(
    prompt: str, model_name: str, timeout_secs: int = DEFAULT_AGY_TIMEOUT_SECS
) -> str:
    if not isinstance(prompt, str):
        raise TypeError("prompt must be str")
    size = len(prompt.encode("utf-8"))
    if size > MAX_AGY_PROMPT_BYTES:
        raise ValueError(f"Prompt size ({size} bytes) exceeds agy CLI limit ({MAX_AGY_PROMPT_BYTES} bytes).")
    command = [resolve_agy_bin(), "--model", model_name, "--output-format", "json",
               "--disable-slash-commands", "-p", prompt]
    with tempfile.TemporaryDirectory(prefix="po_agy_") as cwd:
        try:
            proc = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, cwd=cwd, timeout=timeout_secs)
        except subprocess.TimeoutExpired as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
            raise RuntimeError(f"agy timed out after {timeout_secs}s. stderr tail: {_format_stderr_tail(stderr)}") from None
        except OSError as exc:
            raise RuntimeError("Could not start agy.") from exc
    tail = _format_stderr_tail(proc.stderr)
    if proc.returncode:
        raise RuntimeError(f"agy exited with code {proc.returncode}. stderr tail: {tail}")
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(f"agy returned non-JSON output. stderr tail: {tail}") from None
    status = payload.get("status") if isinstance(payload, dict) else None
    response = payload.get("response") if isinstance(payload, dict) else None
    if status != "SUCCESS" or not isinstance(response, str):
        raise RuntimeError(f"agy failed with status={status!r}. stderr tail: {tail}")
    return response.strip()
