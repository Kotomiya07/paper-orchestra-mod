# PaperOrchestra 改造 実施計画（CPA・agy・T3・OpenAlex 対応）

- 対象リポジトリ: `google-research/paper-orchestra`（ベースコミット `d9bae99`）
- 作業場所: gpu-01 `~/agentic-project/paper-orchestra-mod` / ブランチ `mod/cpa-openalex`
- 参照仕様: `SPEC_MOD.md`（A1〜A7・B1〜B3・C・D をすべて含む最新版）
- 役割分担: 指揮（Neon）が本計画を策定 → 実装（Sol）が T1〜T12 を順に実施 → 検証（Muse）が実 CPA / agy / T3 疎通・成果物確認を実施
- 実装方針: t-wada 流の TDD（Red → Green → Refactor）で、まずモック単体テストを書いてから実装し、変更単位ごとにコミットを分けること。**API キーやトークンの実値はコード・テスト・設定例・ログ・コミットに一切含めないこと。**

---

## 1. 実コード・実環境前提検証の結果

### 1.1 LLM 呼び出し箇所の全数一覧（仕様 A3 との突合）

リポジトリ内の全 `.py` ファイルを精査した結果を以下に示す。

| ファイル | 関数・メソッド（行番号） | 現行の呼び出し | 仕様 A3 との突合 | v1 での対応方針 |
|---|---|---|---|---|
| `methods/agents/outline_agent.py` | `OutlineAgent.run()` (L51) | `call_gemini_with_contents`（テキスト Part×5） | 一致 | `call_llm_with_contents` へ置換（T6） |
| `methods/agents/literature_review_agent.py` | `HybridLiteratureAgent._synthesize_content()` (L499) | `call_gemini_with_contents`（テキスト×2 + `system_instruction`） | 一致 | `call_llm_with_contents` へ置換（T6） |
| `methods/agents/literature_review_agent.py` | `HybridLiteratureAgent._discover_candidates()` (L464) | `call_gemini_with_contents`（`gemini-3-flash-preview` 固定 + `google_search_tool`） | 仕様 B2 と一致（A6/A7 拡張が必要） | テキストバックエンドが `"gemini"` 以外（`openai` / `agy` / `t3`）のときは API 探索へ切替、Gemini 時は従来経路を維持（T9） |
| `methods/agents/content_refinement_agent.py` | `ContentRefinementAgent._get_formatting_review()` (L165) | `call_gemini_with_images` | 一致 | 既存の `call_llm_with_images` へ置換（T6） |
| `methods/agents/content_refinement_agent.py` | `ContentRefinementAgent.run()` 本文推敲ループ (L293) | `call_gemini_with_contents`（**PDF バイナリ `application/pdf` 添付** + テキスト） | **食い違い（後述 2.1）** | 既存の `call_llm_with_pdf` へ置換（T6）。これにより `LLM_BACKEND_MULTIMODAL=t3` での PDF パス渡しにも自然に乗る |
| `methods/agents/content_refinement_agent.py` | `ContentRefinementAgent.run()` フォーマット修正ループ (L520) | `call_gemini_with_contents`（テキスト×1） | 一致 | `call_llm_with_contents` へ置換（T6） |
| `methods/agents/section_writing_agent.py` | `SectionWritingAgent.run()` (L201) | `call_llm_with_images` | 一致（確認済み） | A2/A6/A7 のルーティング対応で動作（コード変更不要。空画像時はテキスト側へフォールバックし、PNG MIME は `openai_utils` 側で改善） |
| `utils/content_parsing_utils.py` | `extract_paper_title_from_citation()` (L38) | `call_gemini_with_text_prompt`（`gemini-3-flash-preview` 固定・`model_name` 引数なし） | 一致（＋呼び出し元補完が必要） | `model_name` 引数を追加し `call_llm_with_text_prompt` へ置換。呼び出し元 `autoraters/citation_f1.py:105` から `self.model_name` を渡す（T6） |
| `autoraters/agent_review.py` | `perform_review_agentreview()` (L137, L172), `get_meta_review()` (L200) | `call_gemini_with_text_prompt` **計 3 か所** | **要対応を確認**（本パイプライン Step 4 の `_get_peer_review()` L90 から直接呼ばれるため必須） | 全 3 か所を `call_llm_with_text_prompt` へ置換（T6） |
| `autoraters/sxs_lit_review_quality.py` | `rate_sxs_lit_review()` (L39–67) | `call_llm_with_text_prompt` だが `if "gemini" / elif "gpt"` 分岐あり | **追加発見（後述 2.6）** | CPA / agy / T3 モデル名で無限ループするため分岐を撤去し `call_llm_with_text_prompt` を直接呼ぶ（T6） |
| `autoraters/sxs_paper_quality.py` | `rate_sxs_paper_quality()` (L58–130) | 独自の `"gpt"/"o1"/"o3"` 判定＋`call_openai_models_with_content` / `call_gemini_with_contents` | **追加発見（後述 2.6）** | `resolve_multimodal_backend(model_name)` で分岐（`openai` / `gemini` / `t3` / `agy`→`NotImplementedError`）（T6） |
| `autoraters/citation_f1.py` | `_get_citation_priorities()` (L185) | `call_llm_with_text_prompt` | 既に統一済み | A2/A6/A7 で自動対応（ただし L41 の S2 キー出力削除と L105 の `model_name` 引き渡しを実施：T6） |
| `autoraters/lit_review_quality.py` | `rate_single_paper()` (L77) | `call_llm_with_pdf` | 既に統一済み | A2/A6/A7 で自動対応 |
| `utils/pdf_utils.py` | `extract_reference_from_pdf_text()` (L138) | `call_llm_with_text_prompt` | 既に統一済み | A2/A6/A7 で自動対応 |
| `utils/paper_banana_utils.py` | `retrieve_few_shot_examples` (L378), `generate_figure_caption` (L442, L450), `predict_figure_content` (L526), `style_figure_content` (L565), `critique_and_revise_figure` (L618), `generate_figure_visuals` (L662, L674) | `call_gemini_with_contents` / `call_gemini_with_text_prompt` / `generate_image_with_gemini` | 仕様 A4（作図系） | v1 は画像生成を Gemini 専用で残し、非 Gemini バックエンド（`openai` / `agy` / `t3`）で作図有効時は明示エラーで即停止（T7） |
| `frontend/app.py` | L145, L365（UI 選択肢・CLI 起動引数） | モデルリスト固定 | 仕様「範囲外」 | 変更しない |

### 1.2 文献照合の呼び出し箇所と引用 90% 強制プロンプトの正確な場所

- **文献照合（Enrichment）の呼び出し箇所**
  1. **本パイプライン（変更対象）**: `methods/agents/literature_review_agent.py`
     - import: L29 `from utils.scholar_utils import s2_title_search`
     - ループ呼び出し元: `_execute_tasks_in_parallel()` L204–209（`time.sleep(1.0)` 挟みで逐次呼び出し）
     - 実照合処理: `_enrich_and_register_paper()` **L326–384**（特に L338 の `s2_title_search(cand_title, cand_year, cutoff_date)`、L344–345 の `if not s2_data.get("abstract", ""): return`）
  2. **評価スクリプト（OpenAlex 化の対象外）**: `autoraters/citation_f1.py` L111–140（`ReferenceF1V1Evaluator._fetch_paper_id`）
     - 正解データキャッシュの S2 `paperId` と集合一致を取る評価用途のため OpenAlex ID には差し替えない。
     - ただし **L41 `print(f"Loaded S2 API Key for this project: {S2_API_KEY}")`** に秘密漏洩があるため T6 で削除する。
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
  - 補足: 変数名 `literature_review_agent_writter_prompt`（`writter` の綴り）は既存 import 互換のため変更しない。他のプロンプトファイルに 90% 引用強制の指示が無いことも確認済み。

### 1.3 OpenAlex 追加時に触る関数の境界

| 層 | ファイル | 触る関数・境界 | 責務 |
|---|---|---|---|
| API / 照合 / 探索ユーティリティ | `utils/scholar_utils.py` | モジュール初期化 (L27–28 修正), `is_date_valid()`（抽出）, `reconstruct_openalex_abstract()`（新設）, `strip_jats_tags()`（新設）, `_select_best_title_match()`（新設）, `s2_title_search()`（共通化）, `openalex_title_search()`（新設）, `crossref_title_search()`（新設）, `enrich_paper_metadata()`（新設：S2→OpenAlex→Crossref 優先＆要旨補完）, `s2_search()`（新設）, `openalex_search()`（新設） | 外部学術 API 呼び出し・正規化・日付フィルタ・題名ファジー一致・要旨補完を閉じ込め、ネットワークなし単体テストを可能にする |
| 文献エージェント | `methods/agents/literature_review_agent.py` | `__init__()` (L87), `_execute_tasks_in_parallel()` (L160–210), `_enrich_and_register_paper()` (L326–384), `_discover_candidates()` (L426–475), `_extract_targeted_query()`（新設補助）, `_synthesize_content()` (L477–505) | 非 Gemini バックエンド時の API 探索切替、多情報源照合・情報源ログ出力、`min_cite_paper_count` 削除 |
| 文献プロンプト | `methods/prompts/literature_review_agent.py` | `literature_review_agent_writter_prompt` (L33) | 90% 引用強制の撤廃 |

### 1.4 `agy` / `t3` CLI の実機前提検証結果（gpu-01）

- **`agy` CLI（仕様 A6）**:
  - gpu-01 には `/home/ryo/.local/bin/agy`（version `1.4.12`、Muse VM 実測は `1.5.14`）が存在。
  - `agy --help` で `--model`, `--output-format` (`text|json|stream-json`), `--disable-slash-commands`, `-p` (`--print`), `--dangerously-skip-permissions` の存在を確認済み。
- **`t3` CLI（仕様 A7）**:
  - gpu-01 には `/home/ryo/.local/bin/t3` が存在。
  - `t3 thread start --help`: `USAGE: t3 thread start [flags] <project> <message>`、`--provider`, `--model`, `--json`, `--wait` をサポート。
  - `t3 thread show --help`: `USAGE: t3 thread show [flags] <thread>`、`--messages <int>`, `--json` をサポート。
  - **実測 JSON スキーマ（重要）**:
    - `t3 thread list --project . --json`: フラット配列 `[{"threadId": "...", "status": "running", ...}]`
    - `t3 thread show --json --messages 0 <threadId>`: **ネスト構造** `{"thread": {"threadId": "...", "status": "running", "activeRunId": "...", "runCount": 3, ...}, "messages": []}`
    - `status` 値一覧: `idle`, `preparing`, `queued`, `starting`, `running`, `waiting`, `completed`, `interrupted`, `failed`, `cancelled`, `rolled_back`

---

## 2. 仕様と実コード・実環境の食い違いと扱い

| # | 食い違いの内容 | 扱い（優先判断） | 理由 |
|---|---|---|---|
| 2.1 | 仕様 A3 は `content_refinement_agent.py` の本文推敲 2 か所を A2 の汎用関数（テキスト断片）経由とする想定だが、実コード L293 の呼び出しは `types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")` で **PDF バイナリを添付**している（L520 のフォーマット修正のみテキスト専用）。 | **実コードに合わせ、L293 は既存の `call_llm_with_pdf()` を使用し、L520 のみ `call_llm_with_contents()` を使用する。** | `call_llm_with_pdf()` は Gemini 側で raw PDF bytes、OpenAI 側で `load_paper()` テキスト抽出、さらに A7 の `LLM_BACKEND_MULTIMODAL=t3` では PDF パス渡しへそのまま振り分けられ、設計が一貫する。 |
| 2.2 | 実コード `utils/openai_utils.py:204` (`get_openai_image_part_from_path`) がデータ URI の MIME 型を `data:image/jpeg;base64,...` 固定にしているが、`pdf_to_grid_images()` (`utils/pdf_utils.py:287`) は `.png` を出力する。 | **実コードを修正し、拡張子から MIME 型を判定する**（`.png` → `image/png`、`.jpg`/`.jpeg` → `image/jpeg`、フォールバック `image/png`）。 | CPA 配下の Claude 系モデル等は宣言 MIME 型と実画像バイト列の不一致を HTTP 400 で拒否するため。 |
| 2.3 | 実コード `utils/openai_utils.py:22` に未使用の `import cv2` がある。 | **実コードから削除する。** | ファイル内で未使用であり、OpenCV 未導入環境で `openai_utils` の import が落ちる原因になるため。 |
| 2.4 | 実コード `utils/openai_utils.py:185` (`call_openai_models_with_text_prompt`) は `content=[{"type": "text", "text": prompt}]` の配列形式を送信している。 | **テキスト専用呼び出し（`call_llm_with_contents`、および画像 0 枚時の `call_llm_with_images`）では `content` に単一文字列 `str`（`"\n\n".join(contents)`）を渡す**（`call_openai_models_with_content` は `Union[str, list[Any]]` を受理）。 | OpenAI 互換 API では `content: str` が全モデル（DeepSeek・Claude・GPT 経由の CPA を含む）で最も互換性が高く、複数テキスト配列や連続 `user` メッセージによる 400 エラーを回避できるため。 |
| 2.5 | gpu-01 環境の `httpx` は `0.28.1` であり、旧引数 `httpx.Client(proxies=...)` は廃止されている。 | **仕様 A1 を現行 `httpx` に合わせ、`httpx.Client(proxy=proxy_url)`（単数形 `proxy=`）を使用する。** | `httpx 0.28` では `proxies=` で `TypeError` になる。`proxy=` 明示指定時は環境変数 `HTTP_PROXY`/`HTTPS_PROXY` より明示プロキシが優先されることを実機確認済み。 |
| 2.6 | 仕様 A3 に未記載だが `autoraters/` に実害のある箇所が 3 点ある：<br>(a) `autoraters/citation_f1.py:41` が import 時に `S2_API_KEY` を print している（`scholar_utils.py:28` と同一バグ）、L105 で `extract_paper_title_from_citation` に `model_name` を渡していない。<br>(b) `autoraters/sxs_lit_review_quality.py:39–67` が `if "gemini" / elif "gpt"` 分岐のため CPA / agy / T3 モデル名で無限ループする。<br>(c) `autoraters/sxs_paper_quality.py:58` が独自に `"gpt"/"o1"/"o3"` 判定している。 | **仕様の全体原則（A1 秘密出力禁止、A2/A6/A7 ルーティング統一、A5 任意モデル名）を優先し、T6 で併せて修正する。** | いずれも小規模修正で済み、秘密漏洩や評価実行時の無限ループを防げるため。 |
| 2.7 | `methods/agents/plotting_agent.py:243–248` は figure ごとの例外を握りつぶし、`methods/paper_writer_with_plotting.py:68–265` は例外を 3 回リトライする。また A6/A7 追加に伴い、非 Gemini バックエンドは `openai` だけでなく `agy`・`t3` も含む。 | **仕様 A4（「分かりやすいエラーを出して止めること。黙って失敗しないこと」）を優先し、非 Gemini バックエンド（`openai` / `agy` / `t3`）で作図が有効な場合、CLI 入口・`paper_writer_with_plotting.write_single_paper` リトライ前・`PlottingAgent.run` 先頭・`generate_figure_visuals` の各境界で早期に明示エラーを送出する。** | `generate_image_with_gemini` 内部だけでエラーを出しても握りつぶしや 3 回リトライに呑まれるため。 |
| 2.8 | 仕様 B2 は「`LLM_BACKEND=openai` のときは API 探索に切り替える」と記述しているが、A6（`agy`）・A7（`t3`）でも Gemini の `GoogleSearch` grounding ツールは使えない。 | **`resolve_text_backend(self.model_name) != "gemini"`（すなわち `openai` / `agy` / `t3` のすべて）で API 探索（S2 + OpenAlex）へ切り替える。** | `LLM_BACKEND=agy` や `t3` で Google Search grounding を呼ぶと `GEMINI_API_KEY` 未設定で落ちるため、A6/A7 の追加と整合させる必要がある。 |
| 2.9 | `OutlineAgent` が生成する `citation_hints`（`methods/prompts/outline_agent.py:76`）は `"Vaswani et al. (Attention Is All You Need)"` や `"research paper or technical report introducing 'AdamW optimizer'"` 形式で、生のまま B1 題名検索（`fuzz.ratio > 70`）に渡すと一致率が落ちる。 | **仕様 B2（targeted は B1 題名検索で 1 件狙う）をベースにしつつ、`_extract_targeted_query()` で括弧内タイトルや引用符内名称を抽出して B1 検索チェーン（S2→OpenAlex→Crossref）を試し、全滅時のみ `openalex_search` / `s2_search` の `limit=1` にフォールバックする。** | 仕様の設計意図を実プロンプト出力形式と整合させるため。 |
| 2.10 | `utils/scholar_utils.py:43` の `is_date_valid` は `"YYYY-MM"` 固定で `"YYYY-MM-DD"` や `"YYYY"` を渡すと例外→`True`（除外漏れ）になる一方、`literature_review_agent.py:385` の `_is_paper_allowed` は 1〜3 要素すべてに対応している。 | **`is_date_valid` をモジュールレベルに抽出する際、`YYYY-MM`（従来挙動維持）・`YYYY-MM-DD`・`YYYY` を安全に解析できるよう整備する。** | S2・OpenAlex・Crossref の 3 ソース間で cutoff 判定の一貫性を保つため。 |
| 2.11 | 仕様 A6 では `call_llm_with_images` について「画像付きが `NotImplementedError` になること」としている一方、`SectionWritingAgent.run()` (L201) は図が 0 枚（`images=[]`）のときも `call_llm_with_images` を呼ぶ。 | **`call_llm_with_images` は `images` が非空のときのみマルチモーダル経路（`agy` なら `NotImplementedError`）とし、`images` が空（`[]`）のときはテキスト経路（`call_llm_with_text_prompt`）に委譲する。** | 画像が本当に添付された呼び出しは仕様どおり `NotImplementedError` で止めつつ、図 0 枚のスモーク素材等で `SectionWritingAgent` が `agy` テキスト生成として動作できるようにするため。 |
| 2.12 | 実測した `t3 thread show --json` の出力は `{"thread": {"status": "..."}, "messages": []}` という**ネスト構造**であり、フラットな `{"status": "..."}` ではない。また A7 の混成構成（テキスト CPA＋画像/PDF T3）では CLI の `--writer_model_name`（CPA モデル名）と T3 の `<provider>:<model>` が形式衝突する。 | **(a) `utils/t3_utils.py` はネスト形式（`data["thread"]["status"]`）とフラット形式（`data["status"]`）の両方を解析する。`--messages 0` を付けてポーリング負荷を抑える。<br>(b) 任意環境変数 `LLM_MULTIMODAL_MODEL` をサポートし、`LLM_BACKEND_MULTIMODAL` 設定時かつ `LLM_MULTIMODAL_MODEL` 指定時のみマルチモーダル呼び出しのモデル名を上書きする（未指定時は呼び出し元の `model_name` をそのまま使う）。<br>(c) `.gitignore` に `po_calls/` を追加する。** | (a) 実 T3 サーバーでの確実な完了検知、(b) `paper_writing_cli.py` 経由の混成構成を破綻させずに動かすため、(c) A7 仕様「呼び出しフォルダは監査用に残す」とリポジトリ clean 状態を両立するため。 |
| 2.13 | gpu-01 の既定 `python3`（conda base 3.13.2）には `requirements.txt` 依存と `pytest` が未導入。また `.gitignore` に `.pytest_cache/`・`__pycache__/`・`.venv/`・`po_calls/` が無い。 | **gpu-01 に導入済みの `uv`（`0.9.5`・Python `3.11.12`）でリポジトリ外に検証用仮想環境を作成し、`.gitignore` にも上記パターンを追加する。** | 仕様 C の `python311` 前提と仕様 D の `compileall` / `pytest` 通過・作業ツリー clean を同時に満たすため。 |

---

## 3. 実装タスク一覧（Sol 向け実行手順）

### 依存関係グラフ

```text
T1 (.gitignore & 検証環境)
 ├── T2 (A1: openai_utils CPA/トンネル/秘密保護) ──────────┐
 ├── T3 (A6: agy_utils 新規作成 & test_agy_backend) ───────┼── T5 (A2/A5/A6/A7: llm_backend_utils ルーティング統合)
 ├── T4 (A7: t3_utils 新規作成 & test_t3_backend) ─────────┘        │
 │                                                                   ├── T6 (A3: 直接 Gemini 呼び出し置換 & autoraters 整合) ──┐
 │                                                                   ├── T7 (A4: --use_plotting 明示エラーガード)             │
 │                                                                   └────────────────────────────────────────────────────────┤
 └── T8 (B1/B2: scholar_utils OpenAlex/Crossref 照合・探索) ──────────────────────────────────────────────────────────────────┴── T9 (B1/B2/B3: literature_review_agent & prompt)
T10 (D: examples/smoke 最小素材)  ※独立実行可
T11 (C/A6/A7: .env.example / README_MOD.md / flake.nix)  ※T2〜T10 後
T12 (D: 全体検証・秘密検査・clean 確認)  ※T1〜T11 後
```

> **共通注意（重要）**: 作業ツリーには未追跡ファイル `SPEC_MOD.md` と `BRIEF_NEON.md` が存在する。**絶対に `git add -A` / `git add .` を使わず、毎回対象ファイル名を明示して `git add <file>...` すること。** `SPEC_MOD.md`・`BRIEF_NEON.md`・`.env` はコミットしないこと。

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
     po_calls/
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
- **推奨コミット**: `chore: ignore __pycache__, .pytest_cache, .venv, and po_calls`

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
       - `OPENAI_API_KEY` 未設定時は `tmp_path` に作成した偽 `settings.json` の `env.ANTHROPIC_AUTH_TOKEN` を読むこと（`HOME` を差し替えて検証）。
       - `OPENAI_API_KEY` が設定されている場合は `settings.json` より `OPENAI_API_KEY` が優先されること。
     - トンネルモード（`PAPER_ORCHESTRA_CPA_TUNNEL=1`）：
       - `HTTPS_PROXY=http://proxy.internal:8080` → `http://proxy.internal:3130` に置換された URL で `httpx.Client(proxy=...)` が作られ、`OpenAI(http_client=...)` に渡ること。
       - 認証情報付き `http://user:pass@proxy.internal:8080/` → `http://user:pass@proxy.internal:3130/`、ポート省略 URL `http://proxy.internal` → `http://proxy.internal:3130` になること。
       - `HTTPS_PROXY` 未設定で `PAPER_ORCHESTRA_CPA_TUNNEL=1` の場合は明確な `ValueError` になること。
       - `PAPER_ORCHESTRA_CPA_TUNNEL` 未設定または `0` のときは `http_client` を作らない／渡さないこと。
     - 秘密非出力テスト：ダミーキー（例 `sk-secret-test-value-9999`）を設定し、正常生成・API 例外発生（例外メッセージにキーを含む偽エラー）・キー未設定エラーのいずれでも、`capsys` の stdout/stderr および送出例外文字列にダミーキーが一切含まれないこと。
     - `get_openai_image_part_from_path`: `.png` ファイルに対して `data:image/png;base64,`、`.jpg` ファイルに対して `data:image/jpeg;base64,` となること。
  2. `utils/openai_utils.py` を実装:
     - L22 の未使用 `import cv2` を削除。`import mimetypes`, `from urllib.parse import urlsplit, urlunsplit`, `from typing import Any, Optional, Union`, `import httpx` を追加。
     - 定数: `DEFAULT_CPA_BASE_URL = "http://m4-mac-mini-32gb.tail5df0ca.ts.net:8317/v1"`, `CPA_TUNNEL_PROXY_PORT = 3130`。
     - 補助関数:
       - `_is_truthy_env(name: str) -> bool`: `os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}`
       - `reset_openai_client() -> None`: `_openai_client = None`（ロック保護）
       - `_resolve_openai_base_url() -> Optional[str]`
       - `_read_claude_settings_token(settings_path: Optional[str] = None) -> Optional[str]`（実行時に `os.path.expanduser("~/.claude/settings.json")` を都度評価し、`data.get("env", {}).get("ANTHROPIC_AUTH_TOKEN")` を返す。失敗時は内容を出さず `None`）
       - `_resolve_openai_api_key() -> str`（`OPENAI_API_KEY` 優先 → CPA 時のみ `_read_claude_settings_token()` → 欠損時はキー値を含めない `ValueError`）
       - `_replace_proxy_port(proxy_url: str, port: int = CPA_TUNNEL_PROXY_PORT) -> str`（`urlsplit`/`urlunsplit` で userinfo・IPv6・スキーム省略を安全に処理してポートを `3130` へ置換）
       - `_build_tunnel_http_client() -> Optional[httpx.Client]`（非トンネル時は `None`、トンネル時は `HTTPS_PROXY`/`https_proxy` 必須、欠損時は `ValueError`、`httpx.Client(proxy=tunnel_url)` を返す）
       - `_redact_secret(text: str, secret: Optional[str]) -> str`
     - `get_openai_client()` 更新：`api_key` に加え、非 `None` のときのみ `base_url`・`http_client` を `OpenAI(**kwargs)` に渡す。
     - `call_openai_models_with_content()` 更新：`content: Union[str, list[Any]]` 対応、`generation_configs: Optional[dict] = None`。例外ログと再送出時に `_redact_secret` で API キーを必ずマスクする。
     - `get_openai_image_part_from_path()` 更新：`mimetypes.guess_type(image_path)` で `image/*` 判定（フォールバック `image/png`）。
- **完了判定**: `/tmp/po-venv/bin/python -m pytest tests/test_llm_backend.py -q` が全件 PASS。
- **推奨コミット**: `feat(openai): support CPA mode, Claude settings token fallback, and tunnel proxy`

---

### T3: `agy` バックエンドの実装と単体テスト（SPEC A6: `utils/agy_utils.py`）
- **依存**: T1
- **対象ファイル**: `utils/agy_utils.py`（新規作成）, `tests/test_agy_backend.py`（新規作成）
- **変更内容（TDD: テスト先行）**:
  1. `tests/test_agy_backend.py` を新規作成し、`subprocess.run` をモックして以下を先に書く（ルーティング系テストは T5 で追記する）:
     - 正常系: `{"conversation_id": "c1", "status": "SUCCESS", "response": "hello world \n", "duration_seconds": 1.2, "num_turns": 1, "usage": {"input_tokens": 8500, "output_tokens": 10}}` の JSON 封筒から `"hello world"`（末尾空白 strip 済み）が返ること。
     - 起動引数・安全性検証:
       - コマンドが `[agy_bin, "--model", model_name, "--output-format", "json", "--disable-slash-commands", "-p", prompt]` であること。
       - `--dangerously-skip-permissions` が含まれないこと。
       - `stdin=subprocess.DEVNULL` が指定されていること。
       - `cwd` がリポジトリ外の独立した一時ディレクトリであり、終了後にクリーンアップされること。
     - バイナリ解決順: `AGY_BIN` 環境変数 → `shutil.which("agy")` → `~/.local/bin/agy`。いずれも存在しない場合は明確な `RuntimeError`。
     - 異常系:
       - `status != "SUCCESS"`（例: `"ERROR"` / `"FAILED"`）で `RuntimeError`。
       - `returncode != 0` で `RuntimeError`。
       - stdout が JSON でない出力（例: プレーンテキスト・空文字）で `RuntimeError`。
       - `subprocess.TimeoutExpired` で `RuntimeError`。
       - いずれの失敗でも、エラーメッセージに含まれる stderr が末尾最大 500 文字以内に収まること（1,000 文字の偽 stderr を与えて先頭が落ち末尾 500 文字のみ残ることを検証）。
     - 500KB 上限: `len(prompt.encode("utf-8")) > 500 * 1024` のプロンプトで `subprocess.run` を呼ばずに明確な `ValueError` を送出すること。
  2. `utils/agy_utils.py` を実装:
     - 定数: `MAX_AGY_PROMPT_BYTES = 500 * 1024`, `DEFAULT_AGY_TIMEOUT_SECS = 300`, `MAX_STDERR_TAIL_CHARS = 500`。
     - `resolve_agy_bin() -> str`:
       1. `env_bin = os.environ.get("AGY_BIN", "").strip()` が設定されていればそのパス（存在確認し無ければ `RuntimeError`）
       2. `shutil.which("agy")`
       3. `fallback = os.path.expanduser("~/.local/bin/agy")` が存在すればそれ
       4. 見つからなければ `RuntimeError("agy CLI not found. Set AGY_BIN or install agy in PATH / ~/.local/bin/agy.")`
     - `_format_stderr_tail(stderr: Optional[str]) -> str`: `(stderr or "")[-MAX_STDERR_TAIL_CHARS:]`
     - `call_agy_with_text_prompt(prompt: str, model_name: str, timeout_secs: int = DEFAULT_AGY_TIMEOUT_SECS) -> str`:
       - `if not isinstance(prompt, str): raise TypeError("prompt must be str")`
       - `prompt_bytes = len(prompt.encode("utf-8"))` → `> MAX_AGY_PROMPT_BYTES` なら `raise ValueError(f"Prompt size ({prompt_bytes} bytes) exceeds agy CLI limit ({MAX_AGY_PROMPT_BYTES} bytes).")`
       - `agy_bin = resolve_agy_bin()`
       - `cmd = [agy_bin, "--model", model_name, "--output-format", "json", "--disable-slash-commands", "-p", prompt]`（モデル名のホワイトリストは作らない。`--dangerously-skip-permissions` は絶対に付けない）
       - `with tempfile.TemporaryDirectory(prefix="po_agy_") as tmpdir:` 内で `subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=tmpdir, timeout=timeout_secs)` を実行。
       - `subprocess.TimeoutExpired as exc`: `stderr_tail = _format_stderr_tail(exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr)` → `raise RuntimeError(f"agy timed out after {timeout_secs}s. stderr tail: {stderr_tail}") from exc`
       - `proc.returncode != 0`: `raise RuntimeError(f"agy exited with code {proc.returncode}. stderr tail: {_format_stderr_tail(proc.stderr)}")`
       - `json.loads(proc.stdout)` に失敗 → `raise RuntimeError(f"agy returned non-JSON output. stderr tail: {_format_stderr_tail(proc.stderr)}")`
       - `not isinstance(payload, dict) or payload.get("status") != "SUCCESS" or not isinstance(payload.get("response"), str)`: `raise RuntimeError(f"agy failed with status={payload.get('status') if isinstance(payload, dict) else 'invalid'}. stderr tail: {_format_stderr_tail(proc.stderr)}")`
       - `return payload["response"].strip()`
- **完了判定**: `/tmp/po-venv/bin/python -m pytest tests/test_agy_backend.py -q` が全件 PASS。
- **推奨コミット**: `feat(agy): add agy CLI print-mode text backend utility and unit tests`

---

### T4: `T3` バックエンドの実装と単体テスト（SPEC A7: `utils/t3_utils.py`）
- **依存**: T1
- **対象ファイル**: `utils/t3_utils.py`（新規作成）, `tests/test_t3_backend.py`（新規作成）
- **変更内容（TDD: テスト先行）**:
  1. `tests/test_t3_backend.py` を新規作成し、`subprocess.run` と `time.sleep` をモックして以下を先に書く（`LLM_BACKEND_MULTIMODAL=t3` ルーティングテストは T5 で追記する）:
     - `parse_t3_model_spec()`:
       - `"antigravity-008:gemini-4-neon"` → `("antigravity-008", "gemini-4-neon")`
       - `"acpRegistry_devin:gpt-6-2-sol-medium"` → `("acpRegistry_devin", "gpt-6-2-sol-medium")`
       - `"gemini-4-neon"`（provider 省略）→ `T3_DEFAULT_PROVIDER` 未設定時は `("antigravity-008", "gemini-4-neon")`、設定時（例: `"my-prov"`）は `("my-prov", "gemini-4-neon")`
     - バイナリ解決順: `T3_BIN` → `shutil.which("t3")` → `~/.local/bin/t3`。
     - 正常系（`call_t3_with_contents`）:
       - `T3_PROJECT_DIR`（`tmp_path`）配下に `po_calls/<call_id>/prompt.md`・`materials/`・`out/` が作られ、添付ファイル（画像・PDF のダミーファイル）が `materials/` にコピーされること（原本ファイルが変更されないこと、同名ファイルが別ディレクトリにあっても上書きされないこと）。
       - `t3 thread start <T3_PROJECT_DIR> <固定指示文> --provider <provider> --model <model> --json` が呼ばれ、戻り JSON から `threadId` が取得されること。
       - 状態ポーリング（1 回目 `{"thread": {"status": "running"}}` → 2 回目 `{"thread": {"status": "completed"}}` の実機ネスト形式、およびフラット形式 `{"status": "completed"}` の両方をテスト）で `out/answer.md` が生成された後に内容が strip されて返ること。
       - 完了後も監査用フォルダ `po_calls/<call_id>/` が残っていること。
     - 異常系:
       - `status == "completed"` だが `out/answer.md` が存在しない場合 → `RuntimeError`（`t3 thread show` の `messages` に答えが入っていても**絶対に代用しない**ことを検証！）。
       - `status` が `"failed"` / `"cancelled"` / `"interrupted"` / `"waiting"` の場合 → `RuntimeError`。
       - タイムアウト（`timeout_secs` 超過）→ `RuntimeError`。
       - `T3_PROJECT_DIR` 未設定または不存在 → `ValueError` / `RuntimeError`。
     - 並行直列化: グローバルロック `_t3_call_lock` により 2 スレッド同時呼び出しが重ならず直列実行されること。
  2. `utils/t3_utils.py` を実装:
     - 定数: `DEFAULT_T3_PROVIDER = "antigravity-008"`, `DEFAULT_T3_TIMEOUT_SECS = 900`, `DEFAULT_T3_POLL_INTERVAL_SECS = 10.0`。
     - グローバルロック: `_t3_call_lock = threading.Lock()`。
     - `resolve_t3_bin() -> str`: `T3_BIN` → `shutil.which("t3")` → `~/.local/bin/t3`（無ければ `RuntimeError`）。
     - `parse_t3_model_spec(model_name: str) -> tuple[str, str]`: `":"` を含む場合は `split(":", 1)`、含まない場合は `(os.environ.get("T3_DEFAULT_PROVIDER", DEFAULT_T3_PROVIDER).strip() or DEFAULT_T3_PROVIDER, model_name.strip())`。`model` が空なら `ValueError`。
     - `_extract_thread_status(payload: Any) -> Optional[str]` と `_extract_thread_id(payload: Any) -> Optional[str]`:
       - ネスト構造 `payload["thread"]["status"]` とフラット構造 `payload["status"]` の両対応。
     - `call_t3_with_contents(prompt_text: str, attachments: Optional[Sequence[str]] = None, model_name: str = "gemini-4-neon", timeout_secs: Optional[int] = None, poll_interval_secs: float = DEFAULT_T3_POLL_INTERVAL_SECS) -> str`:
       - `project_dir = os.environ.get("T3_PROJECT_DIR", "").strip()` を検証（未設定・非ディレクトリなら `ValueError`）。
       - `effective_timeout = timeout_secs if timeout_secs is not None else int(os.environ.get("T3_CALL_TIMEOUT", str(DEFAULT_T3_TIMEOUT_SECS)))`
       - `provider, model = parse_t3_model_spec(model_name)`
       - `t3_bin = resolve_t3_bin()`
       - `with _t3_call_lock:` ブロック内で以下を直列実行:
         1. `call_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"`
         2. `call_dir = os.path.join(project_dir, "po_calls", call_id)`、`materials_dir = os.path.join(call_dir, "materials")`、`out_dir = os.path.join(call_dir, "out")` を作成。
         3. `attachments` の各ファイルを存在確認のうえ `materials_dir` へ `shutil.copy2`（ファイル名衝突時は `01_<basename>` 等で回避）。
         4. `prompt.md` を書き出す（添付ファイル一覧 `po_calls/<call_id>/materials/<name>` と `prompt_text` 全文を記載）。
         5. 固定指示文:
            ```python
            instruction = (
                f"po_calls/{call_id}/prompt.md を読み、materials/ のファイルを参照して、"
                f"答えの全文を po_calls/{call_id}/out/answer.md に書くこと。"
                f"他のファイルは変更しないこと。git 操作はしないこと"
            )
            ```
         6. `subprocess.run([t3_bin, "thread", "start", project_dir, instruction, "--provider", provider, "--model", model, "--json"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)` を実行し、`threadId` を取得（失敗時は `RuntimeError`）。
         7. `deadline = time.monotonic() + effective_timeout` まで `poll_interval_secs` 間隔で `subprocess.run([t3_bin, "thread", "show", thread_id, "--messages", "0", "--json"], ...)` をポーリング:
            - `status == "completed"`（または `status == "idle"` かつ `thread.get("activeRunId") is None` かつ `(thread.get("runCount") or 0) >= 1`）→ ループ終了。
            - `status in {"failed", "cancelled", "interrupted", "rolled_back", "waiting"}` → `raise RuntimeError(f"T3 thread {thread_id} ended with status={status!r}.")`
            - `time.monotonic() >= deadline` → `raise RuntimeError(f"T3 thread {thread_id} timed out after {effective_timeout}s.")`
            - `time.sleep(min(poll_interval_secs, max(0.0, deadline - time.monotonic())))`
         8. `answer_path = os.path.join(out_dir, "answer.md")` の存在を確認。無ければ `raise RuntimeError(f"T3 thread {thread_id} finished without creating {answer_path}.")`（スレッドメッセージ本文を代用しない！）。
         9. `answer_path` を UTF-8 で読み `.strip()` を返す（空なら `RuntimeError`）。`call_dir` は削除せず監査用に残す。
- **完了判定**: `/tmp/po-venv/bin/python -m pytest tests/test_t3_backend.py -q` が全件 PASS。
- **推奨コミット**: `feat(t3): add T3 Code thread backend utility and unit tests`

---

### T5: ルーティング統合（`openai` / `agy` / `t3` / `gemini`、`LLM_BACKEND_MULTIMODAL`、`call_llm_with_contents`）（SPEC A2, A5, A6, A7）
- **依存**: T2, T3, T4
- **対象ファイル**: `utils/llm_backend_utils.py`, `tests/test_llm_backend.py`, `tests/test_agy_backend.py`, `tests/test_t3_backend.py`
- **変更内容（TDD: テスト先行）**:
  1. テスト追記:
     - `tests/test_llm_backend.py`:
       - `LLM_BACKEND=openai` のとき、`claude-sonnet-5-5` / `preview/DeepSeek-V4.1-Flash-1017` / `gemini-3.1-pro-preview` すべてが OpenAI 側・`parse_openai_json_results` に回ること。
       - `LLM_BACKEND` 未設定時の後方互換（`gpt`/`o1`/`o3` のみ OpenAI、他は Gemini）。
       - `LLM_BACKEND` 不正値で `ValueError`。
       - `call_llm_with_contents` の OpenAI 変換（`content="\n\n".join(parts)`, `system_prompt`）、Gemini 変換（`types.Part.from_text`）、OpenAI 側での `tools` 指定時 `NotImplementedError`。
       - `call_llm_with_images` で `images=[]` かつ OpenAI 側のとき単一文字列 `prompt` が渡ること。
     - `tests/test_agy_backend.py`:
       - `LLM_BACKEND=agy` で `call_llm_with_text_prompt` とテキストのみの `call_llm_with_contents`（`system_instruction` 付き含む）が `call_agy_with_text_prompt` 側に回り、`parse_gemini_json_results` で JSON 抽出されること。
       - `LLM_BACKEND=agy`（`LLM_BACKEND_MULTIMODAL` 未設定）で `call_llm_with_images(..., images=["fig.png"])`・`call_llm_with_pdf(...)`・`call_llm_with_contents(..., tools=[object()])` がいずれも `NotImplementedError` になること。
       - `LLM_BACKEND=agy` かつ `images=[]` の `call_llm_with_images` はテキスト経路（`agy`）にフォールバックすること。
     - `tests/test_t3_backend.py`:
       - `LLM_BACKEND=t3` で `call_llm_with_text_prompt` / `call_llm_with_contents` が `call_t3_with_contents(prompt_text=..., attachments=[], ...)` に回ること（`tools` 付きは `NotImplementedError`）。
       - 混成構成（`LLM_BACKEND=openai`, `LLM_BACKEND_MULTIMODAL=t3`）：テキスト呼び出しは OpenAI 側、画像付き `call_llm_with_images` および `call_llm_with_pdf` は `call_t3_with_contents(..., attachments=...)` 側に回ること。
       - `LLM_BACKEND_MULTIMODAL=t3` かつ `LLM_MULTIMODAL_MODEL=antigravity-008:gemini-4-neon` 設定時は、そのモデル名が `call_t3_with_contents` に渡ること（未設定時は引数の `model_name` がそのまま渡ること）。
  2. `utils/llm_backend_utils.py` を実装:
     - `VALID_LLM_BACKENDS = {"openai", "gemini", "agy", "t3"}`
     - `resolve_text_backend(model_name: str) -> str`:
       - `LLM_BACKEND` 設定時は検証のうえその値、未設定時は従来の `"gpt"/"o1"/"o3"` → `"openai"` / 他 → `"gemini"`（モデル名ホワイトリストは作らない）。
     - `resolve_multimodal_backend(model_name: str) -> str`:
       - `LLM_BACKEND_MULTIMODAL` 設定時は検証のうえその値、未設定時は `resolve_text_backend(model_name)`。
     - `is_openai_backend(model_name: str) -> bool`: `return resolve_text_backend(model_name) == "openai"`
     - `_resolve_multimodal_model_name(model_name: str) -> str`:
       - `LLM_BACKEND_MULTIMODAL` が非空かつ `LLM_MULTIMODAL_MODEL` が非空なら `LLM_MULTIMODAL_MODEL`、それ以外は `model_name`。
     - `_combine_system_and_user(prompt_text: str, system_instruction: Optional[str]) -> str`:
       - `system_instruction` があれば `f"System Instructions:\n{system_instruction}\n\n{prompt_text}"`、無ければ `prompt_text`。
     - `_wrap_raw_text_response(raw_response: str, parser: Callable, check_parsed_response_not_none: bool) -> Dict[str, Any]`:
       - `parsed_response = parser(raw_response)`、`if check_parsed_response_not_none: assert parsed_response is not None, ...`、`return {"raw_response": raw_response, "parsed_response": parsed_response}`。
     - `get_llm_parser(model_name: str, return_json: bool = True, backend: Optional[str] = None) -> Callable`:
       - `not return_json` → `identity_parse`
       - `(backend or resolve_text_backend(model_name)) == "openai"` → `parse_openai_json_results`
       - それ以外（`gemini`, `agy`, `t3`）→ `parse_gemini_json_results`
     - `call_llm_with_text_prompt()`・`call_llm_with_contents()`・`call_llm_with_images()`・`call_llm_with_pdf()` を 4 バックエンド振り分けに統合する（`call_llm_with_pdf` は `temperature: Optional[float] = 0.7`）。
- **完了判定**: `/tmp/po-venv/bin/python -m pytest tests/test_llm_backend.py tests/test_agy_backend.py tests/test_t3_backend.py -q` が全件 PASS。
- **推奨コミット**: `feat(llm): unify routing for openai/agy/t3/gemini, multimodal split, and call_llm_with_contents`

---

### T6: 直接 Gemini 呼び出しの統一置き換え（SPEC A3 + 関連 autoraters 修正）
- **依存**: T5
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
     - `call_gemini_with_contents` と `from google.genai import types` を削除、`from utils.llm_backend_utils import call_llm_with_contents` に変更。
     - `OutlineAgent.run()` (L38–54) を `call_llm_with_contents(contents=[...4ファイル...], system_instruction=outline_agent_system_prompt.format(cutoff_date=self.cutoff_date), model_name=self.model_name)` に置換。
  2. `methods/agents/literature_review_agent.py`（`_synthesize_content` のみ。探索は T9）:
     - `_synthesize_content()` (L499–504) を `call_llm_with_contents(contents=[user_prompt, json.dumps(context_payload)], model_name=self.model_name, system_instruction=writer_system_prompt, result_parsing_func=parse_gemini_latex_results)` に置換。
  3. `methods/agents/content_refinement_agent.py`:
     - `from google.genai import types`、`call_gemini_with_contents`、`call_gemini_with_images` を削除。`parse_gemini_json_latex_response` のみ `utils.gemini_utils` から import 継続。
     - `from utils.llm_backend_utils import call_llm_with_contents, call_llm_with_images, call_llm_with_pdf` を追加。
     - `_get_formatting_review()` (L165–169): `call_gemini_with_images` → `call_llm_with_images`。
     - `run()` 本文推敲ループ (L288–303): `with open(valid_pdf_for_context, "rb")` を削除し、`call_llm_with_pdf(pdf_path=valid_pdf_for_context, prompt=user_prompt_text, model_name=self.model_name, system_instruction=self.system_prompt, temperature=None, result_parsing_func=parse_gemini_json_latex_response)` へ置換。
     - `run()` フォーマット修正ループ (L520–527): `call_llm_with_contents(contents=[prompt], model_name=self.model_name, result_parsing_func=lambda x: {"raw_response": x}, check_parsed_response_not_none=False)` へ置換。L497 ログを `"Calling LLM to fix..."` に更新。
  4. `methods/agents/section_writing_agent.py`: L201 が既に `call_llm_with_images` 経由であることを確認（変更なし）。
  5. `utils/content_parsing_utils.py`:
     - `call_gemini_with_text_prompt` → `call_llm_with_text_prompt`。
     - `extract_paper_title_from_citation(citation_text: str, model_name: str = "gemini-3-flash-preview") -> str` に引数追加、`return_json=False, check_parsed_response_not_none=False` で呼び出す。
  6. `autoraters/agent_review.py`:
     - L19, L137, L172, L200 の全 3 か所を `call_llm_with_text_prompt` に置換。
  7. `autoraters/citation_f1.py`:
     - **L41 `print(f"Loaded S2 API Key for this project: {S2_API_KEY}")` を削除**。
     - L105 `delayed(extract_paper_title_from_citation)(ref, model_name=self.model_name)` に更新。
  8. `autoraters/sxs_lit_review_quality.py`:
     - L39–67 の `if "gemini" / elif "gpt"` 分岐を撤去し `call_llm_with_text_prompt` を直接呼ぶ。
  9. `autoraters/sxs_paper_quality.py`:
     - L58–130 を `resolve_multimodal_backend(model_name)` 分岐に変更（`"openai"` / `"gemini"` / `"t3"` は `call_t3_with_contents` に `attachments=[paper1_path, paper2_path]` を渡す / `"agy"` は `NotImplementedError`）。
  10. `tests/test_llm_backend.py` に、`OutlineAgent.run`・`extract_paper_title_from_citation`・`perform_review_agentreview`・`rate_sxs_lit_review` が `LLM_BACKEND=openai` および `LLM_BACKEND=agy` 下で Gemini クライアントを呼ばず期待バックエンドを通るモックテストを追加。
- **完了判定**:
  - `rg -n "call_gemini_" methods/ autoraters/ utils/content_parsing_utils.py` の出力が `methods/agents/literature_review_agent.py`（`_discover_candidates` の Gemini 分岐のみ）と `autoraters/sxs_paper_quality.py`（Gemini 分岐のみ）に限られること。
  - `/tmp/po-venv/bin/python -m pytest tests/test_llm_backend.py -q` が全件 PASS。
- **推奨コミット**: `refactor(agents): route LLM calls through unified llm_backend_utils`

---

### T7: 作図モードの非 Gemini バックエンド明示エラーガード（SPEC A4）
- **依存**: T5
- **対象ファイル**: `paper_writing_cli.py`, `methods/paper_writer_with_plotting.py`, `methods/agents/plotting_agent.py`, `utils/paper_banana_utils.py`, `tests/test_llm_backend.py`
- **変更内容（TDD: テスト先行）**:
  1. ガード条件: `resolve_text_backend(image_model_name) != "gemini"`（すなわち `LLM_BACKEND` が `openai` / `agy` / `t3`、または画像モデル名が OpenAI 系）のとき、Gemini 専用の画像生成作図（`gemini-3-pro-image-preview`）は未対応として即エラー停止する。
  2. エラーメッセージ（統一）:
     ```text
     Plotting (--use_plotting true) is not supported with LLM_BACKEND={backend} because diagram image generation requires Gemini (gemini-3-pro-image-preview). Run with --use_plotting false or switch to the Gemini backend.
     ```
  3. ガード配置箇所（握りつぶしと 3 回リトライを確実に防ぐため 4 境界に配置）:
     - `paper_writing_cli.py`: `if args.use_plotting:` 直後、`write_single_paper` 呼び出し前にチェックし `print(f"Error: {msg}", file=sys.stderr)` → `sys.exit(1)`。
     - `methods/paper_writer_with_plotting.py`: `write_single_paper()` の `while` リトライループ前で `raise NotImplementedError(msg)`。
     - `methods/agents/plotting_agent.py`: `PlottingAgent.run()` 先頭で `raise NotImplementedError(msg)`。
     - `utils/paper_banana_utils.py`: `generate_figure_visuals()` の `else:`（diagram 分岐）先頭で `raise NotImplementedError(msg)`。
  4. `tests/test_llm_backend.py` に、`LLM_BACKEND=openai` および `agy` / `t3` で即座に `NotImplementedError` / `SystemExit(1)` になるテストを追加。
- **完了判定**: `/tmp/po-venv/bin/python -m pytest tests/test_llm_backend.py -q` が全件 PASS。
- **推奨コミット**: `feat(plotting): fail fast with clear error when plotting is enabled under non-Gemini backends`

---

### T8: `utils/scholar_utils.py` の OpenAlex / Crossref 照合・API 探索・優先補完実装（SPEC B1, B2）
- **依存**: T1
- **対象ファイル**: `utils/scholar_utils.py`, `tests/test_scholar_utils.py`（新規作成）
- **変更内容（TDD: テスト先行）**:
  1. `tests/test_scholar_utils.py` を新規作成し、ネットワークなし（`requests.get` モック）で以下を検証:
     - S2 キー非出力: `scholar_utils` import / reload 時に `SEMANTIC_SCHOLAR_API_KEY` が stdout/stderr に出ないこと。
     - OpenAlex 要旨復元 (`reconstruct_openalex_abstract`): 位置順復元、`None`/`{}` → `""`。
     - OpenAlex 題名検索 (`openalex_title_search`): モック応答を `s2_title_search` 同一辞書形へ変換、`OPENALEX_EMAIL` 設定時のみ `mailto` パラメータ付与、`fuzz.ratio` + 年一致 `+10` + 閾値 `> 70`、`cutoff_date` 除外。
     - Crossref 題名検索 (`crossref_title_search`): 同一辞書形変換、JATS タグ除去、要旨欠損時 `""`、`cutoff_date` 除外。
     - 照合優先順位と要旨補完 (`enrich_paper_metadata`): S2 要旨あり即決 / **S2 要旨空 → OpenAlex で要旨のみ補完（メタデータは S2 保持）** / S2 不一致 → OpenAlex 一致 / Crossref フォールバック / 全不一致 `None`。
     - API 探索 (`s2_search`, `openalex_search`): `[{"title", "year", "reason"}, ...]` 返却、`cutoff_date` のみ除外、トップ会議／高被引用フィルタなし。
  2. `utils/scholar_utils.py` を実装:
     - **L28 `print(f"Using S2 API Key: {S2_API_KEY}")` を削除**。`_get_s2_headers()` / `_get_openalex_email()` で毎回 `os.environ` を参照。
     - `is_date_valid()`, `reconstruct_openalex_abstract()`, `strip_jats_tags()`, `_select_best_title_match()` を実装。
     - `s2_title_search()`, `openalex_title_search()`, `crossref_title_search()`, `enrich_paper_metadata()`, `s2_search(query, limit=10, cutoff_date=None)`, `openalex_search(query, limit=10, cutoff_date=None)` を実装。
     - `enrich_paper_metadata` は `_source` と `_abstract_source` を辞書に付与して返す。
- **完了判定**: `/tmp/po-venv/bin/python -m pytest tests/test_scholar_utils.py -q` が全件 PASS。
- **推奨コミット**: `feat(scholar): add OpenAlex/Crossref title search, API discovery, and abstract backfill`

---

### T9: 文献エージェントの多情報源照合・API 探索切替・引用 90% 強制廃止（SPEC B1, B2, B3）
- **依存**: T5, T6, T8
- **対象ファイル**: `methods/agents/literature_review_agent.py`, `methods/prompts/literature_review_agent.py`, `tests/test_scholar_utils.py`（または `tests/test_literature_review_agent.py` 新設）
- **変更内容（TDD: テスト先行）**:
  1. テスト追加（モック使用・ネットワークなし）:
     - `_enrich_and_register_paper`: S2 要旨空→OpenAlex 補完で登録され、一致情報源名が出力され API キーが含まれないこと。全ソース要旨空なら登録されないこと。
     - `_discover_candidates`: `LLM_BACKEND` が `openai` / `agy` / `t3` いずれの非 Gemini モードでも API 探索へ切り替わり、`exploration` は `s2_search` + `openalex_search` 併用＋重複除去、`targeted` はクエリ抽出＋B1 題名検索で 1 件狙い（全滅時フォールバック最大 1 件）になること。
     - `_synthesize_content` & プロンプト: `min_cite_paper_count` や `90%` 強制が存在せず、`paper_count` と `cutoff_date` のみでフォーマットできること。
  2. `methods/prompts/literature_review_agent.py` (L33):
     - `- You MUST cite at least {min_cite_paper_count} of them across the introduction and related work sections.` を「実際に使った論文だけを引用し、関連の薄い引用をしない」指示（英語: `- Cite ONLY papers from 'collected_papers' that are genuinely relevant and actually used to support your arguments; do NOT pad citations or cite weakly related papers.`）へ置換。
  3. `methods/agents/literature_review_agent.py`:
     - `__init__` (L87): `self.google_search_tool = None`（Gemini 分岐で遅延初期化）。
     - `_execute_tasks_in_parallel()`: `use_api_search = resolve_text_backend(self.model_name) != "gemini"`。API 探索時は `max_workers=2` に抑えて S2 レート制限 429 を緩和し、ログも `"Phase 1a: API Candidates Discovery (S2 + OpenAlex)..."` / `"Phase 1b: Paper Metadata Enrichment (S2 -> OpenAlex -> Crossref)..."` に更新。
     - `_extract_targeted_query(focus)` を追加（`"Author et al. (Exact Title)"` の括弧内や `"introducing 'Name'"` の引用符内を優先抽出）。
     - `_discover_candidates()`: `resolve_text_backend(self.model_name) != "gemini"` で API 探索分岐（`targeted` は B1 チェーン 1 件狙い→全滅時 `openalex_search`/`s2_search` `limit=1`、`exploration` は `s2_search(limit=8)` + `openalex_search(limit=8)` 併用・重複除去・トップ会議/高被引用フィルタなし）。Gemini モードは従来維持。
     - `_enrich_and_register_paper()`: `enrich_paper_metadata` 呼び出し、要旨補完後に `_is_paper_allowed` と要旨空チェック、`year` の `int` 防御、一致情報源（`[src]` または `[src, abstract: abs_src]`）を `self._safe_print`（キー非出力）、例外の無言 `pass` を警告出力へ改善。
     - `_synthesize_content()` (L494): `min_cite_paper_count=int(len(papers) * 0.9),` を削除。
- **完了判定**: `/tmp/po-venv/bin/python -m pytest tests/ -q` が全件 PASS。
- **推奨コミット**: `feat(lit-review): integrate multi-source enrichment, API discovery, and drop 90% citation mandate`

---

### T10: スモークテスト用最小素材の作成（SPEC D）
- **依存**: なし
- **対象ファイル**: `examples/smoke/idea_sparse.md`, `examples/smoke/experimental_log.md`, `examples/smoke/figures/info.json`
- **変更内容**:
  - `idea_sparse.md`: 短編架空研究アイデア（英語 20〜35 行）。
  - `experimental_log.md`: 最小実験設定と 1 テーブル（3〜4 行）。
  - `figures/info.json`: `[]\n`（必ず JSON 配列 `[]` にすること）。
- **完了判定**: 3 ファイルが存在し、`examples/smoke/figures/info.json` が有効な JSON リストであること。
- **推奨コミット**: `chore(examples): add minimal smoke test materials`

---

### T11: `.env.example`・`README_MOD.md`・`flake.nix` の作成（SPEC C, A6, A7）
- **依存**: T2〜T10
- **対象ファイル**: `.env.example`, `README_MOD.md`, `flake.nix`
- **変更内容**:
  1. `.env.example`（**実キー・実トークンは一切書かない**）:
     - **CPA / OpenAI 互換**: `LLM_BACKEND=openai`（選択肢コメント: `openai | agy | t3 | gemini`）、`PAPER_ORCHESTRA_CPA=1`、`PAPER_ORCHESTRA_CPA_TUNNEL=0`（VM 時は `1`）、`OPENAI_BASE_URL=http://m4-mac-mini-32gb.tail5df0ca.ts.net:8317/v1`、`OPENAI_API_KEY=`（CPA 時は `~/.claude/settings.json` の `env.ANTHROPIC_AUTH_TOKEN` フォールバックあり）。モデル指定例: `preview/DeepSeek-V4.1-Flash-1017`, `gpt-5.6-luna`, `claude-sonnet-5-5`。
     - **agy バックエンド（A6）**: `LLM_BACKEND=agy` のコメント例、`AGY_BIN=`（任意。未設定時は `PATH` → `~/.local/bin/agy`）。使えるモデル例: `gemini-3.8-flash-high`, `gemini-3.8-flash-medium`, `gemini-3.8-flash-low`, `claude-sonnet-4-6`, `claude-opus-4-6-thinking`, `gpt-oss-120b-medium`。
     - **T3 バックエンド（A7）**: `LLM_BACKEND_MULTIMODAL=`（例: `t3`。テキスト CPA＋画像/PDF T3 混成用）、`LLM_MULTIMODAL_MODEL=`（任意。混成時にマルチモーダル側へ渡す `provider:model` 例: `antigravity-008:gemini-4-neon`）、`T3_PROJECT_DIR=`（登録済み T3 プロジェクトディレクトリ）、`T3_BIN=`（任意）、`T3_DEFAULT_PROVIDER=antigravity-008`、`T3_CALL_TIMEOUT=900`。
     - **文献 API**: `OPENALEX_EMAIL=`、`SEMANTIC_SCHOLAR_API_KEY=`。
     - **既存 Gemini 互換（任意・コメントアウト）**: `GEMINI_API_KEY`, `VERTEX_AI_PROJECT`, `VERTEX_AI_LOCATION`。
  2. `README_MOD.md`（SPEC C・A6・A7 の全項目を網羅）:
     - 改造点一覧（CPA/OpenAI 互換統一、agy バックエンド、T3 バックエンド＆マルチモーダル振り分け、OpenAlex/Crossref 照合＆要旨補完、API 探索切替、引用 90% 強制撤廃、S2 キー出力修正、作図ガード）。
     - **CPA での動かし方**: tailnet 内（gpu-01）の直接実行と、Muse VM でのトンネル実行（`PAPER_ORCHESTRA_CPA_TUNNEL=1` + `HTTPS_PROXY` ポート 3130 置換）。
     - **`agy` バックエンドでの動かし方（A6）**: `LLM_BACKEND=agy`、`AGY_BIN`、API キー不要（`agy` サインイン利用）、使えるモデル例（`gemini-3.8-flash-medium` 等）、**1 呼び出しあたり約 8,500 トークンの固定入力 overhead がある点**、500KB プロンプト上限、画像・PDF・tools 非対応（`NotImplementedError`）の注意。
     - **`T3` バックエンドでの動かし方（A7）**: `T3_PROJECT_DIR`, `T3_BIN`, `T3_DEFAULT_PROVIDER`, `T3_CALL_TIMEOUT`, `LLM_BACKEND_MULTIMODAL`, `LLM_MULTIMODAL_MODEL`、`<provider>:<model>` 形式（例: `antigravity-008:gemini-4-neon`, `acpRegistry_devin:gpt-6-2-sol-medium`）、推奨の混成構成（テキスト CPA＋画像/PDF T3）、**呼び出しは分単位かかり Google 共有枠を消費する注意**、T3 サーバー稼働マシン（gpu-01 等）上でのみ利用可能という制約、`po_calls/<call_id>/` の監査フォルダ仕様と `answer.md` 契約。
     - OpenAlex / Crossref の位置づけ（S2 → OpenAlex → Crossref 優先順位、要旨補完、非 Gemini 時の API 探索、`OPENALEX_EMAIL`）。
     - 作図の制約（v1 は `--use_plotting false`、非 Gemini で `--use_plotting true` 指定時は明示エラー停止）。
     - 単体テストとスモークテスト手順（gpu-01 での `pytest` / `compileall`、および Muse 側での Outline CPA スモークテスト・Muse VM での `agy` 疎通・gpu-01 での `t3` 疎通）。
  3. `flake.nix`:
     - `nixpkgs`（`nixos-24.11`）で `python311`, `uv`, 基本ネイティブライブラリ, `pdflatex` / `bibtex` と CVPR/ICLR テンプレート必須パッケージを含む `texlive.combine` を提供する `devShells.<system>.default`。gpu-01 には `nix` が無いためファイル作成と構文目視確認までで完了。
- **完了判定**: 3 ファイルが存在し、`.env` 本体が未作成、秘密値混入なし、SPEC C・A6・A7 の記載要件をすべて満たすこと。
- **推奨コミット**: `docs(env): add .env.example, README_MOD.md, and flake.nix`

---

### T12: 全体検証・秘密検査・最終クリーン確認（SPEC D 最終ゲート）
- **依存**: T1〜T11
- **検証コマンド**:
  ```bash
  /tmp/po-venv/bin/python -m compileall .
  /tmp/po-venv/bin/python -m pytest tests/ -q
  git status
  git log --oneline d9bae99..HEAD
  ```
- **完了判定（すべて満たすこと）**:
  1. `compileall` がエラー 0 件で終了コード 0 になること。
  2. `pytest tests/ -q` が全テスト（`test_llm_backend.py`, `test_agy_backend.py`, `test_t3_backend.py`, `test_scholar_utils.py` 等）PASS（0 failed, 0 errors）で、外部ネットワーク・実 `agy`・実 `t3` 起動不要であること。
  3. `.env` ファイルが存在しないこと。
  4. `git diff d9bae99..HEAD` に API キー・トークン・実メールアドレスが含まれないこと。
  5. `git status` で変更・ステージ済み差分がゼロであり、未追跡ファイルは `SPEC_MOD.md` と `BRIEF_NEON.md` 以外に残っていないこと。
  6. 全コミットが `mod/cpa-openalex` ブランチ上に変更単位ごとに積まれていること。

---

## 4. リスクと未確定事項

### 4.1 作図の扱い（SPEC A4）
- v1 では `utils/paper_banana_utils.py` のテキスト系移植は行わず、非 Gemini バックエンド（`openai` / `agy` / `t3`）で `--use_plotting true` が有効な場合は入口 4 境界で明示エラー停止する。将来 plot（matplotlib 生成）のみ切り出す拡張余地はある。

### 4.2 CPA モデル名と Vision 対応（SPEC A5）
- CPA 23 件に Gemini 系・`devin/swe-2` は無く、実測済みは `preview/DeepSeek-V4.1-Flash-1017`（候補例 `gpt-5.6-luna`, `claude-sonnet-5-5`）。
- Vision 非対応モデルを `--writer_model_name` / `--reflection_model_name` に指定した場合、Step 3（図画像あり時）は 400 エラー、Step 4（フォーマット画像レビュー）は例外捕捉でスキップされるリスクがある。A7 の `LLM_BACKEND_MULTIMODAL=t3`（+ `LLM_MULTIMODAL_MODEL`）による混成構成、または Vision 対応 CPA モデルの指定で回避する。
- OpenAI 経路は推論モデル互換のため `temperature` を除去しているため、`AgentReview` 3 回アンサンブルの多様性が低下する可能性がある。

### 4.3 `agy` バックエンドのリスクと制約（SPEC A6）
- **固定入力 overhead**: 短いプロンプトでも 1 呼び出しあたり約 8,500 トークンの固定入力コストが発生する（エージェント側のシステム文脈）。多段・反復呼び出し（特に `AgentReview` の 3 回アンサンブル等）では消費トークンが大きくなる。
- **500KB 上限・argv 制限**: `argv` 経由で `-p <prompt>` を渡すため、500KB を超える巨大プロンプト（大量論文の要旨を詰め込んだ `_synthesize_content` 等）は `ValueError` で停止する。また、もしプロンプト先頭が `-` で始まる場合の CLI オプション誤認有無は Muse VM（agy 1.5.14）での実測確認対象とする。
- **画像・PDF・tools 非対応**: Step 4（`ContentRefinementAgent`）は PDF・ページ画像を使うため、`LLM_BACKEND=agy` 単体では Step 4 で `NotImplementedError` となる。全段を通す場合は `LLM_BACKEND_MULTIMODAL=openai` または `t3` と併用する必要がある。
- **バージョン差**: gpu-01 は `agy 1.4.12`、Muse VM は `agy 1.5.14`。実呼び出し確認は仕様どおり Muse VM 上で行う。

### 4.4 `T3` バックエンドのリスクと制約（SPEC A7）
- **実行時間と共有枠**: 1 呼び出しが分単位かかり、Google 共有枠も消費する。全段を `LLM_BACKEND=t3` にするのは非現実的であり、`LLM_BACKEND=openai`（または `agy`）＋ `LLM_BACKEND_MULTIMODAL=t3` の混成運用を基本とする。
- **実行マシン制約**: `t3` CLI は T3 サーバー稼働マシン（gpu-01 等）のローカル実行が前提であり、Muse VM からは直接呼べない。
- **混成時のモデル名形式差**: CPA モデル名（例 `preview/DeepSeek-V4.1-Flash-1017`）と T3 モデル指定（`<provider>:<model>`、例 `antigravity-008:gemini-4-neon`）は別体系のため、混成時は `LLM_MULTIMODAL_MODEL` の指定が必要になる（未指定だと CLI の `--writer_model_name` 等がそのまま T3 に渡ってエラーになる点に注意）。
- **モデル slug の実在性**: 実機 `t3 thread list` で確認した Neon の実 slug は `gemini-pro-orchestrator-neon` であり、仕様 A7 の例示 `antigravity-008:gemini-4-neon` が T3 側でエイリアス解決されるか実 slug 指定が必要かは Muse 側での gpu-01 実疎通テストで確認する（コード側はホワイトリストを持たずそのまま渡す）。
- **完了ステータスの挙動**: `t3 thread show --json` のネスト構造 `{"thread": {"status": ...}}` に対応済みだが、完了時の最終 `status` が `"completed"` になるか `"idle"` に戻るかは Muse 側実疎通でも確認する（両方対応済み）。

### 4.5 テストの回し方と環境ギャップ
- gpu-01 の既定 `python3`（3.13.2）は依存パッケージ・`pytest` 未導入。`uv venv --python 3.11 /tmp/po-venv` を使い、全単体テストはネットワーク・実 CLI 起動なしのモックで完結させる。
- 実疎通確認はすべて Muse 側で分担する：CPA（Muse 側・`examples/smoke/` Outline 段）、`agy`（Muse VM）、`T3`（gpu-01・小プロンプト＋画像 1 枚、`answer.md` 契約確認まで）。
- `flake.nix` は gpu-01 に `nix` が無いため実評価未実施。

### 4.6 学術 API（S2 / OpenAlex / Crossref）の運用リスク
- S2 の 1 req/sec レート制限（429）に対し API 探索並列度を 2 に抑制。OpenAlex 併用で候補枯渇リスクを低減。
- 全 3 ソースで要旨が欠損した論文は現行仕様どおり登録されない（`citation_map.json` が要旨を必須とするため）。
- 既存 `methods/paper_writer.py:248` (`process_paper_task`) の `reflection_model_name` 渡し忘れは CLI 経路外の既存バグ（v1 スコープ外）。
