# CLAUDE.md — reasonable-ux/tests/

## This directory contains only real test files

- **test_sanitize_extracted.py** — 16 assertions covering `_sanitize_extracted.py` prompt-injection defense (`sanitize_persona`, `sanitize_field`, `sanitize_string_list`)
- **test_agent_core.py** — 16 assertions covering pure helpers `_sanitize_selector` and `_infer_goal_from_url` (Batch 48)
- **test_evals.py** — 18 assertions covering `_NAV_DRIFT_RE` and `_nav_drift_check` from `evals/run_evals.py` (Batch 48)

Scope rule: pure functions only. The agent loop itself is deliberately untested — see DECISIONS.md §8 for why. The agent loop lives at `agent_core.py` (repo root), not here.

## Key invariants (agent_core.py)

- Persona: `None` on step 1 (inferred from screenshot), then threaded through remaining steps. Don't re-infer.
- `step_budget = 2048 if advisor else 1024` at `adapter.complete()` — don't raise without rewriting JSON schema
- Image stripping: only current step's screenshot stays in context. Don't undo.
- `nav:<Label>` prefix bypasses `_sanitize_selector` entirely, routes through `_click_nav_by_label`

## Smoke test

```bash
python agent_core.py --url https://linear.app --steps 4
```

See root CLAUDE.md for full architecture map and working rules.
