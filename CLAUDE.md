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

## 7. Known backlog

Confirmed open items (cross-check against latest `session_summaries/` and `git log` before starting — this list ages fast):

- **`nav:` prompt drift.** Resolved (Batch 43): `_nav_drift_check()` in `evals/run_evals.py` flags CSS nav selectors as failures; all `saas_landing` labels carry `assert_nav_drift: true`.
- **`run.py` auth debug screenshot.** Resolved (Batch 40): now writes to `runs/auth_debug_{PID}.png` which is gitignored.
- **`--discover` page type filter.** Resolved: `_SKIP_SEGMENTS` in `site_crawler.py:12-15` already covers `about`, `team`, `press`, `careers`, `legal`, `privacy`, `terms`, `jobs`, `blog`, `now`.
- **Cross-page friction point deduplication.** Resolved (Batch 41): 7-line dedup pass in `stitch_reports` (`generate_report.py`) drops exact duplicate friction strings before exec summary synthesis.
- **Langfuse blindspot runtime verification.** Partially resolved (Batch 44): all 4 LiteLLM step traces confirmed (14 traces across 4 sessions). `AnthropicInstrumentor` paths NOT confirmed — "below the fold" in traces is organic step output, not `_run_below_fold_analysis` spans; zero distinct below-fold, scout, or advisor traces found across 42 observations. Investigate `_trace_session()` wiring: the `propagate_attributes` context manager may not be attaching spans to the active TracerProvider correctly on Python 3.14.
- **Multi-site persona validation.** Resolved (Batch 44): DTC (allbirds.com → "Eco-conscious millennial woman, sustainable footwear") and content/media (substack.com → "Aspiring paid newsletter creator") both produced site-appropriate step-1 personas.
- **Product framing questions flagged in batch 15's audit.** Repo public/private, LICENSE decision, third-party TOS positioning, customer data retention, dependency license cadence, `.claude/settings.local.json` review.

## 8. Working rules for this repo

- **Minimum viable diff.** Make the stated fix and nothing else. No refactors, no renames, no "while I'm here" cleanups, no adding type annotations or docstrings to code you didn't change. If the fix is 5 lines, the diff is 5 lines.
- **One commit per completed batch, fresh Claude Code session per batch.** Each batch has a crisp scope. Don't carry context across batches inside a single session. When a batch is done, commit, `/exit`, start fresh.
- **Do not run the full suite to verify.** Smoke test on a single URL (`python agent_core.py --url https://linear.app --steps 4`) is sufficient. Full suite runs burn tokens and rarely surface anything a smoke test doesn't.
- **After each fix, print 2–3 sentences explaining what changed and why.** Not a diff, not a list — a short prose explanation the user can read in 10 seconds to decide whether to commit.
- **If you go off-rails, the user will `/exit`.** Don't spiral. If the first approach fails, diagnose once, try a focused second approach, and if that also fails stop and explain rather than burning more budget.
- **Pre-commit ritual.** Before every commit, append a new entry to `session_summaries/YYYY-MM-DD.md` (gitignored), update `session_summaries/LATEST.md`, show the entry in chat, then commit.
- **Secrets hygiene.** Never hardcode credentials. Never commit `.env`, auth state JSON, or screenshots containing filled login fields. `runs/`, `screenshots/`, `reports/`, and `session_summaries/` are gitignored — keep them that way.
- **Always `git fetch` before reading git state.** Use `git fetch && git log origin/main --oneline` not bare `git log`. Bare `git log` only shows local commits — PRs merged via GitHub UI won't appear until pulled.
