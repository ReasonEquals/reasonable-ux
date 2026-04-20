# CLAUDE.md — reasonable-ux/tests/

## WARNING: This is NOT a test directory

`agent_test.py` is the **agent core**, not a test file. `run.py` imports `run` and `_infer_goal_from_url` from it. The path is legacy — don't reorganize, the import paths are load-bearing. Refactor planned.

## Files

- **agent_test.py** — the entire agent loop. LLMAdapter, screenshot capture, prompt builder (`_build_prompt`), step loop (`run()`), below-fold analysis. Biggest file in the repo.

## Key invariants (agent_test.py)

- Persona: `None` on step 1 (inferred from screenshot), then threaded through remaining steps. Don't re-infer.
- `step_budget = 2048 if advisor else 1024` at `adapter.complete()` — don't raise without rewriting JSON schema
- Image stripping: only current step's screenshot stays in context. Don't undo.
- `nav:<Label>` prefix bypasses `_sanitize_selector` entirely, routes through `_click_nav_by_label`

## Smoke test

```bash
python tests/agent_test.py --url https://linear.app --steps 4
```

See root CLAUDE.md for full architecture map and working rules.
