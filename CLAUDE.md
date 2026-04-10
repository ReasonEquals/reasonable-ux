# CLAUDE.md — reasonable-ux

Loaded automatically into every Claude Code session. Read it fully before touching anything.

## 1. What this is

**reasonable-ux** is a commercial product under active development — a URL-driven UX evaluation tool. Point it at a website, and a vision-based agent (Claude Sonnet by default, pluggable via `LLMAdapter`) drives a real Chromium browser through the site via Playwright, evaluates each page, and produces scored JSON + HTML + PDF reports suitable for handing to a founder. It runs in one of two modes: **ux** (persona-inferred evaluator scoring CTA clarity, copy quality, and flow smoothness, plus a below-the-fold pass) and **qa** (functional pass/fail agent that verifies pages render and interact correctly). Treat all code as production-grade — no throwaway scripts, no "just for now" hacks.

## 2. Architecture map

**Entry points — read this twice:**
- `run.py` — **the real CLI entry point.** All user-facing flags (`--pages`, `--discover`, `--personas`, `--scout`, `--plan`, multi-page orchestration, PDF stitching) live here. `parse_args()` at line 12, `run_pages()` at line 111, `__main__` at line 263, `--discover` branch at line 296, `--pages` branch at line 399.
- `tests/agent_test.py` — despite the `tests/` path, this is **not a test file** — it is the agent loop itself. `run.py` imports `run` and `_infer_goal_from_url` from it. The path is legacy and has cost real time in past sessions when people assumed "tests/" meant pytest. Also usable standalone as a dev stub via `python tests/agent_test.py --url ...` — that's the fastest smoke test, but it bypasses `run.py`'s multi-page / PDF / persona layers.

**Agent core (`tests/agent_test.py`, 1023 lines):**
- `LLMAdapter` (line 19) — normalizes calls across Anthropic / OpenAI / Google. Translates Anthropic message format (with base64 image blocks) to OpenAI and Google formats.
- `screenshot_as_base64` (line 127) — JPEG quality 40 per-step screenshots.
- `_sanitize_selector` (line 134) — strips `:contains()` selectors which Playwright doesn't support. Raises if all parts are blocked. **Bypassed by the `nav:` prefix path.**
- `_click_nav_by_label` (line 148) — Playwright `get_by_role("link", name=…)` with `a:has-text()` fallback. Used for `nav:<Label>` dispatch.
- `_infer_goal_from_url` (line 166) — URL-path → goal string. Separate dicts for `ux` vs `qa` modes.
- `_make_run_dir` (line 206) — builds `runs/{domain}/{YYYY-MM-DD_HHMM}_{run_type}/`.
- `_build_prompt` (line 218) — **the prompt builder.** UX vs QA branch at line 230. Persona branch at line 231: if `persona is None`, instructs Claude to infer + emit a top-level `persona` field; otherwise threads the known persona into `persona_block` (line 235). `nav:<Label>` prefix documented in the `target` schema field at line 248.
- `_build_below_fold_prompt` (line 287) — takes the inferred persona string and builds the below-fold analysis prompt. Formerly a hardcoded constant.
- `_run_below_fold_analysis` (line 329) — writes `full_page.jpeg` at JPEG quality 60, crops in place to `MAX_HEIGHT = 7500` (line 336) to stay under Claude Vision's 8000px cap, re-reads as base64, calls Claude with the persona-aware prompt.
- `scout_page` (line 372) — cheap text-only pre-screen using `requests` + BeautifulSoup + Haiku to rate interest 1–5 before spending vision tokens.
- `_build_html_report` (line 475) — UX vs QA HTML table rendering. UX branch at line 498, QA at line 587.
- `run` (line 643) — **the agent loop.** Requires `url` or raises `ValueError` (line 645). Scout phase 646–716. Full vision eval at 718. `persona = None` local declared at line 758. Step loop at line 771. Image-stripping from prior messages at lines 794–800 (cost optimization — only the current step's screenshot stays in context). `_build_prompt(..., persona=persona)` call at line 815. `adapter.complete(..., 1024)` token budget at line 820 — **do not raise this without rewriting the JSON schema**. Persona parse on step 1 at lines 849–850. Click dispatch at line 882 (nav prefix branch 884–893, normal branch 895–903). Navigate dispatch at 904 (nav label fallback 906–916, URL path 929). Below-fold call with persona at line 956.
- `__main__` (line 1000) — real argparse, `--url` required, supports `--mode`, `--steps`, `--goal`, `--email`, `--password`, `--token-budget`, `--provider`, `--model`. No herokuapp fallback.

**Orchestration & reporting:**
- `run.py` (506 lines) — CLI dispatcher. Single-page → calls `agent_test.run` → `generate_report.build_pdf`. Multi-page (`--pages` / `--discover`) → `run_pages` loops over URLs, stitches via `generate_report.stitch_reports`. Pre-authenticates via its own Playwright session in `_do_auth` (line 124), writing a storage_state tempfile at line 179 (**see backlog**). `--discover` auth crawler at `_auth_crawl` (line 304). The `/tmp/auth_debug.png` debug dump is at line 172 (**see backlog**).
- `suite_runner.py` (246 lines) — planner-driven multi-test-case runner. Calls `planner.plan(url)` → executes each `suggested_test_case` through `agent_test.run` with `suite_dir` set → writes `suite_report.html`. `--url` is required (line 235).
- `generate_report.py` (884 lines) — ReportLab PDF generation. `build_pdf` (line 580) single-page, `stitch_reports` (line 607) multi-page with executive summary synthesized by Haiku. Palette constants at the top.
- `personas.py` (98 lines) — `DEFAULT_PERSONAS` static list + `generate_personas(url, summary)` that asks Claude to build contextual personas from URL + report summary.
- `persona_orchestrator.py` (40 lines) — runs persona evaluations in batches of 2 with a 2-second pause to respect rate limits.
- `persona_agent.py` (63 lines) — single-persona evaluator; re-reads the full report through one persona's lens and returns `{score, key_findings, recommendations}`.
- `site_crawler.py` (77 lines) — fast internal-link discovery via `requests` with Playwright fallback. Same-domain only.
- `tests/planner.py` (85 lines) — scrapes a page via Playwright and asks Claude to extract testable elements + generate prioritized test cases for `suite_runner`. `--url` required.
- `build_index.py` (99 lines) — regenerates `runs/index.json` for `dashboard.html`. Invoked automatically after every run in `run.py`.
- `migrate_runs.py` (65 lines) — one-off migration from the old flat `reports/` + `screenshots/` layout into the current `runs/{domain}/{ts}_{type}/` structure. Don't need to touch unless the folder layout changes again.
- `tests/test_login.py` (52 lines) — legacy manual playwright test. Not wired into CI.
- `dashboard.html` — dark-theme static dashboard, reads `runs/index.json` via fetch.

## 3. UX vs QA mode distinction

**QA mode** (`--mode qa`, the default) runs the agent as a functional tester: each step emits `pass_fail` + `reasoning`, and failures surface broken selectors, missing elements, or JS errors. **UX mode** (`--mode ux`) runs the agent as an inferred-persona evaluator: each step emits `cta_clarity` / `copy_quality` / `flow_smoothness` scores (1–5), `first_impression`, `friction_points`, `recommendations`, and `confidence`, plus a below-the-fold pass after the loop completes. The branch happens inside `_build_prompt` at `tests/agent_test.py:230` (prompt text) and inside `run` at `tests/agent_test.py:864` (entry shape) — keep the two branches strictly separate.

## 4. Common invocations

```bash
# Single-page smoke test, UX mode, 4 steps (dev stub — fastest)
python tests/agent_test.py --url https://linear.app --mode ux --steps 4

# Same smoke test through the real CLI (also produces a PDF)
python run.py --url https://linear.app --mode ux --steps 4

# Multi-page UX run with auth
python run.py --url https://app.example.com --mode ux \
    --pages / /pricing /features /about \
    --email user@example.com --password "$APP_PASSWORD"

# Full QA suite run (planner → prioritized test cases → agent per case)
python suite_runner.py --url https://linear.app --mode qa --steps 8

# Discover run — crawl site, HEAD-check each path, run agent on each
python run.py --url https://linear.app --mode ux --discover
```

## 5. Artifact layout

Every run lands under:

```
runs/
  <domain_with_underscores>/
    <YYYY-MM-DD_HHMM>_<run_type>/
      report.json          # per-step agent decisions + scores
      report.html          # dark-theme HTML version of report.json
      report.pdf           # (single-page runs) ReportLab PDF
      screenshots/
        step_1.png
        step_2.png
        ...
      full_page.jpeg       # full-page screenshot, cropped to 7500px (UX mode)
      below_fold.json      # below-fold findings + score adjustments (UX mode)
      console.json         # captured console messages
      network.json         # >=400 responses and >2000ms requests
```

`<run_type>` is `single_page` for direct runs and `suite` for `suite_runner.py`. Multi-page (`--pages` / `--discover`) runs use:

```
runs/
  suite_<YYYYMMDD_HHMMSS>/
    homepage/              # one subfolder per page, structured as above
    pricing/
    ...
    <domain>_<date>_<time>_multi_page.pdf   # stitched unified PDF
```

`runs/index.json` (written by `build_index.py`) backs `dashboard.html`. Everything under `runs/` is gitignored.

## 6. Key conventions

**`nav:<Label>` prefix for nav clicks.** The UX prompt instructs Claude to emit `"target": "nav:Pricing"` for main-navigation links instead of a CSS selector. Both the `click` dispatch (`tests/agent_test.py:884`) and the `navigate`-with-non-URL fallback (`tests/agent_test.py:906`) detect the prefix, strip it, and route through `_click_nav_by_label` — which uses Playwright's `get_by_role("link", name=…)`. This **bypasses `_sanitize_selector`** entirely (a label would otherwise be rejected as an invalid selector). Watch for regression: if Claude starts emitting CSS like `a[href*='#pricing']` for nav links again, the prompt has drifted.

**Persona inference on step 1, threaded through the run.** `run()` declares `persona = None` at `tests/agent_test.py:758`. On step 1 the prompt (via `_build_prompt` with `persona=None`) asks Claude to infer a plausible evaluator persona from the screenshot + URL + title and return it as a top-level `persona` field. The parse at line 849 populates the local variable defensively (`decision.get("persona") or "a plausible buyer or user for this product"`). From step 2 onward, `_build_prompt` receives the known persona and tells Claude to stay in character. The same string is passed into `_run_below_fold_analysis(page, run_dir, url, persona)` at line 956 and into `_build_below_fold_prompt(persona)`. If a new code path needs the persona, use the local variable — don't re-infer.

**`max_tokens=1024` budget in the agent loop.** `adapter.complete(conversation, model, 1024)` at line 820. The JSON schema in `_build_prompt` is deliberately compact to fit inside this budget with room for a 3–5 friction-point list. **Don't add fields without shrinking existing ones**, and don't raise the cap without measuring — 1024 is a cost/quality Pareto point.

**Image stripping from conversation history after each step.** Lines 794–800. Before appending the new step's screenshot message, the loop walks prior user messages and drops any `type: image` blocks. Only the current step's screenshot stays in the conversation — prior screenshots are gone. This is the source of the README's "84% token reduction" claim. Don't undo it.

**JPEG quality tiers.** Per-step screenshots are JPEG quality 40 (`screenshot_as_base64`, line 128). Full-page below-fold screenshots are JPEG quality 60 (`_run_below_fold_analysis`, line 333). These were tuned against visual fidelity for text-heavy pages — don't bump them without a cost check.

## 7. Batch history

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

## 8. Known backlog

Confirmed open items (cross-check against latest `session_summaries/` and `git log` before starting — this list ages fast):

- **`nav:` prompt drift.** Watch step JSONs on each smoke test: if Claude starts emitting CSS selectors instead of `nav:<Label>` for main nav links, the UX prompt has drifted and needs an explicit negative example.
- **`run.py` tempfile cleanup — auth state.** `tempfile.NamedTemporaryFile(..., delete=False)` at `run.py:179` writes session cookies + localStorage to `/tmp` and never guarantees cleanup on failure paths. Fix: wrap in `try/finally` so the temp file is removed even if the run crashes.
- **`run.py` tempfile cleanup — auth debug screenshot.** `/tmp/auth_debug.png` at `run.py:172` can contain filled email/password fields. Outside the repo so no commit risk, but should be moved under the per-run dir or deleted after inspection.
- **Git identity decision.** Commits currently use `qareasonably@gmail.com`. Not a secret (commit emails are public), but metadata is permanent — decide before many more commits whether the commercial product wants a dedicated identity.
- **`--discover` page type filter.** Crawler currently follows every same-domain link; pages like `/now`, `/about`, `/team`, `/press`, `/careers` waste tokens without adding UX signal. Add a skip-list or a "page type" scout filter.
- **Cross-page friction point deduplication.** Multi-page runs surface the same friction (e.g. "no pricing above the fold") on every page. The exec summary should dedupe before synthesis.
- **Multi-site persona validation.** Batch 15 validated persona inference on Linear only. Run a small varied suite (SaaS landing, DTC ecommerce, content/media) to confirm step-1 personas stay site-appropriate across categories.
- **Product framing questions flagged in batch 15's audit.** Repo public/private, LICENSE decision, third-party TOS positioning, customer data retention, dependency license cadence, `.claude/settings.local.json` review.

## 9. Working rules for this repo

- **Minimum viable diff.** Make the stated fix and nothing else. No refactors, no renames, no "while I'm here" cleanups, no adding type annotations or docstrings to code you didn't change. If the fix is 5 lines, the diff is 5 lines.
- **Do not touch QA branch when fixing UX branch, and vice versa.** `_build_prompt`, `_build_html_report`, and the `run()` entry shape all branch on `mode`. Keep the branches strictly isolated — a change intended for UX must not land in the QA path.
- **One commit per completed batch, fresh Claude Code session per batch.** Each batch has a crisp scope. Don't carry context across batches inside a single session. When a batch is done, commit, `/exit`, start fresh.
- **Do not run the full suite to verify.** Smoke test on a single URL (`python tests/agent_test.py --url https://linear.app --mode ux --steps 4`) is sufficient. Full suite runs burn tokens and rarely surface anything a smoke test doesn't.
- **After each fix, print 2–3 sentences explaining what changed and why.** Not a diff, not a list — a short prose explanation the user can read in 10 seconds to decide whether to commit.
- **If you go off-rails, the user will `/exit`.** Don't spiral. If the first approach fails, diagnose once, try a focused second approach, and if that also fails stop and explain rather than burning more budget.
- **Pre-commit ritual.** Before every commit, append a new entry to `session_summaries/YYYY-MM-DD.md` (gitignored), update `session_summaries/LATEST.md`, show the entry in chat, then commit. Do NOT include a `Co-Authored-By` trailer on commits.
- **Secrets hygiene.** Never hardcode credentials. Never commit `.env`, auth state JSON, or screenshots containing filled login fields. `runs/`, `screenshots/`, `reports/`, and `session_summaries/` are gitignored — keep them that way.
