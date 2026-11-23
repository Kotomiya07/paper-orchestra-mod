# PaperOrchestra: CPA, Antigravity CLI, T3 Code, and OpenAlex

This branch adds three ways to run the writing pipeline: CLIProxyAPI (CPA),
the Antigravity command-line client (`agy`), and T3 Code (`t3`). The native
Gemini backend remains available. The branch also adds bibliographic search
and abstract recovery through OpenAlex and Crossref.

## Choose a backend

Export settings in your shell. See [.env.example](.env.example) for variable
names and examples. Do not create a `.env` file or put credentials in the
repository.

| `LLM_BACKEND` | What runs the prompt | Attachments |
| --- | --- | --- |
| `openai` | An OpenAI-compatible endpoint such as CPA | Images if the model supports them; PDFs are extracted to text |
| `agy` | Antigravity CLI print mode | Text only |
| `t3` | A local T3 Code thread | Images and PDFs are passed as file copies |
| `gemini` | Native Gemini API | Images and PDFs are passed to Gemini |

If `LLM_BACKEND` is unset, PaperOrchestra retains the original model-name
routing: names containing `gpt`, `o1`, or `o3` go to OpenAI; other names go
to Gemini. `LLM_BACKEND_MULTIMODAL` can override the backend only when a call
has images or a PDF. Set `LLM_MULTIMODAL_MODEL` alongside it if that backend
needs a different model name.

### CPA

On a machine connected to the tailnet, including gpu-01, use:

```sh
export LLM_BACKEND=openai
export PAPER_ORCHESTRA_CPA=1
export PAPER_ORCHESTRA_CPA_TUNNEL=0
```

CPA defaults to `http://m4-mac-mini-32gb.tail5df0ca.ts.net:8317/v1`.
Set `OPENAI_BASE_URL` to use a different OpenAI-compatible endpoint. CPA
uses `OPENAI_API_KEY` from the environment if available; otherwise it reads
`env.ANTHROPIC_AUTH_TOKEN` from the existing `~/.claude/settings.json`.
The pipeline does not print the key.

On the Muse virtual machine, set `PAPER_ORCHESTRA_CPA_TUNNEL=1` and make
sure `HTTPS_PROXY` is set to the proxy for that environment. The tunnel
client replaces the proxy port with `3130`. It uses `httpx2` where available
and falls back to `httpx` otherwise.

Pass the endpoint's model identifiers unchanged through
`--writer_model_name` and `--reflection_model_name`. Examples include
`preview/DeepSeek-V4.1-Flash-1017`, `gpt-5.6-luna`, and `claude-sonnet-5-5`.

### Antigravity CLI

Set `LLM_BACKEND=agy` and pass a model identifier such as
`gemini-3.8-flash-medium` through the model flags. The backend uses the
CLI's existing sign-in and needs no API key from PaperOrchestra. It looks
for the executable in `AGY_BIN`, then `PATH`, then `~/.local/bin/agy`.

Each invocation adds approximately 8,500 input tokens before the prompt
and runs in a separate temporary directory. PaperOrchestra rejects prompts
over 500 KiB of UTF-8 text; the host can impose a smaller argument-size
limit. Images, PDFs, and tools such as Google Search grounding are not
supported and raise `NotImplementedError`. To handle images and PDFs, use
a separate backend through `LLM_BACKEND_MULTIMODAL`.

### T3 Code

Run this backend only on the machine hosting the T3 server, such as
gpu-01. Set `T3_PROJECT_DIR` to a directory already registered as a T3
project. The executable is resolved from `T3_BIN`, then `PATH`, then
`~/.local/bin/t3`. Set `LLM_BACKEND=t3` for text requests, or keep CPA for
text and send only images and PDFs to T3:

```sh
export LLM_BACKEND=openai
export PAPER_ORCHESTRA_CPA=1
export LLM_BACKEND_MULTIMODAL=t3
export LLM_MULTIMODAL_MODEL=antigravity:gemini-4-neon
```

T3 accepts `provider:model`, for example `antigravity:gemini-4-neon`
or `acpRegistry_devin:gpt-6-2-sol-medium`. With no provider prefix, it
uses `T3_DEFAULT_PROVIDER` (default `antigravity`).
Separate calls can take minutes and consume the provider's shared quota,
including Google's quota when using Antigravity. Prefer CPA or `agy` for
ordinary text. T3 calls are serialized within one Python process.
`T3_CALL_TIMEOUT` defaults to 900 seconds for waiting on the thread.

Each call retains `po_calls/<call_id>/` under the registered project for
inspection. The directory contains `prompt.md`, `materials/` with copies
of attachments, and `out/`. The thread is instructed to write only
`out/answer.md` and not to perform Git operations. The backend accepts an
answer only from that file after thread completion. It does not substitute
thread messages for an absent or empty answer. Treat the retained prompts,
copies, and answers as project material, and keep them out of version control.

## Literature and plotting

PaperOrchestra tries Semantic Scholar (S2), OpenAlex, then Crossref to
match titles. If the first match has no abstract, the next source can
supply just the abstract without replacing the first source's metadata.
An optional `OPENALEX_EMAIL` is sent as the OpenAlex `mailto` parameter.
`SEMANTIC_SCHOLAR_API_KEY` is optional and is not printed on import.

Native Gemini retains Google Search grounding for discovery. Other
backends discover candidates through S2 and OpenAlex without a venue or
citation-count filter. Requests targeting a specific paper try title
matching first. The writing prompt requires citations only for relevant
papers actually used, not a percentage of the collected bibliography.

Use `--use_plotting false` (the default) for CPA, `agy`, and T3 Code.
Diagram generation still requires native Gemini, so enabling plotting
under any of those three backends fails before paper generation starts.

## Setup and verification

On Linux x86-64, use Pixi to install the locked Python 3.11 environment
with the packages from `requirements.txt` and `pytest`. From the
repository root:

```sh
pixi install
pixi run test
pixi run check
```

`test` runs the unit tests with mocked service and CLI calls. `check`
checks Python syntax with `compileall`, skipping the installed
environment under `.pixi/`. Use `pixi run python` to run other Python
commands in the same environment.

If you cannot use Pixi, install `requirements.txt` and `pytest` in a
Python 3.11 virtual environment and run `python -m pytest tests/ -q`
and `python -m compileall .` from the repository root. To build LaTeX
documents, install `pdflatex` and `bibtex` separately or use the optional
Nix development shell.

### Live smoke checks

Muse runs these separately from the unit tests. If you activated a
virtual environment instead of using Pixi, replace `pixi run python`
with `python`.

1. On the Muse VM, configure the CPA tunnel as described earlier. Run only
   the outline stage with the synthetic material in `examples/smoke/`:

   ```sh
   pixi run python - <<'PY'
   from methods.agents.outline_agent import OutlineAgent

   outline = OutlineAgent("preview/DeepSeek-V4.1-Flash-1017").run(
       "examples/smoke/idea_sparse.md",
       "examples/smoke/experimental_log.md",
       "templates/iclr2025/template.tex",
       "templates/iclr2025/guidelines.md",
   )
   assert isinstance(outline, dict) and outline.get("section_plan")
   PY
   ```

2. On the Muse VM, sign in to `agy`, set `LLM_BACKEND=agy`, then run
   `pixi run python -c 'from utils.llm_backend_utils import call_llm_with_text_prompt as call; print(call("Reply with OK.", "gemini-3.8-flash-medium", return_json=False)["raw_response"])'`.
   Check that the response contains `OK`.
3. On the T3 server host, configure the mixed backend as described above.
   Set `SMOKE_IMAGE` to the path of a small, non-sensitive PNG and run
   `pixi run python -c 'import os; from utils.llm_backend_utils import call_llm_with_images as call; print(call("Describe this image in one sentence.", [os.environ["SMOKE_IMAGE"]], "preview/DeepSeek-V4.1-Flash-1017", return_json=False)["raw_response"])'`.
   Confirm that the returned text matches the new `out/answer.md` after
   trimming whitespace and that `materials/` contains a copy of the PNG.
