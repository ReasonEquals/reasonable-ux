# CLAUDE.md — reasonable-ux

Loaded automatically into every Claude Code session. Read it fully before touching anything.

## 1. What this is

**reasonable-ux** is a URL-driven UX evaluation tool under active development. Point it at a website, and a vision-based agent (Claude Sonnet by default, pluggable via `LLMAdapter`) drives a real Chromium browser through the site via Playwright, evaluates each page as an inferred persona (scoring CTA clarity, copy quality, and flow smoothness, plus a below-the-fold pass), and produces scored JSON + HTML + PDF reports suitable for handing to a founder. Treat all code as production-grade — no throwaway scripts, no "just for now" hacks.

## 2. Architecture map

**Entry points — read this twice:**
- `run.py` — **the real CLI entry point.** All user-facing flags (`--pages`, `--discover`, `--personas`, `--scout`, multi-page orchestration, PDF stitching) live here.
- `agent_core.py` — the agent loop. `run.py` imports `run` and `_infer_goal_from_url` from it. Usable standalone as a dev stub via `python agent_core.py --url ...` — that's the fastest smoke test, but it bypasses `run.py`'s multi-page / PDF / persona layers.

**Agent core (`agent_core.py`):**
- `LLMAdapter` — normalizes calls across providers via `litellm.acompletion()`. Translates Anthropic message format (with base64 image blocks) to OpenAI format for LiteLLM. Exception: advisor-beta tool calls route directly to Anthropic SDK (`_complete_anthropic_advisor`). Emits Langfuse traces on every LiteLLM call when `LANGFUSE_PUBLIC_KEY` is set (gated at module load). Batch 38.1 added `langfuse_otel` callback; all 3 direct-SDK paths are wrapped via `_lf_observe` (Batch 45).
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

Session summaries live in `session_summaries/` (gitignored). Read the latest one before starting work. `LATEST.md` is a pointer to the most recent daily file. Full narrative for each batch: `git log --oneline` + `session_summaries/`.

| Batch | What |
|-------|------|
| 1–3 | Core agent, runs/ layout, suite runner, dashboard, CI wiring, persona system, scout, multi-provider LLMAdapter |
| 4–4f | Persistent auth session, two-step login, domain subfolder layout |
| 5–13 | Per-page goals, auth fixes, PDF screenshots, exec summary, `--discover` auth |
| 14–15 | `personas.py`, `nav:` label helper, persona inference threading, `--url` required |
| 38.1–39 | `langfuse_otel` callback, `_lf_observe` on 3 direct-SDK paths (Batch 45 replaced broken OTel approach) |
| 40–44 | Auth debug screenshot, dedup pass, nav drift guard (`_nav_drift_check`), persona validation |
| 45 | Fix Langfuse CPU-peg bug — `capture_input/output=False` on `_lf_observe`; removed `_trace_session()` and `AnthropicInstrumentor` |
| 58–60 | Cost logging, `suite_session_id`, `cost_summary.json`, dashboard Tokens column |
| 61–64 | `drift_report.py`, per-type thresholds, `(url, run_type)`-scoped baselines, per-step normalization |
| 65–67 | Async cleanup, Langfuse session leak fix (`metadata.session_id` isolation) |
| 68–69 | `compare_variants.py` + chart, dashboard rework, `build_index.py` fix, `DECISIONS.html` |
| 70–72 | Advisor visibility, LLM judge, pre-public sweep, advisor aggregation fix |
| 73 | Audit: dep pinning, CLAUDE.md trim, drift calibration |

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
- **Drift evaluation.** Resolved (Batch 61 + Batch 64 + Batch 66 + Batch 73 calibration): `drift_report.py` reads `cost_log.csv`, groups by `(url, run_type)`, flags runs that exceed per-type threshold vs first-run baseline. Inline check in `run.py` prints a warning after each `_log_cost()` call. CLI: `python drift_report.py`. Thresholds calibrated 2026-05-01 (24 runs): `single=0.20` (observed <20% variance), `multi=0.30` (observed ±33%; advisor variants intentionally exceed — expected). Per-step normalization active: when both rows carry `step_count > 0`, drift is measured on tokens/step not raw total.
- **API key rotation.** Resolved (2026-05-01).
- **`langfuse_cost_usd` backfill gap.** Resolved as unresolvable: the 3 pre-Batch-58 portfolio suite runs (Stripe, Linear, Vercel) have no matching Langfuse traces — they predate the `suite_session_id` wiring. Backfill was attempted (2026-04-28) and confirmed empty. All runs from Batch 58 onward auto-populate `langfuse_cost_usd` at run time.
- **Cross-task Haiku variant matrix.** Add a `--menial-model` flag (or per-task overrides) so executive-summary / persona-generation / persona-enrichment / scout can swap between Sonnet and Haiku independently of the agent loop. Run a 4-cell matrix (Haiku × Sonnet for menial; advisor on/off) across the 3 portfolio sites to measure cost vs quality drop. Likely batch 74+ — unblocks once batch 71 judge proves the rubric works. Hypothesis: Haiku-for-menial significantly cuts cost on multi-page suites with negligible quality loss.
- **Advisor invocation tracking diagnosis.** Resolved (Batch 72b): `run.py:378-379` now aggregates `advisor_called_count` and `advisor_eligible_steps` across all pages in multi-page runs. Root cause was the aggregation loop not summing those fields from per-page token dicts. See DECISIONS.md §9 for the pre-fix data and interpretation.

## 8. Working rules for this repo

- **Minimum viable diff.** Make the stated fix and nothing else. No refactors, no renames, no "while I'm here" cleanups, no adding type annotations or docstrings to code you didn't change. If the fix is 5 lines, the diff is 5 lines.
- **One commit per completed batch, fresh Claude Code session per batch.** Each batch has a crisp scope. Don't carry context across batches inside a single session. When a batch is done, commit, `/exit`, start fresh.
- **Do not run the full suite to verify.** Smoke test on a single URL (`python agent_core.py --url https://linear.app --steps 4`) is sufficient. Full suite runs burn tokens and rarely surface anything a smoke test doesn't.
- **After each fix, print 2–3 sentences explaining what changed and why.** Not a diff, not a list — a short prose explanation the user can read in 10 seconds to decide whether to commit.
- **If you go off-rails, the user will `/exit`.** Don't spiral. If the first approach fails, diagnose once, try a focused second approach, and if that also fails stop and explain rather than burning more budget.
- **Pre-commit ritual.** Before every commit, append a new entry to `session_summaries/YYYY-MM-DD.md` (gitignored), update `session_summaries/LATEST.md`, show the entry in chat, then commit.
- **Secrets hygiene.** Never hardcode credentials. Never commit `.env`, auth state JSON, or screenshots containing filled login fields. `runs/`, `screenshots/`, `reports/`, and `session_summaries/` are gitignored — keep them that way.
- **Always `git fetch` before reading git state.** Use `git fetch && git log origin/main --oneline` not bare `git log`. Bare `git log` only shows local commits — PRs merged via GitHub UI won't appear until pulled.
- **Dependency license check when adding a new dep.** Run `venv/bin/pip-licenses --package <name>` and confirm the license is MIT/Apache/BSD/PSF/ISC before committing. Flag anything LGPL, GPL, AGPL, or MPL for an explicit decision — MPL is usable but weak copyleft; GPL/AGPL are incompatible with the repo's MIT license.
- **Regenerate `DECISIONS.pdf` and `DECISIONS.html` whenever `DECISIONS.md` changes.** Both are tracked portfolio artifacts (PDF for offline / editorial register, HTML for in-browser / site-token register — recruiters/founders shouldn't need to install Playwright either way). `python render_decisions.py` produces both from the same `parse()` output. Stage all three (`DECISIONS.md`, `.pdf`, `.html`) together. If you commit DECISIONS.md without regenerating, the public artifacts silently drift from the source.
