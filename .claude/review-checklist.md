# Review Checklist — reasonable-ux

Project-specific review criteria. Used by the /review skill.

## Security
- [ ] No hardcoded credentials, API keys, or tokens
- [ ] No unsafe eval(), exec(), or os.system() with user input
- [ ] Temp files cleaned up (/tmp/auth_debug.png, /tmp/auth_state.json)
- [ ] No sensitive data in error messages or logs

## Project Rules (from CLAUDE.md)
- [ ] agent_core.py is the agent loop at repo root — tests/ contains only real test files
- [ ] max_tokens=1024 per step has not been raised without discussion
- [ ] UX and QA branches are isolated — UX changes don't leak into QA mode
- [ ] Minimum viable diff — no drive-by refactors
- [ ] No changes to auto-generated files

## Known Backlog
- [ ] nav: prompt uses label text, not CSS selectors (prompt drift)
- [ ] Temp files from auth flow are cleaned up after run
- [ ] --discover page-type filter skips /about, /careers, /press
- [ ] Cross-page friction dedup works in multi-page runs
- [ ] Multi-site persona validation passes diverse sites

## Quality
- [ ] No debug print() statements left in
- [ ] No commented-out code blocks
- [ ] Error handling covers failure paths
- [ ] Image stripping still active (84% token reduction)
