# CLAUDE.md — reasonable-ux

Loaded automatically into every Claude Code session. Read it fully before touching anything.

## 1. What this is

**reasonable-ux** is a URL-driven UX evaluation tool under active development. Point it at a website, and a vision-based agent (Claude Sonnet by default, pluggable via `LLMAdapter`) drives a real Chromium browser through the site via Playwright, evaluates each page as an inferred persona (scoring CTA clarity, copy quality, and flow smoothness, plus a below-the-fold pass), and produces scored JSON + HTML + PDF reports suitable for handing to a founder. Treat all code as production-grade — no throwaway scripts, no "just for now" hacks.

## 2. Architecture map

**Entry points — read this twice:**
- `run.py` — **the real CLI entry point.** All user-facing flags (`--pages`, `--discover`, `--personas`, `--scout`, multi-page orchestration, PDF stitching) live here.
- `agent_core.py` — the agent loop. `run.py` imports `run` and `_infer_goal_from_url` from it. Usable standalone as a dev stub via `python agent_core.py --url ...` — that's the fastest smoke test, but it bypasses `run.py`'s multi-page / PDF / persona layers.

**Agent core (`agent_core.py`):**
- `LLMAdapter` — normalizes calls across providers via `litellm.acompletion()`. Translates Anthropic message format (with base64 image blocks) to OpenAI format for LiteLLM. Exception: advisor-beta tool calls route directly to Anthropic SDK (`_complete_anthropic_advisor`). Emits Langfuse traces on every LiteLLM call when `LANGFUSE_PUBLIC_KEY` is set (gated at module load). Batches 38.1–39 added `langfuse_otel` callback + `AnthropicInstrumentor`; all 3 direct-SDK paths are now wrapped via `_trace_session()`.
- `screenshot_as_base64` — JPEG quality 40 per-step screenshots.
- `_sanitize_selector` — strips `:contains()` selectors which Playwright doesn't support. Raises if all parts are blocked. **Bypassed by the `nav:` prefix path.**
- `_click_nav_by_label` — Playwright `get_by_role("link", name=…)` with `a:has-text()` fallback. Used for `nav:<Label>` dispatch.
- `_infer_goal_from_url` — URL-path → UX goal string.
- `_make_run_dir` — builds `runs/{domain}/{YYYY-MM-DD_HHMM}_{run_type}/`.
- `_build_prompt` — the UX prompt builder. Persona branch: if `persona is None`, instructs Claude to infer + emit a top-level `persona` field; otherwise threads the known persona into `persona_block`. `nav:<Label>` prefix documented in the `target` schema field.
- `_build_below_fold_prompt` — takes the inferred persona string and builds the below-fold analysis prompt.
- `_run_below_fold_analysis` — writes `full_page.jpeg` at JPEG quality 60, crops in place to `MAX_HEIGHT = 7500` to stay under Claude Vision's 8000px cap, re-reads as base64, calls Claude with the persona-aware prompt.
- `scout_page` — cheap text-only pre-screen using `requests` + BeautifulSoup + `claude-sonnet-4-6` to rate interest 1–5 before spending vision tokens.
- `_build_html_report` — UX HTML table rendering with below-fold embed.
- `run` — **the agent loop.** Requires `url` or raises `ValueError`. Image-stripping from prior messages (cost optimization — only the current step's screenshot stays in context). `adapter.complete(..., step_budget)` token budget — **do not raise this without rewriting the JSON schema**. Persona parse on step 1. Click / navigate dispatch with `nav:` prefix handling. Below-fold call with persona.
- `__main__` — dev stub argparse, `--url` required, supports `--steps`, `--goal`, `--email`, `--password`, `--token-budget`, `--provider`, `--model`, `--advisor`.

**Orchestration & reporting:**
- `run.py` — CLI dispatcher. Single-page → calls `agent_core.run` → `generate_report.build_pdf`. Multi-page (`--pages` / `--discover`) → `run_pages` loops over URLs, stitches via `generate_report.stitch_reports`. Pre-authenticates via its own Playwright session in `_do_auth` (only when `--email` and `--password` are both set), writing a storage_state tempfile (**see backlog**). `--discover` auth crawler at `_auth_crawl`.
- `generate_report.py` — PDF generation. `build_pdf` single-page (HTML → Jinja → Playwright), `stitch_reports` multi-page with executive summary synthesized by Haiku.
- `personas.py` — `generate_personas(url, summary)` that asks Claude to build 3 contextual personas from URL + report summary. Fallback pads from `DEFAULT_PERSONAS` in `report_data.py`.
- `persona_orchestrator.py` — runs persona evaluations in batches of 2 with a 2-second pause to respect rate limits.
- `persona_agent.py` — single-persona evaluator; re-reads the full report through one persona's lens and returns `{score, key_findings, recommendations}`.
- `site_crawler.py` — fast internal-link discovery via `requests` with Playwright fallback. Same-domain only.
- `build_index.py` — regenerates `runs/index.json` for `dashboard.html`. Invoked automatically after every run in `run.py`.
- `migrate_runs.py` — one-off migration from the old flat `reports/` + `screenshots/` layout into the current `runs/{domain}/{ts}_{type}/` structure.
- `dashboard.html` — dark-theme static dashboard, reads `runs/index.json` via fetch.

## 3. Common invocations

```bash
# Single-page smoke test, 4 steps (dev stub — fastest)
python agent_core.py --url https://linear.app --steps 4

# Same smoke test through the real CLI (also produces a PDF)
python run.py --url https://linear.app --steps 4

# Multi-page run with auth
python run.py --url https://app.example.com \
    --pages / /pricing /features /about \
    --email user@example.com --password "$APP_PASSWORD"

# Discover run — crawl site, HEAD-check each path, run agent on each
python run.py --url https://linear.app --discover
```

## 4. Artifact layout

Every run lands under:

```
runs/
  <domain_with_underscores>/
    <YYYY-MM-DD_HHMM>_<run_type>/
      report.json          # per-step agent decisions + scores
      report.html          # dark-theme HTML version of report.json
      report.pdf           # (single-page runs) Playwright-rendered PDF from Jinja template
      screenshots/
        step_1.png
        step_2.png
        ...
      full_page.jpeg       # full-page screenshot, cropped to 7500px
      below_fold.json      # below-fold findings + score adjustments
      console.json         # captured console messages
      network.json         # >=400 responses and >2000ms requests
```

`<run_type>` is `single_page` for direct runs. Multi-page (`--pages` / `--discover`) runs use:

```
runs/
  suite_<YYYYMMDD_HHMMSS>/
    homepage/              # one subfolder per page, structured as above
    pricing/
    ...
    <domain>_<date>_<time>_multi_page.pdf   # stitched unified PDF
```

`runs/index.json` (written by `build_index.py`) backs `dashboard.html`. Everything under `runs/` is gitignored.

## 5. Key conventions

**`nav:<Label>` prefix for nav clicks.** The UX prompt instructs Claude to emit `"target": "nav:Pricing"` for main-navigation links instead of a CSS selector. Both the `click` dispatch and the `navigate`-with-non-URL fallback detect the prefix, strip it, and route through `_click_nav_by_label` — which uses Playwright's `get_by_role("link", name=…)`. This **bypasses `_sanitize_selector`** entirely (a label would otherwise be rejected as an invalid selector). Watch for regression: if Claude starts emitting CSS like `a[href*='#pricing']` for nav links again, the prompt has drifted.

**Persona inference on step 1, threaded through the run.** `run()` declares `persona = None`. On step 1 the prompt (via `_build_prompt` with `persona=None`) asks Claude to infer a plausible evaluator persona from the screenshot + URL + title and return it as a top-level `persona` field. From step 2 onward, `_build_prompt` receives the known persona and tells Claude to stay in character. The same string is passed into `_run_below_fold_analysis` and `_build_below_fold_prompt`. If a new code path needs the persona, use the local variable — don't re-infer.

**`max_tokens` budget in the agent loop.** Current caps: **2048 when advisor is enabled, 1024 otherwise** — `step_budget = 2048 if advisor else 1024`. The JSON schema in `_build_prompt` is deliberately compact to fit inside these budgets with room for a 3–5 friction-point list. **Don't add fields without shrinking existing ones**, and **ask before raising either cap** — cost scales linearly with output tokens.

**Image stripping from conversation history after each step.** Before appending the new step's screenshot message, the loop walks prior user messages and drops any `type: image` blocks. Only the current step's screenshot stays in the conversation. This is the source of the README's "84% token reduction" claim. Don't undo it.

**JPEG quality tiers.** Per-step screenshots are JPEG quality 40. Full-page below-fold screenshots are JPEG quality 60. Tuned against visual fidelity for text-heavy pages — don't bump them without a cost check.

**Langfuse `@observe` must disable input/output capture on agent functions.** `_lf_observe` wraps `langfuse.observe` with `capture_input=False, capture_output=False`. This is **not optional** — by default `@observe` JSON-serializes every argument and return value into the trace. `scout_page` is fine, but `_run_below_fold_analysis(page, ...)` receives a Playwright `Page` object (recursive serialization touches every internal browser/DOM attribute) and `_complete_anthropic_advisor(self, messages, ...)` receives `messages` containing base64-encoded screenshots. Without the capture flags off, the decorator pegs CPU at 100% and consumes 50+ GB of RAM trying to serialize them, hanging the process indefinitely after the agent loop ends. To preserve prompt/response visibility in Langfuse without re-triggering the leak, each decorated function calls `_lf_update_generation(input=..., output=..., model=..., input_tokens=..., output_tokens=...)` *after* its LLM call — passing only safe text/scalar fields. Advisor's `safe_input` strips image content blocks from `messages` before forwarding. Discovered the hard way during Batch 45 verification.

**LiteLLM `metadata.session_id` overrides the outer `propagate_attributes` context.** Wrapping a call site with `with propagate_attributes(session_id=lf_session_id)` is *not* sufficient on its own — if the inner call passes `metadata={"session_id": run_dir}` to `adapter.complete()` / `litellm.acompletion()`, the langfuse_otel callback uses the metadata value as the trace's `session_id`, overriding the propagate_attributes default. For suite runs this fragments traces into per-page sessions (`runs/{domain}/{ts}_single_page`) instead of the shared `suite_session_id`. Inner helpers that take `run_dir` for file-path purposes (`_run_below_fold_analysis`, `scout_page`, `persona_library._enrich`/`save_inferred`) accept a separate `session_id` parameter — use that for `metadata.session_id`, keep `run_dir` for paths only. Discovered Batch 67 after a Langfuse export showed 4 trailing per-page sessions per suite run.

**Langfuse `propagate_attributes` is sync-only.** Use `with propagate_attributes(session_id=...)`, not `async with`. The helper returns `_AgnosticContextManager` from `opentelemetry.util._decorator`, which implements `__enter__`/`__exit__` but not the async equivalents. `async with` raises a runtime error. Inside the `with` block you can still `await` async functions — OTel context propagates through `contextvars` across `await` boundaries.

## 6. Batch history

Session summaries live in `session_summaries/` (gitignored). Read the latest one before starting work. `LATEST.md` is a pointer to the most recent daily file.

- **Batches 1–3** (2026-03-07 → 2026-04-07) — pre-labeled foundational work: initial agent, runs/ folder migration, planner, suite runner, dashboard, CI wiring, token budget cap, model tiering, UX mode, multi-page support, `--discover` flag, persona system, scout mode, multi-provider LLMAdapter. The explicit "batch N" label wasn't used yet; see `git log --before 2026-04-08` for the actual commit trail.
- **Batch 4** (2026-04-08) — persistent browser session for multi-page authenticated runs.
- **Batch 4b–4f** (2026-04-08) — auth login URL detection, selector waits, debug screenshot, two-step login flow, hardcoded DepreciationPro auth sequence, Continue button selector fix (`type=button`), run folder detection for domain subfolder structure.
- **Batch 5** (2026-04-08) — per-page goal inference, scout auth fix, persona auth fix.
- **Batch 6** (2026-04-08) — screenshot embeds in PDF, executive summary, 12-step default for `--pages`, `--page-steps` flag.
- **Batch 7** (2026-04-08) — always-on console + network instrumentation.
- **Batch 8** (2026-04-08) — single-page PDF generation, UX goal inference fix.
- **Batch 9** (2026-04-08) — `--discover` auth fix + Haiku model name fix.
- **Batch 10** (2026-04-08) — two-step login support for `--discover` auth.
- **Batch 11** (2026-04-08) — fix `--pages` with absolute URLs.
- **Batch 12** (2026-04-08) — fix `_auth_crawl` base URL construction.
- **Batch 12b** (2026-04-08) — hard stop on auth failure for `--pages` runs.
- **Batch 13** (2026-04-08) — fix React form auth + exec summary JSON parsing.
- **Batch 13b** (2026-04-08) — fix auth submit (skip Enter fallback if already navigated away).
- **Batch 14** (2026-04-09) — created `personas.py`; dynamic persona generation from URL + report summary.
- **Batch 15** (2026-04-09) — `nav:<Label>` nav-click helper, full-page screenshot crop at 7500px (PIL), dynamic persona inference and threading through `_build_prompt` / `_run_below_fold_analysis`, herokuapp silent-redirect killed, `--url` now required everywhere, `.env.example` populated.
- **Batch 38.1** (2026-04-22) — pin `langfuse>=3.0.0`, switch LiteLLM callback from legacy `success_callback=["langfuse"]` to `litellm.callbacks = ["langfuse_otel"]` for Python 3.14 compat. Added async `_flush_langfuse_spans()` awaited at end of `run()` (atexit ordering breaks on 3.14). `LANGFUSE_OTEL_HOST` env var (EU cloud).
- **Batch 39** (2026-04-22) — close Langfuse blindspots for direct Anthropic SDK calls. Added `opentelemetry-instrumentation-anthropic>=0.60.0`; `AnthropicInstrumentor().instrument()` runs after the `langfuse_otel` callback setup, sharing its active TracerProvider. New `_trace_session(run_dir)` helper wraps each of the 3 blindspots (`_complete_anthropic_advisor`, `_run_below_fold_analysis`, `scout_page`) with Langfuse's `propagate_attributes(session_id=run_dir)` context manager so their spans land under the same session as per-step LiteLLM calls. Hoisted `run_dir` computation to the top of `run()` (pre-scout) and removed duplicates from both scout-skip and full-eval branches; scout now receives `run_dir` via new optional param.
- **Batch 40** (2026-04-22) — auth debug screenshot relocated to `runs/auth_debug_{PID}.png`; CLAUDE.md backlog housekeeping (confirmed `_SKIP_SEGMENTS` and Langfuse wrappers already in place).
- **Batch 41** (2026-04-22) — cross-page friction deduplication: 7-line dedup pass in `stitch_reports` (`generate_report.py`) removes repeated top-finding strings before exec summary synthesis.
- **Batch 42** (2026-04-22) — rename `tests/agent_test.py` → `agent_core.py` at repo root; remove `sys.path.insert` shims; add H5.11 `main-branch-edit-guard` hook.
- **Batch 43** (2026-04-24) — `nav:` drift regression: `_NAV_DRIFT_RE` + `_nav_drift_check()` in `evals/run_evals.py`; `assert_nav_drift: true` on all 7 `saas_landing` eval labels; git-hygiene working rule added.
- **Batch 45** (2026-04-24) — fix Langfuse blindspots: root cause was `AnthropicInstrumentor().instrument()` running at import time before `langfuse_otel` initialized its TracerProvider, so advisor/below-fold/scout spans were silently discarded. Replaced broken OTel approach with langfuse v4 `@observe(as_type="generation", capture_input=False, capture_output=False)` (`@_lf_observe` wrapper) on `scout_page`, `_run_below_fold_analysis`, and `_complete_anthropic_advisor`; each call site uses `with propagate_attributes(session_id=run_dir)` (sync, not `async with`) to group spans under the run session. Removed `_trace_session()`, `AnthropicInstrumentor`, and `opentelemetry-instrumentation-anthropic` from requirements.txt. Two correctness traps surfaced during verification: (1) `propagate_attributes` returns a sync-only `_AgnosticContextManager` — `async with` raises at runtime; (2) `@observe` defaults to capturing all function inputs/outputs as JSON, which recursively serialized the Playwright `Page` object (below-fold) and base64-encoded screenshots inside `messages` (advisor), pegging CPU at 100% and consuming 50+ GB RAM until killed. Both are now documented as section-5 invariants.
- **Batch 58** (2026-04-28) — cost logging for multi-page suite runs: `_log_cost()` in `run.py` writes `cost_summary.json` (input_tokens, output_tokens, total_tokens, model, session_id, langfuse_cost_usd) per suite/run dir and appends to `runs/cost_log.csv`; `_fetch_langfuse_cost()` queries Langfuse traces API post-flush. `suite_session_id` (format `suite_YYYYMMDD_HHMMSS`) now names both the suite folder and the Langfuse session so all pages' spans aggregate under one session. `agent_core.run()` gains a `session_id` param; `lf_session_id` replaces `run_dir` at all 3 Langfuse propagation points (scout, per-step metadata, below-fold).
- **Batch 59** (2026-04-28) — `cost_usd` per-generation in Langfuse via `_calc_cost_usd()` (delegates to `litellm.cost_per_token()`); `_lf_update_generation` gains `cost_usd` param, passed at all 3 call sites. Suite PDF URL divider pages: `stitch_reports()` stamps `isGroupStart`/`groupUrl` on first step of each URL group; `templates/full.html.j2` renders a centered `.page` divider before those steps.
- **Batch 60** (2026-04-28) — CLAUDE.md backlog housekeeping (mark dedup unit test + PDF section breaks as resolved, add batch 59 to history); `build_index.py` reads `cost_summary.json` per run/suite and surfaces `total_tokens`; `dashboard.html` adds Tokens column to both runs and suites tables.
- **Batch 61** (2026-04-28) — drift evaluation: new `drift_report.py` (`load_cost_log`, `check_drift`, `report`, `__main__`); inline `check_drift()` wired into both `_log_cost()` call sites in `run.py`; 6 new tests. Baseline = first chronological run per URL; threshold = `DRIFT_THRESHOLD = 0.20` (uncalibrated placeholder). CSV primary over Langfuse scores.
- **Batch 62** (2026-04-28) — bounded concurrent page execution: `asyncio.Semaphore(2)` in `run_pages()`; `--page-stagger` flag (default 5s); `_run_folder_for()` replaces racy snapshot pair for folder attribution; `_make_run_dir` timestamp `%H%M` → `%H%M%S` to prevent same-domain folder collisions; DECISIONS.md §"Sequential page execution" reversed.
- **Batch 63** (2026-04-28) — `cost_log.csv` schema self-healing: `_log_cost` migrates stale headers on each write; `load_cost_log` uses `int(row.get(...) or 0)` for tolerance; hard `RuntimeError` guard for mixed-schema corruption; 4 new pinning tests.
- **Batch 64** (2026-04-28) — drift threshold A+B: `DRIFT_THRESHOLD` replaced with `DRIFT_THRESHOLDS` dict (`single=0.20`, `multi=0.30`, `""=0.20` fallback); `check_drift` gains required `run_type` param; baseline filter scoped to `(url, run_type)` to eliminate cross-type false positives; `report()` groups by `(url, run_type)`; 2 new tests (scoping + per-type threshold).
- **Batch 65** (2026-04-29) — async cleanup: three escape paths in `agent_core.run()` that could skip `context.close()` and `_flush_langfuse_spans()` are now guarded (screenshot try/except→break, save_inferred try/except, context.close() try/except). `requests.head()` in `run.py:_run_one_page` converted to `asyncio.to_thread()` to unblock the event loop during concurrent page runs.
- **Batch 66** (2026-04-29) — per-step token normalization: `agent_core.run()` returns `step_count = len(report)`; `_log_cost()` writes it to `cost_summary.json` and `cost_log.csv` (self-healing migration handles old rows); `drift_report.py` normalizes drift comparison to tokens/step when both baseline and current rows carry `step_count > 0`, eliminating false positives from variable-length runs. `report()` displays per-step token rate alongside totals. 2 new tests.
- **Batch 67** (2026-04-29) — Langfuse session leak: suite runs were producing trailing per-page sessions named `runs/{domain}/{ts}_single_page` because three inner LLM calls (`_run_below_fold_analysis`, `scout_page`, `persona_library._enrich`) passed `metadata={"session_id": run_dir}` to `adapter.complete()`, which the langfuse_otel callback used to override the outer `propagate_attributes(session_id=lf_session_id)` context. Added `session_id` parameter to all three (alongside the existing `run_dir` for file paths); `metadata={"session_id": session_id or run_dir}` falls back to `run_dir` when no explicit session is passed (single-page runs). Call sites in `agent_core.run()` and `save_inferred()` pass `session_id=lf_session_id`. Verified end-to-end against EU Langfuse: pre-fix smoke run produced suite + 2 trailing per-page sessions; post-fix smoke run produced only the suite session, with all 9 traces (per-step, below-fold, persona enrich) correctly attributed. New CLAUDE.md section-5 invariant documents the metadata-override gotcha.
- **Batch 68** (2026-04-29) — variant comparison portfolio artifact: new `compare_variants.py` aggregates the 4×3 variant suite matrix (v1_baseline / v2_advisor / v3_8step / v4_8step_advisor across stripe/linear/glossier) by joining `cost_log.csv` rows on `langfuse_session_id` with per-page `report.json` score data. Suite→variant mapping is hardcoded by chronological order (no schema migration); page folders are matched to suites by 30-min timestamp window. Outputs tracked `artifacts/variant_comparison.{md,png}` — markdown table + 3-panel matplotlib chart (cost / tokens-per-step / composite score). 10 new tests in `tests/test_compare_variants.py`. Added `matplotlib>=3.8.0` to requirements.txt. Findings: v2_advisor has highest tok/step (12.7k vs 9.4k baseline), v3_8step ran fewer total steps than v1, composite scores cluster tightly across variants (2.79–2.88).
- **Batch 69** (2026-04-29) — dashboard rework + index fix + decisions.html. Three coupled fixes: (1) `runs/index.json` was empty because `build_index.py` looked for `report.json` directly under `runs/<X>/`, but the actual layout is `runs/<domain>/<ts>_single_page/report.json`. Refactored to expose `main(runs_dir)` with dual-path iteration: top-level `report.json` (flat, defensive) OR domain-folder recursion (current). Suite branch dropped the `suite_report.html` requirement (no suite folder contains it); now keys off `cost_summary.json` or `*_multi_*.pdf` glob and points `html_path` at the PDF. (2) `dashboard.html` palette swapped from qagent leftover (`#1a1a2e` / cyan) to reasonequals.com tokens (dark `#0a0a0b`, purple `#c77dff`, system sans). New top-of-page Variants section above the existing tabs renders the 4×3 batch-68 matrix from `runs/variants_index.json`, written by `build_index.py` via `compare_variants.build_rows()`. (3) New `DECISIONS.html` (site tokens, self-contained, recruiter-readable in-browser) auto-rendered by `render_decisions.py` alongside the existing PDF — both produced from the same `parse(DECISIONS.md)` source. New template `templates/decisions_web.html.j2`. New pinning tests `tests/test_build_index.py` (3 tests: nested layout, flat layout, suite-with-PDF). Smoke against real data: 115 runs, 12 suites, 12 variant rows indexed (was 0 / 0 / 0).

## 7. Known backlog

Confirmed open items (cross-check against latest `session_summaries/` and `git log` before starting — this list ages fast):

- **`nav:` prompt drift.** Resolved (Batch 43): `_nav_drift_check()` in `evals/run_evals.py` flags CSS nav selectors as failures; all `saas_landing` labels carry `assert_nav_drift: true`.
- **`run.py` auth debug screenshot.** Resolved (Batch 40): now writes to `runs/auth_debug_{PID}.png` which is gitignored.
- **`--discover` page type filter.** Resolved: `_SKIP_SEGMENTS` in `site_crawler.py:12-15` already covers `about`, `team`, `press`, `careers`, `legal`, `privacy`, `terms`, `jobs`, `blog`, `now`.
- **Cross-page friction point deduplication.** Resolved (Batch 41): 7-line dedup pass in `stitch_reports` (`generate_report.py`) drops exact duplicate friction strings before exec summary synthesis.
- **Langfuse blindspot runtime verification.** Resolved (Batch 45 + Batch 48 verification): `langfuse.decorators.observe()` wrapper on the three direct-SDK paths, confirmed end-to-end via two smoke tests against live Langfuse. Run 1 (`agent_core.py --url https://linear.app --steps 4`) produced LiteLLM step generations + `_run_below_fold_analysis`. Run 2 (`run.py --url https://stripe.com --steps 3 --scout --advisor`) produced `scout_page`, `_complete_anthropic_advisor`, and `_run_below_fold_analysis`. All generations correctly grouped under their respective `session_id` via `propagate_attributes`.
- **Multi-site persona validation.** Resolved (Batch 44): DTC (allbirds.com → "Eco-conscious millennial woman, sustainable footwear") and content/media (substack.com → "Aspiring paid newsletter creator") both produced site-appropriate step-1 personas.
- **Product framing questions flagged in batch 15's audit.** Partially resolved (Batch 46): LICENSE ✓ MIT, TERMS.md ✓ (third-party TOS, data retention, API usage, acceptable use), `.claude/settings.local.json` pruned ✓. Still open (non-code decisions): repo public/private, dependency license cadence audit.
- **Dedup-pass unit test.** Resolved (Batch 53): `_deduplicate_findings` helper extracted to `generate_report.py:230`; `tests/test_dedup_findings.py` added with full coverage.
- **PDF page section breaks.** Resolved (Batch 59): `stitch_reports()` stamps `isGroupStart`/`groupUrl` on the first step of each URL group; `templates/full.html.j2` renders a centered `.page` divider before those steps.
- **Drift evaluation.** Resolved (Batch 61 + Batch 64 + Batch 66): `drift_report.py` reads `cost_log.csv`, groups by `(url, run_type)`, flags runs that exceed per-type threshold vs first-run baseline. Inline check in `run.py` prints a warning after each `_log_cost()` call. CLI: `python drift_report.py`. Thresholds (`DRIFT_THRESHOLDS`) are uncalibrated placeholders (`single=0.20`, `multi=0.30`). Per-step normalization active: when both rows carry `step_count > 0`, drift is measured on tokens/step not raw total.
- **API key rotation.** Pre-public gate flagged in batch-57 session summary — still open.
- **`langfuse_cost_usd` backfill gap.** Resolved as unresolvable: the 3 pre-Batch-58 portfolio suite runs (Stripe, Linear, Vercel) have no matching Langfuse traces — they predate the `suite_session_id` wiring. Backfill was attempted (2026-04-28) and confirmed empty. All runs from Batch 58 onward auto-populate `langfuse_cost_usd` at run time.

## 8. Working rules for this repo

- **Minimum viable diff.** Make the stated fix and nothing else. No refactors, no renames, no "while I'm here" cleanups, no adding type annotations or docstrings to code you didn't change. If the fix is 5 lines, the diff is 5 lines.
- **One commit per completed batch, fresh Claude Code session per batch.** Each batch has a crisp scope. Don't carry context across batches inside a single session. When a batch is done, commit, `/exit`, start fresh.
- **Do not run the full suite to verify.** Smoke test on a single URL (`python agent_core.py --url https://linear.app --steps 4`) is sufficient. Full suite runs burn tokens and rarely surface anything a smoke test doesn't.
- **After each fix, print 2–3 sentences explaining what changed and why.** Not a diff, not a list — a short prose explanation the user can read in 10 seconds to decide whether to commit.
- **If you go off-rails, the user will `/exit`.** Don't spiral. If the first approach fails, diagnose once, try a focused second approach, and if that also fails stop and explain rather than burning more budget.
- **Pre-commit ritual.** Before every commit, append a new entry to `session_summaries/YYYY-MM-DD.md` (gitignored), update `session_summaries/LATEST.md`, show the entry in chat, then commit.
- **Secrets hygiene.** Never hardcode credentials. Never commit `.env`, auth state JSON, or screenshots containing filled login fields. `runs/`, `screenshots/`, `reports/`, and `session_summaries/` are gitignored — keep them that way.
- **Always `git fetch` before reading git state.** Use `git fetch && git log origin/main --oneline` not bare `git log`. Bare `git log` only shows local commits — PRs merged via GitHub UI won't appear until pulled.
- **Regenerate `DECISIONS.pdf` and `DECISIONS.html` whenever `DECISIONS.md` changes.** Both are tracked portfolio artifacts (PDF for offline / editorial register, HTML for in-browser / site-token register — recruiters/founders shouldn't need to install Playwright either way). `python render_decisions.py` produces both from the same `parse()` output. Stage all three (`DECISIONS.md`, `.pdf`, `.html`) together. If you commit DECISIONS.md without regenerating, the public artifacts silently drift from the source.
