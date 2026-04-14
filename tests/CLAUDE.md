# CLAUDE.md — reasonable-ux/tests/

## WARNING: This is NOT a test directory

`agent_test.py` is the **agent core** (~1023 lines), not a test file. `run.py` imports `run` and `_infer_goal_from_url` from it. The path is legacy — don't reorganize, the import paths are load-bearing. Refactor planned.

## Files

- **agent_test.py** — the entire agent loop. LLMAdapter, screenshot capture, prompt builder (`_build_prompt`), step loop (`run()`), below-fold analysis. Biggest file in the repo.
- **planner.py** — scrapes a page via Playwright, asks Claude to extract testable elements + generate prioritized test cases for `suite_runner.py`. `--url` required.
- **test_login.py** — legacy manual Playwright test. Not wired into anything. Ignore.

## Key invariants (agent_test.py)

- UX vs QA branch in `_build_prompt` at line 230 — keep branches strictly isolated
- Persona: `None` on step 1 (inferred from screenshot), then threaded through remaining steps. Don't re-infer.
- `max_tokens=1024` at `adapter.complete()` — don't raise without rewriting JSON schema
- Image stripping (lines 794–800): only current step's screenshot stays in context. Don't undo.
- `nav:<Label>` prefix bypasses `_sanitize_selector` entirely, routes through `_click_nav_by_label`

## Smoke test

```bash
python tests/agent_test.py --url https://linear.app --mode ux --steps 4
```

See root CLAUDE.md for full architecture map and working rules.
