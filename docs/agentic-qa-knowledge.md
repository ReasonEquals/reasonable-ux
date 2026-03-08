# Agentic QA System — Knowledge Base
### Distilled from building reasons-qagent end-to-end

---

## What This Document Is

A complete reference for building an agentic QA testing tool using Python, Playwright, and the Claude API. Covers architecture decisions, key learnings, best practices, talking points, and gotchas — distilled from multiple build sessions. Intended as both a replication guide and an interview/portfolio reference.

---

## The Core Concept

An agentic QA system differs from traditional test automation in one fundamental way:

> **Traditional automation:** You tell the computer exactly what to do. Brittle. Breaks when UI changes.
> **Agentic automation:** You tell the agent what to *goal* to achieve. It figures out the steps.

The agent sees the page as a screenshot, decides what to do, executes it, takes another screenshot, and repeats. It reasons like a QA engineer would — observing, deciding, acting — rather than following a script.

This means:
- It can handle unexpected UI states
- It can recover from partial failures
- It generates its own test cases from page structure
- Outcomes are non-deterministic (a feature and a limitation)

---

## Architecture Overview

```
planner.py          → Scrapes HTML, generates prioritized test cases
agent_test.py       → Core agent loop: screenshot → Claude → action → repeat
run.py              → CLI entry point for single test runs
suite_runner.py     → Orchestrates full suite: planner → agent × N → reports
build_index.py      → Indexes all runs for the dashboard
dashboard.html      → Visual browser for all historical runs
```

### The Pipeline

```
URL → planner.py → test_cases[]
                         ↓
              suite_runner.py loops:
                agent_test.py (per test)
                  screenshot → Claude API → JSON decision → browser action
                  repeat until done/timeout/max_steps
                         ↓
              report.json + report.html (per test)
              suite_report.html (per suite)
              build_index.py → runs/index.json
              dashboard.html reads index.json
```

---

## Key Concepts to Understand

### asyncio / await
Python runs one thing at a time by default. Playwright (browser control) and the Claude API are both I/O-bound — they spend most of their time waiting for responses. `asyncio` lets Python do other work while waiting instead of blocking.

```python
# This blocks — nothing else can run while waiting
response = client.messages.create(...)

# This yields control — other tasks can run while waiting
response = await async_client.messages.create(...)
```

**Rule of thumb:** Any function that calls Playwright or an async API needs `async def` and `await`.

### The Agent Loop
The agent loop is the heart of the system:
1. Take screenshot
2. Strip images from previous conversation turns (cost optimization)
3. Append current screenshot + prompt to conversation
4. Send to Claude API
5. Parse JSON response
6. Execute action (click/type/navigate)
7. Repeat until `action: done` or step limit

The conversation history is what gives the agent memory — it can see what it observed and decided in previous steps.

### Conversation History
Claude has no memory between API calls. You pass the entire conversation each time:
```python
conversation.append({"role": "user", "content": [screenshot, prompt]})
response = client.messages.create(messages=conversation)
conversation.append({"role": "assistant", "content": response_text})
```
This is how the agent "remembers" what it already tried.

### Virtual Environments
Always use a venv. It isolates your project's dependencies from the system Python. Without it, packages from different projects conflict.
```bash
python -m venv venv
venv\Scripts\activate   # Windows
source venv/bin/activate # Mac/Linux
pip install anthropic playwright python-dotenv
```

### .env and .gitignore
Never commit API keys. Store them in `.env`, load with `python-dotenv`, add `.env` to `.gitignore`.
```python
from dotenv import load_dotenv
load_dotenv()
client = Anthropic()  # automatically reads ANTHROPIC_API_KEY from env
```

---

## Engineering Decisions (with reasoning)

### 1. Token Cost Optimization — 84% Reduction
**Problem:** Each step sent all previous screenshots to Claude. A 8-step test sent 8 images on step 8.
**Solution:** Strip images from previous conversation turns before each API call. Keep text (reasoning trail) but drop image blocks.
**Result:** ~55,000 tokens/test → ~8,800 tokens/test.
**Why it works:** Only the current page state matters for deciding the next action. Historical screenshots are noise.

```python
for msg in conversation:
    if msg["role"] == "user" and isinstance(msg["content"], list):
        msg["content"] = [
            block for block in msg["content"]
            if block.get("type") != "image"
        ]
```

**Talking point:** "I identified a token cost problem early, profiled it, and drove an 84% reduction without any measurable impact on test quality."

### 2. JPEG Compression
Screenshots sent as JPEG at 40% quality instead of PNG. Claude reads the page fine at this quality. Significant reduction in image token cost.
```python
screenshot = await page.screenshot(type="jpeg", quality=40)
```

### 3. Folder-per-Run Structure
Each run gets its own folder: `runs/{timestamp}_{label}/screenshots/`. Suite runs nest inside `runs/suite_{timestamp}/`.
**Why:** Prevents file collisions, makes runs self-contained, scales to parallel execution.

### 4. Separation of Concerns
Each file has exactly one job. `agent_test.py` is a reusable module — it doesn't call `build_index.py`, doesn't parse CLI args, doesn't know about suites. `suite_runner.py` orchestrates. `run.py` is the user-facing CLI.
**Why it matters:** When you need to add CI/CD, swap the planner, or extend the agent, you only touch one file.

### 5. CSS Selector Guidance in Prompt
Claude defaults to bare tag selectors (`a`, `button`) which match multiple elements and cause Playwright to fail or timeout.
**Fix:** Explicitly prompt Claude to prefer id and attribute selectors:
```
prefer id over class over tag (e.g. '#username', 'button[type=submit]', 'input[name=password]')
avoid generic selectors like '.button' or bare 'a'
```
**Result:** Eliminated most selector-based failures without post-processing Claude's output.

### 6. Structured JSON Output
Claude returns decisions as JSON, not prose. This makes the agent loop deterministic to parse.
```json
{
    "observation": "what you see",
    "action": "click | type | navigate | done",
    "target": "CSS selector or URL or null",
    "value": "text to type or null",
    "reasoning": "why",
    "pass_fail": "pass | fail | in_progress",
    "verdict": "one sentence summary"
}
```
Always strip markdown fences before parsing: `raw.replace("```json", "").replace("```", "").strip()`

### 7. Timeout Handling
Two levels:
- **Per-step:** Wrap each browser action in `asyncio.wait_for(..., timeout=10)` — catches hung clicks/fills
- **Per-test:** Wrap the entire `run()` call in `asyncio.wait_for(..., timeout=300)` in suite_runner — catches infinite loops

Always catch `asyncio.TimeoutError` before the generic `Exception` catch, or it will never be reached.

```python
except asyncio.TimeoutError:
    # handle timeout
except Exception as e:
    # handle other errors
```

### 8. Headless Detection
CI environments have no display. Running headed (`headless=False`) on GitHub Actions crashes immediately.
**Fix:** Detect the `CI` environment variable (automatically set by GitHub Actions):
```python
headless = os.environ.get("CI", "false").lower() == "true"
browser = await p.chromium.launch(headless=headless)
```
Runs headed locally (visible browser for debugging), headless in CI — no manual config needed.

### 9. Exit Codes for CI/CD
CI needs to know if tests passed or failed via exit codes: `0` = pass, `1` = fail.
```python
result = asyncio.run(run_suite(...))
sys.exit(0 if result else 1)
```
`run_suite()` returns `True` if suite passed, `False` otherwise.

### 10. CI Skip Logic
Running all tests in CI is expensive and some tests are headless-incompatible (CSS ribbon overlays, new tab links). Skip low/medium in CI, only run HIGH priority:
```python
CI_MODE = os.environ.get("CI", "false").lower() == "true"
if CI_MODE and priority in ["low", "medium"]:
    # skip and continue
```
Skipped tests do NOT count as failures — suite status only fails on `fail`, `error`, or `timeout`.

---

## GitHub Actions (CI/CD)

### How It Works
A YAML file at `.github/workflows/test.yml` tells GitHub what to do on certain events. GitHub spins up a fresh Ubuntu VM, runs your steps, and reports pass/fail.

### Key Concepts
- **`on:`** — what triggers the workflow (`push`, `pull_request`, `workflow_dispatch`)
- **`workflow_dispatch`** — manual trigger via GitHub UI (avoids running on every commit = cost control)
- **`secrets`** — encrypted key-value store for API keys; reference as `${{ secrets.ANTHROPIC_API_KEY }}`
- **`upload-artifact`** — saves files from the VM so you can download them after the run
- **`if: always()`** — runs the upload step even if tests failed

### The Workflow File
```yaml
name: QA Suite
on:
  workflow_dispatch:
  pull_request:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: |
          pip install anthropic playwright python-dotenv
          playwright install chromium
          playwright install-deps chromium
      - env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python suite_runner.py --url https://yoursite.com --steps 8
      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: qa-reports
          path: runs/
```

### Common CI Pitfalls
- `headless: False` crashes on GitHub Actions — no display server
- Mixed tabs/spaces in Python cause `IndentationError` — run "Convert Indentation to Spaces" in Cursor
- `git add .gitHub/` fails on case-sensitive systems — use exact case `.github/`
- `workflow_dispatch:` needs either `inputs: {}` or be part of a list format

---

## Known Limitations

| Limitation | Cause | Mitigation |
|---|---|---|
| CSS ribbon elements not clickable headless | Element reported as not visible by Playwright | Skip in CI or mark as known |
| New tab links timeout | Agent can't switch browser contexts | Document as limitation |
| Non-deterministic results | LLM reasoning varies | Run multiple times, use majority verdict |
| OAuth/MFA flows | Multi-step auth outside agent's context | Not currently supported |
| Dynamic UI with delayed render | Agent acts on stale state | Increase sleep between steps |

---

## Best Practices

### Prompt Engineering for Agents
- **Be explicit about output format** — specify exact JSON shape, Claude will follow it
- **Include step context** — tell the agent what step it's on and what the max is
- **Force termination** — "If you are on step N, you MUST use action: done" prevents infinite loops
- **Selector guidance** — explicitly tell Claude what kind of selectors to prefer/avoid
- **Negative examples** — "avoid generic selectors like '.button'" is more effective than just positive guidance

### Cost Management
- Strip images from conversation history after each step
- Use JPEG at 40% quality instead of PNG
- Skip low/medium priority tests in CI
- Track tokens per test and per suite — makes optimization data-driven
- Consider model tiering for future: cheap model for actions, strong model for failure analysis

### Debugging
- Always run headed locally first — watching the browser is the fastest way to understand failures
- JSON report per step lets you see exactly what the agent observed and decided
- Screenshots at each step are your audit trail
- `print(raw)` before parsing lets you see Claude's raw output when debugging JSON parse errors

### Project Structure
- One file, one job — never let files call each other in a circle
- CLI args go in the entry point (`run.py`), not in the module (`agent_test.py`)
- Infrastructure (`build_index.py`) stays separate from agent logic
- Keep `runs/` in `.gitignore` — reports are artifacts, not source code

---

## Talking Points for Interviews

**On the project overall:**
"I built an agentic QA tool that takes a URL, generates its own test cases by analyzing the page structure, executes them in a real browser using vision-based reasoning, and produces structured pass/fail reports. It runs in CI via GitHub Actions."

**On the token optimization:**
"I identified that early runs were sending every previous screenshot to Claude on each step. The current page state is all that matters for deciding the next action, so I stripped image blocks from conversation history while keeping the text reasoning trail. That reduced token usage by 84% with no measurable impact on test quality."

**On the architecture decisions:**
"I kept strict separation of concerns — the agent module doesn't know about suites, the suite runner doesn't know about the CLI. This made adding CI/CD straightforward because the entry point and the logic were already decoupled."

**On agentic vs traditional automation:**
"Traditional automation is brittle because it encodes the exact steps. An agentic approach encodes the goal and lets the agent reason about how to achieve it. That makes it more resilient to UI changes and capable of exploratory testing that a script can't do."

**On limitations and honesty:**
"The system has real limitations — it's non-deterministic, it can't handle OAuth flows, and some UI elements aren't reliably clickable in headless mode. I documented these explicitly in the README rather than hiding them, because the honest framing is that this is exploratory agentic testing, not a replacement for deterministic regression."

---

## Tech Stack Reference

| Tool | Purpose | Key Concept |
|---|---|---|
| `anthropic` Python SDK | Claude API calls | `client.messages.create()`, conversation history |
| `playwright` | Browser automation | `async_playwright`, `page.click()`, `page.fill()`, `page.screenshot()` |
| `python-dotenv` | Environment variables | `load_dotenv()` reads `.env` file |
| `asyncio` | Async execution | `async def`, `await`, `asyncio.run()`, `asyncio.wait_for()` |
| `pathlib.Path` | File paths | Cross-platform path handling |
| `json` | Parse Claude output | `json.loads()`, `json.dump()` |
| `base64` | Encode screenshots | Images must be base64 for Claude API |
| GitHub Actions | CI/CD | `.github/workflows/`, secrets, artifacts |

---

## Replication Checklist

- [ ] Create project folder and venv
- [ ] Install: `anthropic playwright python-dotenv`
- [ ] Run: `playwright install chromium`
- [ ] Create `.env` with `ANTHROPIC_API_KEY=sk-ant-...`
- [ ] Add `.env` and `runs/` to `.gitignore`
- [ ] Build `agent_test.py` — single agent loop with screenshot + JSON decision
- [ ] Add image stripping for token optimization
- [ ] Add per-step timeout with `asyncio.wait_for`
- [ ] Build `planner.py` — HTML scrape + Claude test case generation
- [ ] Build `suite_runner.py` — loop over test cases, collect results, build HTML report
- [ ] Add total test timeout in suite runner
- [ ] Add exit codes (`sys.exit(0 if result else 1)`)
- [ ] Add headless detection via `CI` env var
- [ ] Build `build_index.py` — scan runs/, write index.json
- [ ] Build `dashboard.html` — reads index.json, displays all runs
- [ ] Build `run.py` — CLI entry point with argparse
- [ ] Create `.github/workflows/test.yml`
- [ ] Add `ANTHROPIC_API_KEY` to GitHub Secrets
- [ ] Add CI skip logic for low/medium priority tests
- [ ] Write README with: what it is, how it works, setup, usage, engineering decisions, known limitations

---

*Built across multiple sessions. Last updated March 2026.*
