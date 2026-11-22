# PaperOrchestra 改造 実施計画（CPA・OpenAlex 対応）

- 対象リポジトリ: `google-research/paper-orchestra`（ベースコミット `d9bae99`）
- 作業場所: gpu-01 `~/agentic-project/paper-orchestra-mod` / ブランチ `mod/cpa-openalex`
- 参照仕様: `SPEC_MOD.md`
- 役割分担: 指揮（Neon）が本計画を策定 → 実装（Sol）が T1〜T10 を順に実施 → 検証（Muse）が実 CPA 疎通・成果物確認を実施
- 実装方針: t-wada 流の TDD（Red → Green → Refactor）で、まずモック単体テストを書いてから実装し、変更単位ごとにコミットを分けること。**API キーやトークンの実値はコード・テスト・設定例・ログ・コミットに一切含めないこと。**

---

## 1. 実コード前提検証の結果

### 1.1 LLM 呼び出し箇所の全数一覧（仕様 A3 との突合）

リポジトリ内の全 `.py` ファイルを精査した結果を以下に示す。

| ファイル | 関数・メソッド（行番号） | 現行の呼び出し | 仕様 A3 との突合 | v1 での対応方針 |
|---|---|---|---|---|
| `methods/agents/outline_agent.py` | `OutlineAgent.run()` (L51) | `call_gemini_with_contents`（テキスト Part×5） | 一致 | `call_llm_with_contents` へ置換（T4） |
| `methods/agents/literature_review_agent.py` | `HybridLiteratureAgent._synthesize_content()` (L499) | `call_gemini_with_contents`（テキスト×2 + `system_instruction`） | 一致 | `call_llm_with_contents` へ置換（T4） |
| `methods/agents/literature_review_agent.py` | `HybridLiteratureAgent._discover_candidates()` (L464) | `call_gemini_with_contents`（`gemini-3-flash-preview` 固定 + `google_search_tool`） | 仕様 B2 と一致 | `LLM_BACKEND=openai` 時は API 探索へ切替、Gemini 時は従来経路を維持（T7） |
| `methods/agents/content_refinement_agent.py` | `ContentRefinementAgent._get_formatting_review()` (L165) | `call_gemini_with_images` | 一致 | 既存の `call_llm_with_images` へ置換（T4） |
| `methods/agents/content_refinement_agent.py` | `ContentRefinementAgent.run()` 本文推敲ループ (L293) | `call_gemini_with_contents`（**PDF バイナリ `application/pdf` 添付** + テキスト） | **食い違い（後述 2.1）** | 既存の `call_llm_with_pdf` へ置換（T4） |
| `methods/agents/content_refinement_agent.py` | `ContentRefinementAgent.run()` フォーマット修正ループ (L520) | `call_gemini_with_contents`（テキスト×1） | 一致 | `call_llm_with_contents` へ置換（T4） |
| `methods/agents/section_writing_agent.py` | `SectionWritingAgent.run()` (L201) | `call_llm_with_images` | 一致（確認済み） | A2 のルーティング対応で動作（コード変更不要。空画像・PNG MIME は `llm_backend_utils` / `openai_utils` 側で改善） |
| `utils/content_parsing_utils.py` | `extract_paper_title_from_citation()` (L38) | `call_gemini_with_text_prompt`（`gemini-3-flash-preview` 固定・`model_name` 引数なし） | 一致（＋呼び出し元補完が必要） | `model_name` 引数を追加し `call_llm_with_text_prompt` へ置換。呼び出し元 `autoraters/citation_f1.py:105` から `self.model_name` を渡す（T4） |
| `autoraters/agent_review.py` | `perform_review_agentreview()` (L137, L172), `get_meta_review()` (L200) | `call_gemini_with_text_prompt` **計 3 か所** | **要対応を確認**（本パイプライン Step 4 の `_get_peer_review()` L90 から直接呼ばれるため必須） | 全 3 か所を `call_llm_with_text_prompt` へ置換（T4） |
| `autoraters/sxs_lit_review_quality.py` | `rate_sxs_lit_review()` (L39–67) | `call_llm_with_text_prompt` だが `if "gemini" / elif "gpt"` 分岐あり | **追加発見（後述 2.6）** | CPA モデル名で無限ループするため分岐を撤去し `call_llm_with_text_prompt` を直接呼ぶ（T4） |
| `autoraters/sxs_paper_quality.py` | `rate_sxs_paper_quality()` (L58–130) | 独自の `"gpt"/"o1"/"o3"` 判定＋`call_openai_models_with_content` / `call_gemini_with_contents` | **追加発見（後述 2.6）** | 判定を `is_openai_backend(model_name)` に統一（T4） |
| `autoraters/citation_f1.py` | `_get_citation_priorities()` (L185) | `call_llm_with_text_prompt` | 既に統一済み | A2 で自動対応（ただし L41 の S2 キー出力削除と L105 の `model_name` 引き渡しを実施：T4） |
| `autoraters/lit_review_quality.py` | `rate_single_paper()` (L77) | `call_llm_with_pdf` | 既に統一済み | A2 で自動対応 |
| `utils/pdf_utils.py` | `extract_reference_from_pdf_text()` (L138) | `call_llm_with_text_prompt` | 既に統一済み | A2 で自動対応 |
| `utils/paper_banana_utils.py` | `retrieve_few_shot_examples` (L378), `generate_figure_caption` (L442, L450), `predict_figure_content` (L526), `style_figure_content` (L565), `critique_and_revise_figure` (L618), `generate_figure_visuals` (L662, L674) | `call_gemini_with_contents` / `call_gemini_with_text_prompt` / `generate_image_with_gemini` | 仕様 A4（作図系） | v1 は画像生成を Gemini 専用で残し、`LLM_BACKEND=openai` で作図有効時は明示エラーで即停止（T5） |
| `frontend/app.py` | L145, L365（UI 選択肢・CLI 起動引数） | モデルリスト固定 | 仕様「範囲外」 | 変更しない |

### 1.2 文献照合の呼び出し箇所と引用 90% 強制プロンプトの正確な場所

- **文献照合（Enrichment）の呼び出し箇所**
  1. **本パイプライン（変更対象）**: `methods/agents/literature_review_agent.py`
     - import: L29 `from utils.scholar_utils import s2_title_search`
     - ループ呼び出し元: `_execute_tasks_in_parallel()` L204–209（`time.sleep(1.0)` 挟みで逐次呼び出し）
     - 実照合処理: `_enrich_and_register_paper()` **L326–384**（特に L338 の `s2_title_search(cand_title, cand_year, cutoff_date)`、L344–345 の `if not s2_data.get("abstract", ""): return`）
  2. **評価スクリプト（OpenAlex 化の対象外）**: `autoraters/citation_f1.py` L111–140（`ReferenceF1V1Evaluator._fetch_paper_id`）
     - 正解データキャッシュの S2 `paperId` と集合一致を取る評価用途のため OpenAlex ID には差し替えない。
     - ただし **L41 `print(f"Loaded S2 API Key for this project: {S2_API_KEY}")`** に秘密漏洩があるため T4 で削除する。
- **引用 90% 強制の正確な場所（リポジトリ全体で 2 か所のみ）**
  1. `methods/prompts/literature_review_agent.py` **L33**（`literature_review_agent_writter_prompt` 内）:
     ```text
     - You MUST cite at least {min_cite_paper_count} of them across the introduction and related work sections.
     ```
  2. `methods/agents/literature_review_agent.py` **L492–496**（`HybridLiteratureAgent._synthesize_content` 内、特に **L494**）:
     ```python
     writer_system_prompt = literature_review_agent_writter_prompt.format(
         paper_count=len(papers),
         min_cite_paper_count=int(len(papers) * 0.9),
         cutoff_date=cutoff_date,
     )
     ```
  - 補足: 変数名 `literature_review_agent_writter_prompt`（`writter` の綴り）は既存 import 互換のため変更しない。他のプロンプトファイル（`outline_agent.py`, `section_writing_agent.py`, `content_refinement_agent.py`, `format_agent.py`）に 90% 引用強制の指示が無いことも確認済み。

### 1.3 OpenAlex 追加時に触る関数の境界

| 層 | ファイル | 触る関数・境界 | 責務 |
|---|---|---|---|
| API / 照合 / 探索ユーティリティ | `utils/scholar_utils.py` | モジュール初期化 (L27–28 修正), `is_date_valid()`（抽出）, `reconstruct_openalex_abstract()`（新設）, `strip_jats_tags()`（新設）, `_select_best_title_match()`（新設）, `s2_title_search()`（共通化）, `openalex_title_search()`（新設）, `crossref_title_search()`（新設）, `enrich_paper_metadata()`（新設：S2→OpenAlex→Crossref 優先＆要旨補完）, `s2_search()`（新設）, `openalex_search()`（新設） | 外部学術 API 呼び出し・正規化・日付フィルタ・題名ファジー一致・要旨補完を閉じ込め、ネットワークなし単体テストを可能にする |
| 文献エージェント | `methods/agents/literature_review_agent.py` | `__init__()` (L87), `_execute_tasks_in_parallel()` (L160–210), `_enrich_and_register_paper()` (L326–384), `_discover_candidates()` (L426–475), `_extract_targeted_query()`（新設補助）, `_synthesize_content()` (L477–505) | `LLM_BACKEND=openai` 時の API 探索切替、多情報源照合・情報源ログ出力、`min_cite_paper_count` 削除 |
| 文献プロンプト | `methods/prompts/literature_review_agent.py` | `literature_review_agent_writter_prompt` (L33) | 90% 引用強制の撤廃 |

---

## 2. 仕様と実コードの食い違いと扱い

| # | 食い違いの内容 | 扱い（優先判断） | 理由 |
|---|---|---|---|
| 2.1 | 仕様 A3 は `content_refinement_agent.py` の本文推敲 2 か所を A2 の汎用関数（テキスト断片）経由とする想定だが、実コード L293 の呼び出しは `types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")` で **PDF バイナリを添付**している（L520 のフォーマット修正のみテキスト専用）。 | **実コードに合わせ、L293 は既存の `call_llm_with_pdf()` を使用し、L520 のみ `call_llm_with_contents()` を使用する。** | `call_llm_with_pdf()` は既に Gemini 側で raw PDF bytes、OpenAI 側で `load_paper()` テキスト抽出を実装済み。A2 の `call_llm_with_contents()` をテキスト専用契約に保ったまま Gemini 後方互換と CPA 動作を両立できる。 |
| 2.2 | 実コード `utils/openai_utils.py:204` (`get_openai_image_part_from_path`) がデータ URI の MIME 型を `data:image/jpeg;base64,...` 固定にしているが、`pdf_to_grid_images()` (`utils/pdf_utils.py:287`) は `.png` を出力する。 | **実コードを修正し、拡張子から MIME 型を判定する**（`.png` → `image/png`、`.jpg`/`.jpeg` → `image/jpeg`、フォールバック `image/png`）。 | CPA 配下の Claude 系モデル等は宣言 MIME 型と実画像バイト列の不一致を HTTP 400 で拒否するため。 |
| 2.3 | 実コード `utils/openai_utils.py:22` に未使用の `import cv2` がある。 | **実コードから削除する。** | ファイル内で未使用であり、OpenCV 未導入環境で `openai_utils` の import が落ちる原因になるため。 |
| 2.4 | 実コード `utils/openai_utils.py:185` (`call_openai_models_with_text_prompt`) は `content=[{"type": "text", "text": prompt}]` の配列形式を送信している。 | **テキスト専用呼び出し（`call_llm_with_contents`、および画像 0 枚時の `call_llm_with_images`）では `content` に単一文字列 `str`（`"\n\n".join(contents)`）を渡す**（`call_openai_models_with_content` は `Union[str, list[Any]]` を受理）。 | OpenAI 互換 API では `content: str` が全モデル（DeepSeek・Claude・GPT 経由の CPA を含む）で最も互換性が高く、複数テキスト配列や連続 `user` メッセージによる 400 エラーを回避できるため。 |
| 2.5 | gpu-01 環境の `httpx` は `0.28.1` であり、旧引数 `httpx.Client(proxies=...)` は廃止されている。 | **仕様 A1 を現行 `httpx` に合わせ、`httpx.Client(proxy=proxy_url)`（単数形 `proxy=`）を使用する。** | `httpx 0.28` では `proxies=` で `TypeError` になる。なお `proxy=` 明示指定時は環境変数 `HTTP_PROXY`/`HTTPS_PROXY` より明示プロキシが優先されることを実機確認済み。 |
| 2.6 | 仕様 A3 に未記載だが `autoraters/` に実害のある箇所が 3 点ある：<br>(a) `autoraters/citation_f1.py:41` が import 時に `S2_API_KEY` を print している（`scholar_utils.py:28` と同一バグ）、L105 で `extract_paper_title_from_citation` に `model_name` を渡していない。<br>(b) `autoraters/sxs_lit_review_quality.py:39–67` が `if "gemini" / elif "gpt"` 分岐のため CPA モデル名で無限ループする。<br>(c) `autoraters/sxs_paper_quality.py:58` が独自に `"gpt"/"o1"/"o3"` 判定している。 | **仕様の全体原則（A1 秘密出力禁止、A2 ルーティング統一、A5 任意モデル名）を優先し、T4 で併せて修正する。** | いずれも数行の最小修正で済み、秘密漏洩や評価実行時の無限ループを確実に防げるため。 |
| 2.7 | `methods/agents/plotting_agent.py:243–248` は figure ごとの例外を握りつぶし、`methods/paper_writer_with_plotting.py:68–265` は例外を 3 回リトライする。 | **仕様 A4（「分かりやすいエラーを出して止めること。黙って失敗しないこと」）を優先し、CLI 入口・`paper_writer_with_plotting.write_single_paper` リトライ前・`PlottingAgent.run` 先頭・`generate_figure_visuals` の各境界で早期に明示エラーを送出する。** | `generate_image_with_gemini` 内部だけでエラーを出しても握りつぶしや 3 回リトライに呑まれ、Outline/文献検索を無駄に回してから黙って失敗するため。 |
| 2.8 | `OutlineAgent` が生成する `citation_hints`（`methods/prompts/outline_agent.py:76`）は `"Vaswani et al. (Attention Is All You Need)"` や `"research paper or technical report introducing 'AdamW optimizer'"` 形式で、生のまま B1 の題名検索（`fuzz.ratio > 70`）に渡すと一致率が落ちる。 | **仕様 B2（targeted は B1 題名検索で 1 件狙う）をベースにしつつ、`_extract_targeted_query()` で括弧内タイトルや引用符内名称を抽出して B1 検索チェーン（S2→OpenAlex→Crossref）を試し、全滅時のみ `openalex_search` / `s2_search` の `limit=1` にフォールバックする。** | 仕様の設計意図（targeted で的確に 1 件取得）を実プロンプト出力形式と整合させるため。 |
| 2.9 | `utils/scholar_utils.py:43` の `is_date_valid` は `"YYYY-MM"` 固定（`c_year, c_month = map(int, ...)`）で `"YYYY-MM-DD"` や `"YYYY"` を渡すと例外→`True`（除外漏れ）になる一方、`literature_review_agent.py:385` の `_is_paper_allowed` は 1〜3 要素すべてに対応している。 | **`is_date_valid` をモジュールレベルに抽出する際、`YYYY-MM`（従来挙動維持）・`YYYY-MM-DD`・`YYYY` を安全に解析できるよう整備する。** | S2・OpenAlex・Crossref の 3 ソース間で cutoff 判定の一貫性を保つため。 |
| 2.10 | gpu-01 の既定 `python3`（conda base 3.13.2）には `openai` / `google-genai` / `thefuzz` / `pytest` / `cv2` / `pymupdf` 等が未導入。また `.gitignore` に `.pytest_cache/`・`__pycache__/`・`.venv/` が含まれていない。 | **gpu-01 に導入済みの `uv`（`0.9.5`・Python `3.11.12`）でテスト用仮想環境を作成し、`.gitignore` にも上記パターンを追加する。** | 仕様 C の `python311` 前提と仕様 D の `compileall` / `pytest` 通過・作業ツリー clean を同時に満たすため。 |

---

## 3. 実装タスク一覧（Sol 向け実行手順）

### 依存関係グラフ

```text
T1 (.gitignore & 検証環境)
 ├── T2 (A1: openai_utils CPA/トンネル/秘密保護)
 │    └── T3 (A2/A5: llm_backend_utils ルーティング & call_llm_with_contents)
 │         ├── T4 (A3: 直接 Gemini 呼び出し置換 & autoraters 整合) ──┐
 │         ├── T5 (A4: --use_plotting 明示エラーガード)             │
 │         └────────────────────────────────────────────────────────┤
 └── T6 (B1/B2: scholar_utils OpenAlex/Crossref 照合・探索) ────────┴── T7 (B1/B2/B3: literature_review_agent & prompt)
T8 (D: examples/smoke 最小素材)  ※独立実行可
T9 (C: .env.example / README_MOD.md / flake.nix)  ※T2〜T8 後
T10 (D: 全体検証・秘密検査・clean 確認)  ※T1〜T9 後
```

> **共通注意（重要）**: 作業ツリーには未追跡ファイル `SPEC_MOD.md` と `BRIEF_NEON.md` が存在する。**絶対に `git add -A` / `git add .` を使わず、毎回対象ファイル名を明示して `git add <file>...`すること。** `SPEC_MOD.md`・`BRIEF_NEON.md`・`.env` はコミットしないこと。

---

### T1: `.gitignore` 整備と Python 3.11 テスト環境構築
- **依存**: なし
- **対象ファイル**: `.gitignore`
- **変更内容**:
  1. `.gitignore` に以下を追記する（末尾改行も整える）:
     ```gitignore
     __pycache__/
     .pytest_cache/
     .venv/
     ```
  2. リポジトリ外に Python 3.11 仮想環境を作成する（リポジトリ直下に `.venv` を置くと `python -m compileall .` が外部パッケージまで走査してノイズや失敗の原因になるため、リポジトリ外 `/tmp/po-venv` を推奨）:
     ```bash
     uv venv --python 3.11 /tmp/po-venv
     uv pip install --python /tmp/po-venv/bin/python -r requirements.txt pytest
     ```
     （以降、検証コマンドの `PYTHON=/tmp/po-venv/bin/python` として実行する）
- **完了判定**:
  - `/tmp/po-venv/bin/python --version` が `Python 3.11.x` を出力すること。
  - `/tmp/po-venv/bin/python -c "import openai, google.genai, httpx, requests, thefuzz, pytest"` が終了コード 0 で成功すること。
  - `git diff .gitignore` が意図どおりであること。
- **推奨コミット**: `chore: ignore __pycache__, .pytest_cache, and .venv`

---

### T2: OpenAI クライアントの CPA / トンネル対応と秘密保護（SPEC A1）
- **依存**: T1
- **対象ファイル**: `utils/openai_utils.py`, `tests/test_llm_backend.py`（新規作成）
- **変更内容（TDD: テスト先行）**:
  1. `tests/test_llm_backend.py` を新規作成し、以下の単体テスト（ネットワークなし・`OpenAI` / `httpx.Client` をモック）を先に書く:
     - `OPENAI_BASE_URL` 未設定かつ非 CPA モード時：`OpenAI(api_key=...)` が `base_url` 指定なし・`http_client` 指定なしで呼ばれること。
     - `OPENAI_BASE_URL` 設定時：`OpenAI(api_key=..., base_url=...)` に値が渡ること。
     - CPA モード（`PAPER_ORCHESTRA_CPA=1`）：
       - `OPENAI_BASE_URL` 未設定なら既定値 `http://m4-mac-mini-32gb.tail5df0ca.ts.net:8317/v1` が使われること。
       - `OPENAI_BASE_URL` 設定時はそちらが優先されること。
       - `OPENAI_API_KEY` 未設定時は `tmp_path` に作成した偽 `settings.json` の `env.ANTHROPIC_AUTH_TOKEN` を読むこと（`HOME` または `settings_path` を差し替えて検証）。
       - `OPENAI_API_KEY` が設定されている場合は `settings.json` より `OPENAI_API_KEY` が優先されること。
     - トンネルモード（`PAPER_ORCHESTRA_CPA_TUNNEL=1`）：
       - `HTTPS_PROXY=http://proxy.internal:8080` → `http://proxy.internal:3130` に置換された URL で `httpx.Client(proxy=...)` が作られ、`OpenAI(http_client=...)` に渡ること。
       - 認証情報付き `http://user:pass@proxy.internal:8080/` → `http://user:pass@proxy.internal:3130/` になること（ポート省略 URL `http://proxy.internal` → `http://proxy.internal:3130` も確認）。
       - `HTTPS_PROXY` 未設定で `PAPER_ORCHESTRA_CPA_TUNNEL=1` の場合は明確な `ValueError` になること。
       - `PAPER_ORCHESTRA_CPA_TUNNEL` 未設定または `0` のときは `http_client` を作らない／渡さないこと。
     - 秘密非出力テスト：ダミーキー（例 `sk-secret-test-value-9999`）を設定し、正常生成・API 例外発生（例外メッセージにキーを含む偽エラーを発生させる）・キー未設定エラーのいずれでも、`capsys` の stdout/stderr および送出例外文字列にダミーキーが一切含まれないこと。
     - `get_openai_image_part_from_path`: `.png` ファイルに対して `data:image/png;base64,`、`.jpg` ファイルに対して `data:image/jpeg;base64,` となること。
  2. `utils/openai_utils.py` を実装:
     - L22 の未使用 `import cv2` を削除。`import mimetypes`, `from urllib.parse import urlsplit, urlunsplit`, `from typing import Any, Optional, Union`, `import httpx` を追加。
     - 定数定義:
       - `DEFAULT_CPA_BASE_URL = "http://m4-mac-mini-32gb.tail5df0ca.ts.net:8317/v1"`
       - `CPA_TUNNEL_PROXY_PORT = 3130`
       - `CLAUDE_SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")`（実行時は `os.path.expanduser("~/.claude/settings.json")` を都度評価してテストで `HOME` を差し替えられるようにすること！）
     - 補助関数を追加:
       - `_is_truthy_env(name: str) -> bool`: `os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}`
       - `reset_openai_client() -> None`: `_openai_client = None`（ロック保護。テスト用・設定変更用）
       - `_resolve_openai_base_url() -> Optional[str]`:
         - `explicit = os.environ.get("OPENAI_BASE_URL", "").strip()` が非空なら `explicit`
         - `_is_truthy_env("PAPER_ORCHESTRA_CPA")` なら `DEFAULT_CPA_BASE_URL`
         - それ以外は `None`
       - `_read_claude_settings_token(settings_path: Optional[str] = None) -> Optional[str]`:
         - パスは `settings_path or os.path.expanduser("~/.claude/settings.json")`
         - ファイルが存在すれば JSON 読み込みし `data.get("env", {}).get("ANTHROPIC_AUTH_TOKEN")` を `.strip()` して返す（失敗時は例外にファイル内容を出さず `None` を返す）。
       - `_resolve_openai_api_key() -> str`:
         - `OPENAI_API_KEY`（strip 後非空）を優先。
         - 無く、かつ `_is_truthy_env("PAPER_ORCHESTRA_CPA")` のとき `_read_claude_settings_token()` を参照。
         - それでも無ければ `ValueError("OPENAI_API_KEY must be set (or configure env.ANTHROPIC_AUTH_TOKEN in ~/.claude/settings.json when PAPER_ORCHESTRA_CPA=1).")` を送出（キー値は絶対に出さない）。
       - `_replace_proxy_port(proxy_url: str, port: int = CPA_TUNNEL_PROXY_PORT) -> str`:
         - 前後空白除去。`"://"` が無ければ `"http://"` を仮付与して `urlsplit`。
         - `hostname` が空なら `ValueError("Invalid HTTPS_PROXY URL.")`。
         - IPv6 ホスト（`":" in hostname`）は `[hostname]` で囲む。
         - `userinfo`（`username` / `password`）を保持したまま `netloc` を再構成し、`port` を `3130` に置換して `urlunsplit` で返す。
       - `_build_tunnel_http_client() -> Optional[httpx.Client]`:
         - `_is_truthy_env("PAPER_ORCHESTRA_CPA_TUNNEL")` が `False` なら `None` を返す（tailnet 内のマシンでは一切通さない）。
         - `raw_proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip()`
         - 空なら `ValueError("PAPER_ORCHESTRA_CPA_TUNNEL=1 requires HTTPS_PROXY to be set.")`。
         - `tunnel_url = _replace_proxy_port(raw_proxy, CPA_TUNNEL_PROXY_PORT)`
         - `return httpx.Client(proxy=tunnel_url)`
       - `_redact_secret(text: str, secret: Optional[str]) -> str`:
         - `secret` が非空なら `text.replace(secret, "[REDACTED]")` を返す。
     - `get_openai_client()` 更新:
       - `kwargs: dict[str, Any] = {"api_key": _resolve_openai_api_key()}`
       - `base_url = _resolve_openai_base_url()` が非 `None` なら `kwargs["base_url"] = base_url`
       - `http_client = _build_tunnel_http_client()` が非 `None` なら `kwargs["http_client"] = http_client`
       - `_openai_client = OpenAI(**kwargs)`
     - `call_openai_models_with_content()` 更新:
       - 引数型を `content: Union[str, list[Any]]` に拡張、`generation_configs: Optional[dict] = None`（内部で `generation_configs or {}`）にする。
       - `except Exception as e:` 内で `_redact_secret(str(e), getattr(openai_client, "api_key", None))` を使って `Warning:` ログを出す。最終リトライ失敗時も、元の例外文字にキーが含まれていればマスクした `RuntimeError` に差し替えて送出する（キーが含まれなければ元の `e` を再送出）。
     - `get_openai_image_part_from_path()` 更新:
       - `mime_type, _ = mimetypes.guess_type(image_path)`、`mime_type = mime_type if (mime_type and mime_type.startswith("image/")) else "image/png"`
       - `"url": f"data:{mime_type};base64,{base64_image}"`
- **完了判定**: `/tmp/po-venv/bin/python -m pytest tests/test_llm_backend.py -q` が全件 PASS。
- **推奨コミット**: `feat(openai): support CPA mode, Claude settings token fallback, and tunnel proxy`

---

### T3: ルーティング統一と `call_llm_with_contents()` の追加（SPEC A2, A5）
- **依存**: T2
- **対象ファイル**: `utils/llm_backend_utils.py`, `tests/test_llm_backend.py`
- **変更内容（TDD: テスト先行）**:
  1. `tests/test_llm_backend.py` に以下を追加:
     - `LLM_BACKEND=openai` のとき、`claude-sonnet-5-5`、`preview/DeepSeek-V4.1-Flash-1017`、`gemini-3.1-pro-preview` いずれのモデル名でも `is_openai_backend()` が `True`、`get_llm_parser()` が `parse_openai_json_results`、`call_llm_with_text_prompt` / `call_llm_with_images` / `call_llm_with_pdf` / `call_llm_with_contents` すべて OpenAI 側（モック）にルーティングされること。
     - `LLM_BACKEND` 未設定時は従来どおり `gpt-*` / `o1*` / `o3*` のみ OpenAI、それ以外（`gemini-*` 等）は Gemini 側（モック）に回ること（後方互換）。
     - `LLM_BACKEND` に不正値（例: `"invalid"`）を入れた場合は `ValueError` になること。
     - `call_llm_with_contents(contents=["a", "b"], model_name="claude-sonnet-5-5", system_instruction="sys")` が OpenAI 側では `content="a\n\nb"`, `system_prompt="sys"` として `call_openai_models_with_content` を呼ぶこと。
     - `call_llm_with_contents(..., tools=[object()])`（または `generation_configs={"tools": [object()]}`）を `LLM_BACKEND=openai` で呼ぶと `NotImplementedError` を明示送出すること。
     - `call_llm_with_contents` が Gemini 側（`LLM_BACKEND` 未設定・`gemini-3.1-pro-preview`）では `types.Part.from_text` のリストと `generation_configs`（`system_instruction`, `tools` 含む）に変換して `call_gemini_with_contents` を呼ぶこと。
     - `call_llm_with_images` で `images=[]` かつ OpenAI 側のとき、`content` に単一文字列 `prompt` が渡ること。
  2. `utils/llm_backend_utils.py` を実装:
     - `import os` と `from typing import Optional, Callable, Dict, Any, Sequence` を追加。
     - `is_openai_backend(model_name: str) -> bool` を新設:
       ```python
       def is_openai_backend(model_name: str) -> bool:
           backend = os.environ.get("LLM_BACKEND", "").strip().lower()
           if backend == "openai":
               return True
           if backend == "gemini":
               return False
           if backend:
               raise ValueError(
                   f"Unsupported LLM_BACKEND={backend!r}. Use 'openai', 'gemini', or leave unset."
               )
           lower = (model_name or "").lower()
           return "gpt" in lower or "o1" in lower or "o3" in lower
       ```
       - **重要**: モデル名のホワイトリストは絶対に作らない（SPEC A5）。
     - `get_llm_parser()`, `call_llm_with_text_prompt()`, `call_llm_with_images()`, `call_llm_with_pdf()` の `"gpt"/"o1"/"o3"` 判定をすべて `is_openai_backend(model_name)` に統一。
     - `call_llm_with_images()` の OpenAI 分岐で:
       - `images` が空のとき `content = prompt`（単一文字列）。
       - `images` が非空のとき従来どおり `content = [{"type": "text", "text": prompt}]` + `get_openai_image_part_from_path(image_path)`。
     - `call_llm_with_pdf()` の `temperature: Optional[float] = 0.7` にし、Gemini 分岐で `temperature is not None` のときのみ `generation_configs["temperature"] = temperature` を設定。
     - `call_llm_with_contents()` を新設:
       ```python
       def call_llm_with_contents(
           contents: Sequence[str],
           model_name: str,
           system_instruction: Optional[str] = None,
           tools: Optional[list[Any]] = None,
           generation_configs: Optional[Dict[str, Any]] = None,
           check_parsed_response_not_none: bool = True,
           return_json: bool = True,
           result_parsing_func: Optional[Callable] = None,
           max_retries: int = 5,
           base_interval_sec: int = 5,
       ) -> Dict[str, Any]:
       ```
       - `contents` の各要素が `str` であることを検証（`isinstance(contents, str)` の場合は `[contents]` とするか `TypeError` とするか明確化：安全のため `if isinstance(contents, str): text_parts = [contents]`、それ以外は `list(contents)` で非 `str` 要素があれば `TypeError("call_llm_with_contents only accepts text fragments (str).")`）。
       - `configs = dict(generation_configs) if generation_configs else {}`
       - `cfg_sys = configs.pop("system_instruction", None)` → `effective_sys = system_instruction if system_instruction is not None else cfg_sys`
       - `cfg_tools = configs.pop("tools", None)` → `effective_tools = tools if tools is not None else cfg_tools`
       - `parser = result_parsing_func if result_parsing_func is not None else get_llm_parser(model_name, return_json)`
       - OpenAI 側（`is_openai_backend(model_name)` が `True`）:
         - `if effective_tools:` → `raise NotImplementedError("tools (such as Google Search grounding) are not supported with the OpenAI-compatible backend.")`
         - `configs.pop("temperature", None)`
         - `joined_content = "\n\n".join(text_parts)`
         - `return call_openai_models_with_content(content=joined_content, model_name=model_name, generation_configs=configs, result_parsing_func=parser, check_parsed_response_not_none=check_parsed_response_not_none, max_retries=max_retries, base_interval_sec=base_interval_sec, system_prompt=effective_sys)`
       - Gemini 側:
         - `gemini_contents = [types.Part.from_text(text=part) for part in text_parts]`
         - `if effective_sys is not None: configs["system_instruction"] = effective_sys`
         - `if effective_tools is not None: configs["tools"] = effective_tools`
         - `return call_gemini_with_contents(contents=gemini_contents, model_name=model_name, generation_configs=configs, result_parsing_func=parser, check_parsed_response_not_none=check_parsed_response_not_none, max_retries=max_retries, base_interval_sec=base_interval_sec)`
- **完了判定**: `/tmp/po-venv/bin/python -m pytest tests/test_llm_backend.py -q` が全件 PASS。
- **推奨コミット**: `feat(llm): add LLM_BACKEND routing and unified call_llm_with_contents`

---

### T4: 直接 Gemini 呼び出しの統一置き換え（SPEC A3 + 関連 autoraters 修正）
- **依存**: T3
- **対象ファイル**:
  - `methods/agents/outline_agent.py`
  - `methods/agents/literature_review_agent.py`（`_synthesize_content` の呼び出し変更のみ）
  - `methods/agents/content_refinement_agent.py`
  - `utils/content_parsing_utils.py`
  - `autoraters/agent_review.py`
  - `autoraters/citation_f1.py`
  - `autoraters/sxs_lit_review_quality.py`
  - `autoraters/sxs_paper_quality.py`
  - `tests/test_llm_backend.py`（エージェント呼び出し経路テスト追記）
- **変更内容**:
  1. `methods/agents/outline_agent.py`:
     - `call_gemini_with_contents` と `from google.genai import types` の import を削除し、`from utils.llm_backend_utils import call_llm_with_contents` に変更。
     - `OutlineAgent.run()` (L38–54) を以下に変更:
       ```python
       response_dict = call_llm_with_contents(
           contents=[
               "'idea.md':\n" + (load_md_file(idea_file) or ""),
               "'experimental_log.md':\n" + (load_md_file(experimental_log_file) or ""),
               "'template.tex':\n" + (load_md_file(latex_template_file) or ""),
               load_md_file(conference_guidelines_file) or "",
           ],
           system_instruction=outline_agent_system_prompt.format(
               cutoff_date=self.cutoff_date
           ),
           model_name=self.model_name,
       )
       ```
  2. `methods/agents/literature_review_agent.py`（`_synthesize_content` のみ。探索は T7）:
     - `from utils.llm_backend_utils import call_llm_with_contents` を追加。
     - `_synthesize_content()` (L499–504) の `call_gemini_with_contents(...)` を `call_llm_with_contents(contents=[user_prompt, json.dumps(context_payload)], model_name=self.model_name, system_instruction=writer_system_prompt, result_parsing_func=parse_gemini_latex_results)` に変更。
  3. `methods/agents/content_refinement_agent.py`:
     - `from google.genai import types`、`call_gemini_with_contents`、`call_gemini_with_images` の import を削除。`parse_gemini_json_latex_response` のみ `utils.gemini_utils` から import 継続。
     - `from utils.llm_backend_utils import call_llm_with_contents, call_llm_with_images, call_llm_with_pdf` を追加。
     - `_get_formatting_review()` (L165–169): `call_gemini_with_images` → `call_llm_with_images`。
     - `run()` 本文推敲ループ (L288–303): `with open(valid_pdf_for_context, "rb")` を削除し、`call_llm_with_pdf(...)` へ置換:
       ```python
       response_dict = call_llm_with_pdf(
           pdf_path=valid_pdf_for_context,
           prompt=user_prompt_text,
           model_name=self.model_name,
           system_instruction=self.system_prompt,
           temperature=None,
           result_parsing_func=parse_gemini_json_latex_response,
       )
       ```
     - `run()` フォーマット修正ループ (L520–527): `call_gemini_with_contents` → `call_llm_with_contents(contents=[prompt], model_name=self.model_name, result_parsing_func=lambda x: {"raw_response": x}, check_parsed_response_not_none=False)`。L497 のログ `"Calling Gemini to fix..."` も `"Calling LLM to fix..."` に更新。
  4. `methods/agents/section_writing_agent.py`:
     - L201 が既に `call_llm_with_images` 経由であることを再確認（変更なし）。
  5. `utils/content_parsing_utils.py`:
     - `from .gemini_utils import call_gemini_with_text_prompt` → `from .llm_backend_utils import call_llm_with_text_prompt`。
     - `extract_paper_title_from_citation(citation_text: str, model_name: str = "gemini-3-flash-preview") -> str` に引数追加し、`call_llm_with_text_prompt(prompt=instruction, model_name=model_name, check_parsed_response_not_none=False, return_json=False)` を呼ぶ。
  6. `autoraters/agent_review.py`:
     - L19 `from utils.gemini_utils import call_gemini_with_text_prompt` → `from utils.llm_backend_utils import call_llm_with_text_prompt`。
     - L137, L172, L200 の全 3 か所を `call_llm_with_text_prompt` に置換。
  7. `autoraters/citation_f1.py`:
     - **L41 `print(f"Loaded S2 API Key for this project: {S2_API_KEY}")` を削除**（秘密漏洩修正）。
     - L105 `delayed(extract_paper_title_from_citation)(ref)` → `delayed(extract_paper_title_from_citation)(ref, model_name=self.model_name)`。
  8. `autoraters/sxs_lit_review_quality.py`:
     - L39–67 の `if "gemini" in model_name: ... elif "gpt" in model_name: ...` を撤去し、`call_llm_with_text_prompt(prompt=prompt, model_name=model_name, generation_configs={"system_instruction": sxs_lit_review_quality_system_prompt, "temperature": 0.0})` を 1 本で呼ぶ（OpenAI 側の `temperature` 除去は `call_llm_with_text_prompt` 側で行われる）。
  9. `autoraters/sxs_paper_quality.py`:
     - L26 に `is_openai_backend` を追加 import し、L58–62 を `is_openai = is_openai_backend(model_name)` に置換。
  10. `tests/test_llm_backend.py` に、`OutlineAgent.run`・`extract_paper_title_from_citation`・`perform_review_agentreview`・`rate_sxs_lit_review` が `LLM_BACKEND=openai` + CPA モデル名（例: `preview/DeepSeek-V4.1-Flash-1017`）で Gemini クライアントを一切呼ばず OpenAI 経路を通るモックテストを追加。
- **完了判定**:
  - `rg -n "call_gemini_" methods/ autoraters/ utils/content_parsing_utils.py` の出力が `methods/agents/literature_review_agent.py`（`_discover_candidates` の Gemini 分岐のみ）と `autoraters/sxs_paper_quality.py`（Gemini 分岐のみ）に限られること。
  - `/tmp/po-venv/bin/python -m pytest tests/test_llm_backend.py -q` が全件 PASS。
- **推奨コミット**: `refactor(agents): route LLM calls through unified llm_backend_utils`

---

### T5: 作図モードの OpenAI バックエンド明示エラーガード（SPEC A4）
- **依存**: T3
- **対象ファイル**: `paper_writing_cli.py`, `methods/paper_writer_with_plotting.py`, `methods/agents/plotting_agent.py`, `utils/paper_banana_utils.py`, `tests/test_llm_backend.py`
- **変更内容（TDD: テスト先行）**:
  1. ガード条件: `is_openai_backend(image_model_name)` が `True`（すなわち `LLM_BACKEND=openai`、または画像モデル名が OpenAI 系）のとき、Gemini 専用の画像生成作図（`gemini-3-pro-image-preview`）は未対応として即エラー停止する。
  2. エラーメッセージ（全箇所で統一した分かりやすい案内を出す）:
     ```text
     Plotting (--use_plotting true) is not supported with LLM_BACKEND=openai because diagram image generation requires Gemini (gemini-3-pro-image-preview). Run with --use_plotting false or switch to the Gemini backend.
     ```
  3. ガード配置箇所（握りつぶしと 3 回リトライを確実に防ぐため 4 境界に配置）:
     - `paper_writing_cli.py`: `if args.use_plotting:` に入った直後、`write_single_paper` 呼び出し前に `is_openai_backend(args.image_model_name)` をチェックし、True なら `print(f"Error: {msg}", file=sys.stderr)` のうえ `sys.exit(1)`。
     - `methods/paper_writer_with_plotting.py`: `write_single_paper()` 先頭（`while (not succeed) and (cur_try < max_n_tries):` の**前**）で `if is_openai_backend(image_model_name): raise NotImplementedError(msg)`。
     - `methods/agents/plotting_agent.py`: `PlottingAgent.run()` 先頭（`ThreadPoolExecutor` の前）で `if is_openai_backend(self.image_model_name): raise NotImplementedError(msg)`。
     - `utils/paper_banana_utils.py`: `generate_figure_visuals()` の `else:`（diagram 画像生成分岐）先頭で `if is_openai_backend(image_model_name): raise NotImplementedError(msg)`。
  4. `tests/test_llm_backend.py` に、`LLM_BACKEND=openai` 下で `PlottingAgent.run()` / `paper_writer_with_plotting.write_single_paper()` / `generate_figure_visuals("diagram", ...)` が即座に `NotImplementedError` を送出すること、および `paper_writing_cli.main()` に `--use_plotting true` を渡すと `SystemExit(1)` になることのテストを追加。
- **完了判定**: `/tmp/po-venv/bin/python -m pytest tests/test_llm_backend.py -q` が全件 PASS。
- **推奨コミット**: `feat(plotting): fail fast with clear error when plotting is enabled under OpenAI backend`

---

### T6: `utils/scholar_utils.py` の OpenAlex / Crossref 照合・API 探索・優先補完実装（SPEC B1, B2）
- **依存**: T1
- **対象ファイル**: `utils/scholar_utils.py`, `tests/test_scholar_utils.py`（新規作成）
- **変更内容（TDD: テスト先行）**:
  1. `tests/test_scholar_utils.py` を新規作成し、ネットワークなし（`requests.get` をモック）で以下をテストする:
     - **S2 キー非出力**: `utils/scholar_utils.py` の import / reload 時に `SEMANTIC_SCHOLAR_API_KEY` が stdout/stderr に一切出力されないこと。
     - **OpenAlex 要旨復元 (`reconstruct_openalex_abstract`)**:
       - `{"Deep": [0], "learning": [1, 4], "for": [2], "vision": [3], "models": [5]}` → `"Deep learning for vision learning models"`
       - `None` / `{}` → `""`
     - **OpenAlex 題名検索 (`openalex_title_search`)**:
       - モック応答（`display_name`, `authorships`, `primary_location.source.display_name`, `publication_year`, `publication_date`, `abstract_inverted_index`, `cited_by_count`, `biblio`）を `s2_title_search` と同一辞書形（`title`, `authors=[{"name": ...}]`, `venue`, `year`, `abstract`, `citationCount`, `journal={"name", "volume", "pages"}`, `publicationDate`）へ正しく変換すること。
       - `OPENALEX_EMAIL` 設定時に `mailto` クエリパラメータが付与され、未設定時は付与されないこと。
       - `thefuzz.fuzz.ratio` + 年一致 `+10` + 閾値 `> 70` が機能し、不一致は `None` になること。
       - `cutoff_date` より後の論文が除外されること。
     - **Crossref 題名検索 (`crossref_title_search`)**:
       - モック応答（`message.items` の `title`, `author`, `container-title`, `published-print`/`issued`, `abstract` 内の `<jats:p>...</jats:p>` タグ、 `is-referenced-by-count`, `volume`, `page`）を同一辞書形へ変換し、JATS タグが除去されること。
       - 要旨がない Crossref 応答では `abstract == ""` で返ること。
       - `cutoff_date` より後の論文が除外されること。
     - **照合の優先順位と要旨補完 (`enrich_paper_metadata`)**:
       - Case 1: S2 で要旨あり一致 → OpenAlex / Crossref を呼ばず S2 の結果を返し、`_source == "Semantic Scholar"`, `_abstract_source == "Semantic Scholar"`。
       - Case 2: **S2 で一致したが要旨が空 → OpenAlex を呼んで要旨だけ補完**（メタデータは S2 を保持、要旨は OpenAlex から取得、`_source == "Semantic Scholar"`, `_abstract_source == "OpenAlex"`、Crossref は呼ばない）。
       - Case 3: S2 で不一致（`None`）→ OpenAlex で要旨あり一致 → OpenAlex の結果を返す（`_source == "OpenAlex"`）。
       - Case 4: S2・OpenAlex ともに要旨空または不一致 → Crossref から要旨補完または Crossref で一致。
       - Case 5: 全ソース不一致 → `None`。
     - **API 探索 (`s2_search`, `openalex_search`)**:
       - `s2_search(query, limit)` と `openalex_search(query, limit)` が `[{"title": ..., "year": ..., "reason": ...}, ...]` 形式で候補を返し、`cutoff_date` 後の論文のみ除外し、会場名・被引用数によるフィルタを掛けないこと。
  2. `utils/scholar_utils.py` を実装:
     - **L28 `print(f"Using S2 API Key: {S2_API_KEY}")` を削除**。
     - `_get_s2_headers()` と `_get_openalex_email()` で毎回 `os.environ` を参照（テスト時の `monkeypatch.setenv` に追従）。
     - `is_date_valid(p_date: Optional[str], p_year: Optional[int], cutoff_str: Optional[str]) -> bool` をモジュールレベルに抽出（`YYYY-MM`, `YYYY-MM-DD`, `YYYY` を安全に解析）。
     - `reconstruct_openalex_abstract(abstract_inverted_index: Optional[Dict[str, List[int]]]) -> str` を実装。
     - `strip_jats_tags(text: Optional[str]) -> str` を実装（`re.sub(r"<[^>]+>", " ", text)` → `html.unescape` → 連続空白正規化）。
     - `_select_best_title_match(candidates, title_query, year_hint=None, cutoff_date=None, threshold=70) -> Optional[Dict]` を実装（`fuzz.ratio`、年一致 `+10`、`highest_ratio > threshold`、日付フィルタ）。
     - `s2_title_search(title_query: str, year_hint: Optional[int] = None, cutoff_date: Optional[str] = None) -> Optional[Dict]` を共通ヘルパ化（返り値辞書形と URL・fields は従来維持）。
     - `openalex_title_search(title_query: str, year_hint: Optional[int] = None, cutoff_date: Optional[str] = None) -> Optional[Dict]` を実装:
       - `GET https://api.openalex.org/works`、`params={"search": title_query, "per-page": 3}`（`OPENALEX_EMAIL` があれば `"mailto"` 追加）、`timeout=10`。
       - 各 work を正規化辞書へ変換し `_select_best_title_match` を適用。
       - `journal`: `biblio` から `volume`（文字列または `None`）と `pages`（`first_page`・`last_page` から `f"{first}-{last}"` または片方のみなら `str(first)`、無ければ `None`）を取り、`{"name": venue or None, "volume": volume, "pages": pages}` とする。
     - `crossref_title_search(title_query: str, year_hint: Optional[int] = None, cutoff_date: Optional[str] = None) -> Optional[Dict]` を実装:
       - `GET https://api.crossref.org/works`、`params={"query.title": title_query, "rows": 3}`、`timeout=10`。
       - 各 item を正規化辞書へ変換（JATS タグ除去）し `_select_best_title_match` を適用。
     - `enrich_paper_metadata(title_query: str, year_hint: Optional[int] = None, cutoff_date: Optional[str] = None) -> Optional[Dict]` を実装:
       - `sources = [("Semantic Scholar", s2_title_search), ("OpenAlex", openalex_title_search), ("Crossref", crossref_title_search)]`
       - 順に呼び出し、最初の一致結果を `merged` に保持。`(merged.get("abstract") or "").strip()` が空なら次ソースへ進み、次ソースで要旨が取れたら `merged["abstract"]` のみ補完して打ち切る。
       - `merged` が得られたら `merged["_source"] = matched_source`、`merged["_abstract_source"] = abstract_source` を付与して返す。
     - `s2_search(query: str, limit: int = 10, cutoff_date: Optional[str] = None) -> List[Dict]` を実装（`GET https://api.semanticscholar.org/graph/v1/paper/search`, `fields="title,year,publicationDate"`, `{"title": ..., "year": r.get("year") or 0, "reason": f"Semantic Scholar search match for: {query}"}`）。HTTP 429 時は短時間リトライ 1 回（または安全に `[]` 返却、テストで不要な sleep が走らないよう設定）。
     - `openalex_search(query: str, limit: int = 10, cutoff_date: Optional[str] = None) -> List[Dict]` を実装（`GET https://api.openalex.org/works`, `params={"search": query, "per-page": limit}` + 任意 `mailto`, `{"title": ..., "year": w.get("publication_year") or 0, "reason": f"OpenAlex search match for: {query}"}`）。
- **完了判定**: `/tmp/po-venv/bin/python -m pytest tests/test_scholar_utils.py -q` が全件 PASS。
- **推奨コミット**: `feat(scholar): add OpenAlex/Crossref title search, API discovery, and abstract backfill`

---

### T7: 文献エージェントの多情報源照合・API 探索切替・引用 90% 強制廃止（SPEC B1, B2, B3）
- **依存**: T3, T4, T6
- **対象ファイル**: `methods/agents/literature_review_agent.py`, `methods/prompts/literature_review_agent.py`, `tests/test_scholar_utils.py`（または `tests/test_literature_review_agent.py` 新設）
- **変更内容（TDD: テスト先行）**:
  1. テスト追加（モック使用・ネットワークなし）:
     - `_enrich_and_register_paper`: S2 要旨空→OpenAlex で要旨補完された論文がドロップされず `registry` に登録されること、および標準出力に一致情報源名（例: `[Semantic Scholar, abstract: OpenAlex]`）が出力され API キーが含まれないこと。
     - `_enrich_and_register_paper`: 全ソースで要旨が空の論文は登録されないこと。
     - `_discover_candidates`（`LLM_BACKEND=openai`）:
       - `exploration` 型: `s2_search` と `openalex_search` の両方を呼び、重複タイトルを除去して候補を返すこと（Gemini は呼ばない）。
       - `targeted` 型: `"Vaswani et al. (Attention Is All You Need)"` のようなヒントから題名を抽出し、B1 題名検索関数で 1 件を狙って返すこと（全滅時フォールバックで最大 1 件）。
     - `_synthesize_content` & プロンプト: `literature_review_agent_writter_prompt` に `min_cite_paper_count` や `90%` 強制が存在せず、`len(papers)` のみで正常に `.format(paper_count=..., cutoff_date=...)` できること。
  2. `methods/prompts/literature_review_agent.py` (L33):
     - `- You MUST cite at least {min_cite_paper_count} of them across the introduction and related work sections.` を以下に置き換える:
       ```text
       - Cite ONLY papers from 'collected_papers' that are genuinely relevant and actually used to support your arguments; do NOT pad citations or cite weakly related papers.
       ```
  3. `methods/agents/literature_review_agent.py`:
     - import: `from utils.llm_backend_utils import call_llm_with_contents, is_openai_backend` と `from utils.scholar_utils import enrich_paper_metadata, s2_title_search, openalex_title_search, crossref_title_search, s2_search, openalex_search` を整備。
     - `__init__` (L87): `self.google_search_tool = None` に変更し、Gemini 探索分岐内で必要時のみ `types.Tool(google_search=types.GoogleSearch())` を遅延生成する。
     - `_execute_tasks_in_parallel()` (L175–185):
       - `use_api_search = is_openai_backend(self.model_name)` で分岐し、ログを `"Phase 1a: API Candidates Discovery (S2 + OpenAlex)..."` / `"Phase 1a: Google Candidates Discovery (MaxPool=10)..."` に分ける。
       - `use_api_search` 時は S2 の 1 req/sec レート制限による 429 連鎖を避けるため `pool_workers = 2` に抑える（Phase 1b のログも `"Phase 1b: Paper Metadata Enrichment (S2 -> OpenAlex -> Crossref)..."` に更新）。
     - `_extract_targeted_query(self, focus: str) -> List[str]` 補助メソッドを追加:
       - 例: `"Vaswani et al. (Attention Is All You Need)"` → 括弧内 `"Attention Is All You Need"` を最優先クエリに抽出。
       - 例: `"research paper or technical report introducing 'FlashAttention-2'"` → 引用符内 `"FlashAttention-2"` を抽出。
       - 元の `focus` 文字列も重複しない範囲で候補に含める。
     - `_discover_candidates()` (L426–475):
       - `if is_openai_backend(self.model_name):` 分岐を先頭に追加:
         - `targeted` 型: `_extract_targeted_query(task["focus"])` の各クエリに対し `s2_title_search` → `openalex_title_search` → `crossref_title_search` を順に試し、最初に見つかった 1 件を `[{"title": hit["title"], "year": hit.get("year") or 0, "reason": task.get("context") or task["focus"]}]` として即返却。すべて `None` の場合のみフォールバックとして `openalex_search(primary_q, limit=1, cutoff_date=cutoff_date)` → 空なら `s2_search(primary_q, limit=1, cutoff_date=cutoff_date)` の先頭 1 件を返す。
         - `exploration` 型: `query = task["focus"]` で `s2_search(query, limit=8, cutoff_date=cutoff_date)` と `openalex_search(query, limit=8, cutoff_date=cutoff_date)` を両方実行し、`self._normalize_title(c["title"])` で重複除去した候補リストを返す。トップ会議・高被引用フィルタは掛けない。
       - `else:`（Gemini モード）: 従来の Google Search grounding 経路を維持（`self.google_search_tool` を遅延初期化）。
     - `_enrich_and_register_paper()` (L326–384):
       - `paper_data = enrich_paper_metadata(cand_title, cand_year, cutoff_date)` に変更。
       - `if not paper_data: return`
       - `if not self._is_paper_allowed(paper_data, cutoff_date): return`
       - `if not (paper_data.get("abstract") or "").strip(): return`
       - `year_val = paper_data.get("year") or cand_year or 0`（`int(year_val)` で `PaperData.year: int` の検証エラーを防止）。
       - 一致情報源表示:
         - `src = paper_data.get("_source") or "Unknown"`
         - `abs_src = paper_data.get("_abstract_source")`
         - `src_label = src if (not abs_src or abs_src == src) else f"{src}, abstract: {abs_src}"`
         - `self._safe_print(f"      -> 📚 Added enriched paper [{src_label}]: {full_obj.title}")`
       - `except Exception as e:` は無言 `pass` をやめ `self._safe_print(f"      ⚠️ Failed to enrich paper '{cand_title}': {type(e).__name__}")`（キー非出力）。
     - `_synthesize_content()` (L492–496): `min_cite_paper_count=int(len(papers) * 0.9),` を削除。
- **完了判定**: `/tmp/po-venv/bin/python -m pytest tests/ -q` が全件 PASS。
- **推奨コミット**: `feat(lit-review): use multi-source enrichment, API discovery in openai mode, and drop 90% citation rule`

---

### T8: スモークテスト用最小素材の作成（SPEC D）
- **依存**: なし
- **対象ファイル**:
  - `examples/smoke/idea_sparse.md`
  - `examples/smoke/experimental_log.md`
  - `examples/smoke/figures/info.json`
- **変更内容**:
  1. `examples/smoke/idea_sparse.md`: Outline / Literature / Writing を最小コストで回せる簡潔な架空研究アイデア（英語 20〜35 行程度。Problem Statement / Hypothesis / Method / Contributions を含む）。
  2. `examples/smoke/experimental_log.md`: 上記アイデアに対応する最小限の実験設定（Dataset, Baselines, Metric, Optimizer）と小さな Markdown 結果表（1 テーブル・3〜4 行）。
  3. `examples/smoke/figures/info.json`: `[]\n`（**必ず空の JSON 配列 `[]` にすること**。`SectionWritingAgent._format_figures_context()` が `List[Dict]` を前提としているため、`{}` ではなく `[]` が正）。
- **完了判定**:
  - 3 ファイルが存在すること。
  - `/tmp/po-venv/bin/python -c "import json; d = json.load(open('examples/smoke/figures/info.json')); assert isinstance(d, list)"` が通ること。
  - 実 CPA API 呼び出しは gpu-01 では実行せず（キーを置かない方針）、Muse 側検証に引き継ぐこと。
- **推奨コミット**: `chore(examples): add minimal smoke test materials`

---

### T9: `.env.example`・`README_MOD.md`・`flake.nix` の作成（SPEC C）
- **依存**: T2〜T8
- **対象ファイル**: `.env.example`, `README_MOD.md`, `flake.nix`
- **変更内容**:
  1. `.env.example` を作成（**実キー・実トークンは一切書かない**）:
     - `LLM_BACKEND=openai`
     - `PAPER_ORCHESTRA_CPA=1`
     - `PAPER_ORCHESTRA_CPA_TUNNEL=0`（Muse VM などトンネル経由時は `1` にする旨をコメント記載）
     - `OPENAI_BASE_URL=http://m4-mac-mini-32gb.tail5df0ca.ts.net:8317/v1`
     - `OPENAI_API_KEY=`（CPA モード時は未設定なら `~/.claude/settings.json` の `env.ANTHROPIC_AUTH_TOKEN` を参照する旨コメント）
     - `OPENALEX_EMAIL=`（polite pool 用の任意設定。例: `your-email@example.com` をコメント表記）
     - `SEMANTIC_SCHOLAR_API_KEY=`（任意）
     - 既存 Gemini 互換用の変数（`GEMINI_API_KEY`, `VERTEX_AI_PROJECT`, `VERTEX_AI_LOCATION`）をコメントアウトで併記
     - モデル指定例コメント: `--writer_model_name preview/DeepSeek-V4.1-Flash-1017` / `gpt-5.6-luna` / `claude-sonnet-5-5`
  2. `README_MOD.md` を作成（SPEC C の全項目を網羅）:
     - 改造点の一覧（CPA/OpenAI 互換統一、OpenAlex/Crossref 照合＆要旨補完、API 探索切替、引用 90% 強制撤廃、S2 API キー出力修正、作図ガード）
     - CPA での動かし方（tailnet 内 gpu-01 での直接実行例と、Muse VM での `PAPER_ORCHESTRA_CPA_TUNNEL=1` + `HTTPS_PROXY` ポート 3130 置換実行例、`--writer_model_name` / `--reflection_model_name` 指定例）
     - OpenAlex / Crossref の位置づけ（S2 → OpenAlex → Crossref 優先順位、要旨空時の補完、`LLM_BACKEND=openai` 時の API 探索、`OPENALEX_EMAIL` の役割）
     - 作図の制約（v1 は `--use_plotting false` が対象、`LLM_BACKEND=openai` で `--use_plotting true` を指定すると明示エラーで停止する仕様、画像生成 `gemini-3-pro-image-preview` は Gemini 専用として残置）
     - 単体テストとスモークテスト手順（`pytest`・`compileall` の回し方、および Muse 側で `examples/smoke/` を使って Outline 段だけ回す検証ワンライナー例）
  3. `flake.nix` を作成:
     - `nixpkgs`（`nixos-24.11` 等）をベースに `x86_64-linux` / `aarch64-linux` / `aarch64-darwin` 向けの `devShells.<system>.default` を定義。
     - `pkgs.python311`、`pkgs.uv`、OpenCV/PyMuPDF 向け基本ネイティブライブラリ、および `pdflatex`・`bibtex` と CVPR/ICLR テンプレートが使うパッケージ群（`scheme-medium` または `texlive.combine` で `scheme-small` + `bibtex` + `collection-latexextra` + `collection-fontsrecommended` + `natbib` + `booktabs` + `cleveref` + `hyperref` + `xcolor` + `microtype` + `times` 等）を含める。
     - `shellHook` で `uv venv --python python3.11` / `uv pip install -r requirements.txt` の案内を出す。
     - gpu-01 には `nix` コマンドが無いため、ファイル作成＋括弧対応等の構文目視チェックまでで完了とする（SPEC C 準拠）。
- **完了判定**:
  - 3 ファイルが存在し、`.env` 本体が作成されていないこと。
  - 秘密値・実キーが含まれていないこと。
- **推奨コミット**: `docs(env): add .env.example, README_MOD.md, and flake.nix`

---

### T10: 全体検証・秘密検査・最終クリーン確認（SPEC D 最終ゲート）
- **依存**: T1〜T9
- **検証コマンド**:
  ```bash
  /tmp/po-venv/bin/python -m compileall .
  /tmp/po-venv/bin/python -m pytest tests/ -q
  git status
  git log --oneline d9bae99..HEAD
  ```
- **完了判定（すべて満たすこと）**:
  1. `compileall` がエラー 0 件で終了コード 0 になること。
  2. `pytest tests/ -q` が全テスト PASS（0 failed, 0 errors）で、外部ネットワーク不要であること。
  3. `.env` ファイルが作業ディレクトリに存在しないこと。
  4. `git diff d9bae99..HEAD` に API キー・トークン・実メールアドレスが含まれないこと。
  5. `git status` で変更・ステージ済み差分がゼロであり、未追跡ファイルは指示元の `SPEC_MOD.md` と `BRIEF_NEON.md` 以外に残っていないこと（`SPEC_MOD.md`・`BRIEF_NEON.md` はコミット履歴にも含まれていないこと）。
  6. すべてのコミットが `mod/cpa-openalex` ブランチ上に積まれていること。

---

## 4. リスクと未確定事項

### 4.1 作図の扱い（SPEC A4）
- **現状判断**: v1 では `utils/paper_banana_utils.py` のテキスト系関数（`retrieve_few_shot_examples`, `style_figure_content`, `generate_figure_visuals(task_name="plot")` 等）の移植は行わず、`LLM_BACKEND=openai` で `--use_plotting true` が有効な場合は入口（CLI・`paper_writer_with_plotting`・`PlottingAgent`・`generate_figure_visuals`）で明示エラー停止する。
- **リスク / 将来課題**: `OutlineAgent` が生成する `plotting_plan` には通常 `"diagram"` と `"plot"` が混在するため、plot（matplotlib コード生成）だけ CPA 化しても diagram 画像生成で止まる。v2 で plot のみ CPA 許可するフラグや画像生成 API 差し替えを検討する余地がある。

### 4.2 CPA モデル名と Vision（画像入力）対応の差異（SPEC A5）
- **既知事実**: 2026-11-23 実測の CPA モデル 23 件に Gemini 系・`devin/swe-2` は無く、実呼び出し確認済みは `preview/DeepSeek-V4.1-Flash-1017`（他に `gpt-5.6-luna`, `claude-sonnet-5-5` 等）。
- **未確定事項 / リスク**:
  1. Step 3 (`SectionWritingAgent.run`) は `figures/` に `.png`/`.jpg` がある場合 `call_llm_with_images` で画像を添付する。Step 4 (`ContentRefinementAgent._get_formatting_review`) は PDF ページ画像 `grid_*.png` を `call_llm_with_images` で送信する。
  2. もし `--writer_model_name` や `--reflection_model_name` に **Vision 非対応モデル**を指定した場合、Step 3 で図画像ありだと API が 400 エラーになる可能性がある（Step 4 の `_get_formatting_review` は例外捕捉でスキップされるがレイアウト検査が無効になる）。
  3. **確認すべきこと（Muse 検証時）**: CPA 上で `preview/DeepSeek-V4.1-Flash-1017` が画像入力に対応しているか、非対応なら `--reflection_model_name`（および図あり時の `--writer_model_name`）には `claude-sonnet-5-5` や `gpt-5.6-luna` 等の Vision 対応モデルを推奨する運用とする。
  4. `llm_backend_utils.py` の OpenAI 経路は推論モデル互換のため `temperature` を除去している。これにより `AgentReview` の 3 回アンサンブル（`temperature=0.75`）のばらつきが低下する可能性がある（動作上の不具合ではないが品質面の留意点）。

### 4.3 テストと検証の回し方
- **gpu-01 の Python 環境**: 既定の `python3`（conda base 3.13.2）は `requirements.txt` 依存および `pytest` が未導入。`uv`（`0.9.5`）＋導入済み `Python 3.11.12` でリポジトリ外 `/tmp/po-venv` に仮想環境を作って `compileall` と `pytest` を回す手順を標準とする。
- **Muse 側での実 CPA 疎通テスト**: gpu-01 に実キーを置かない運用のため、実エンドポイント呼び出し確認（`examples/smoke/` を用いた Outline 段のみの実行）は Muse 側で `PAPER_ORCHESTRA_CPA=1 PAPER_ORCHESTRA_CPA_TUNNEL=1 LLM_BACKEND=openai` を設定して実施する。
- **`flake.nix` の評価未確認**: gpu-01 に `nix` が未導入のため、Nix 環境での `nix develop` 実評価は未実施（SPEC C でも任意扱い）。`requirements.txt` 内の `pymupdf4llm` / `json_repair` / `google-genai` は nixpkgs 未収録の可能性があるため、`flake.nix` は `python311` + `uv` + `texlive` を提供する devShell とし、Python パッケージ自体は `uv pip install -r requirements.txt` で入れる設計が安全。

### 4.4 学術 API（S2 / OpenAlex / Crossref）の運用リスク
- **S2 レート制限（HTTP 429）**: `SEMANTIC_SCHOLAR_API_KEY` 未設定／無料枠では 1 req/sec 制限が厳しい。Phase 1a の並列度を 10 → 2 に下げてもタスク数が多いと 429 が発生し得るが、OpenAlex を併用しているため候補ゼロにはなりにくい。
- **要旨の欠損**: OpenAlex は一部出版社論文で `abstract_inverted_index` が `null`、Crossref も要旨欠損が多い。S2 → OpenAlex → Crossref の 3 段補完でも全ソースで要旨が空の論文は現行どおり登録されない（下流の `citation_map.json` が要旨を必須とするため）。
- **既存バッチ関数の軽微な不具合（スコープ外）**: `methods/paper_writer.py:248` (`process_paper_task`) は `write_single_paper` に `reflection_model_name` を渡し忘れている。`paper_writing_cli.py` は `write_single_paper` を直接呼ぶため v1 の CLI 経路には影響しない。
