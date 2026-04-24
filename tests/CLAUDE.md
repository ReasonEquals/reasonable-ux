# CLAUDE.md — reasonable-ux/tests/

## This directory contains only real test files

- **test_sanitize_extracted.py** — 28 assertions covering `_sanitize_selector` prompt-injection defense cases
- **test_agent_core.py** — unit tests for pure functions in `agent_core.py` (added in Batch 43)

The agent loop lives at `agent_core.py` (repo root), not here.

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
