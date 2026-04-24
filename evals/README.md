# Eval harness (Phase 1)

Deterministic regression net for the agent loop. Runs before/after any change that could shift model behavior — LiteLLM swap, prompt edits, model bumps. If pass-rate drops more than ~5% between runs, something regressed.

This is **Phase 1** of the LLMOps v1 integration plan. It exists specifically to catch regressions in the next three phases (LiteLLM swap, cost ceiling, Langfuse).

## How it works

1. `labels.jsonl` — one URL per line with expected behavior.
2. `run_evals.py` — per invocation:
   - Creates a fresh `eval_runs/<YYYY-MM-DD_HHMMSS>[_<label>]/` directory (sibling to `runs/`).
   - **Pre-flight check** per label: 10s `requests.get` with the shared user-agent. Skips the URL (does not spend tokens) on any of: HTTP ≥ 400, non-HTML content-type, network error, or a Cloudflare/captcha challenge-body signature. Skipped URLs are tracked separately in the manifest and excluded from the pass-rate denominator.
   - For each label that passes pre-flight: calls `agent_core.run(url, max_steps=4)`, finds the produced `runs/{domain}/{ts}_single_page/`, **moves** it into `eval_runs/<eval_ts>/{domain}/` so audit-grade `runs/` stays clean.
   - Runs five assertions per URL:
     - `report.json` parses as valid JSON
     - Step 1's top-level `persona` string contains at least one `expected_persona_keywords` entry (case-insensitive)
     - Aggregate score falls inside `expected_score_band`. Score = `mean(all per-step subscores across cta_clarity / copy_quality / flow_smoothness) * 20` — yields a 20–100 scale.
     - At least one `expected_friction_keywords` entry appears as a lowercase substring in the concatenated `friction_points` text
     - Wall-clock over 90s warns (does not fail)
   - Writes `manifest.json` at the eval-run root with pass-rate, per-category breakdown, per-URL results (score, persona, failures, warnings, wall clock), labels-file SHA256, and settings.

Evals run at `max_steps=4` fixed to keep cost down. No personas, no PDF, no advisor.

Pre-flight and the Playwright browser context both use an identifiable user-agent defined at `agent_core.py` top-level (`USER_AGENT`): `Mozilla/5.0 … Chrome/131.0.0.0 Safari/537.36 reasonable-ux/0.1`. Real-Chrome prefix so fingerprint-based blockers pass; `reasonable-ux/0.1` suffix so site operators seeing the traffic in logs can identify the tool. Audit runs (`run.py`, `site_crawler.py`) will follow in a later TOS-hygiene batch.

## Output layout

```
eval_runs/
  2026-04-21_0930_baseline/
    manifest.json          ← pass rate, per-URL results, labels SHA, settings
    linear_app/
      report.json
      report.html
      screenshots/
      full_page.jpeg
      below_fold.json
      console.json
      network.json
    figma_com/
      ...
    stripe_com/
      ...
  2026-04-25_1700_post-phase2/
    manifest.json
    ...
```

The whole `eval_runs/` tree is gitignored. Manifests are diffable across historical eval runs — grep pass_rate or aggregate score per URL over time to see drift.

## `labels.jsonl` schema

One JSON object per line:

```json
{"url": "https://linear.app", "expected_persona_keywords": ["engineer", "manager"], "expected_score_band": [60, 90], "expected_friction_keywords": ["pricing", "cta"], "category": "saas_landing"}
```

| Field | Type | Notes |
|---|---|---|
| `url` | string | Fully-qualified URL the agent will visit |
| `expected_persona_keywords` | list[string] | 2–4 lowercase hints. OR-match against the agent's free-form persona string. Pick generic ones ("engineer", "shopper"), not specific ("senior staff ENG IV"). |
| `expected_score_band` | [int, int] | Inclusive 20–100 band. Use ±15 around the expected mean — tighter and you're measuring model variance, not regressions. |
| `expected_friction_keywords` | list[string] | 2–4 lowercase substrings. Pick obvious ones ("pricing", "cta", "above the fold") not subjective ones ("polish"). OR-match. |
| `category` | string | One of `saas_landing`, `dtc_ecom`, `content_media`. Used for the per-category breakdown. Auth-walled product UIs (login pages, dashboards) are intentionally out of scope — this tool targets marketing surfaces until explicit permission exists to test authenticated flows. |

## Adding a URL

1. Run the agent once manually to calibrate: `python agent_core.py --url https://newsite.com --steps 4`.
2. Open the produced `runs/{domain}/{ts}_single_page/report.json` and note the persona string, the three subscores per step, and the friction points.
3. Compute an expected score: mean of all subscores × 20. Set the band ±15 points.
4. Pick 2–4 keywords each for persona and friction that felt obvious in the real output.
5. Append a line to `labels.jsonl`.

## Running

```bash
python evals/run_evals.py                                     # all labels
python evals/run_evals.py --limit 3                           # first 3 labels
python evals/run_evals.py --category saas_landing             # one category
python evals/run_evals.py --label baseline                    # nickname the eval_runs/ dir
python evals/run_evals.py --label post-phase2 --category saas_landing
```

`--label` is a nickname suffix on the eval-run directory. Useful for marking milestone runs like `baseline` (pre-Phase 2), `post-phase2`, `post-langfuse`, etc.

## Reading the output

Per-URL line prints `PASS` or `FAIL` plus wall clock. Failures list the specific assertion miss (expected vs. actual). Warnings (wall-clock > 90s) don't fail the run.

End-of-run summary:

```
============================================================
RESULT: 17/19 passed (1 skipped)
Eval run dir: eval_runs/2026-04-21_0930_baseline
============================================================

Per-category:
  content_media: 5/6
  dtc_ecom: 6/6 (1 skipped)
  saas_landing: 6/7

Failures:
  https://example.com (dtc_ecom)
    - score 42.0 outside band [55, 85]
  https://other.com (content_media)
    - no friction keyword matched — expected any of ['subscribe', 'paywall']

Skipped (pre-flight):
  https://blocked.example (dtc_ecom) — HTTP 403
```

Exit code is 0 iff no URL failed (skipped URLs don't fail the run, but surface in the summary so you know to swap them).

## Label-set sizing

**Minimum 20 URLs, 30–40 ideal.** Below 20, a single failing URL swings pass-rate more than 5%, which is the regression threshold Phase 2 uses — meaning you can't distinguish "real regression" from "one flaky URL".

Spread across all three categories (5–10 each). A 20-URL set skewed entirely to one category under-tests the rest of the model's behavior surface.

## Out of scope for Phase 1

- Cost ceiling (Phase 3) — evals run unbudgeted.
- Langfuse traces (Phase 4) — no telemetry yet.
- LiteLLM (Phase 2) — evals call the existing `agent_core.run` unchanged.
