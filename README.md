# reasons-qagent 🧪
![CI](https://github.com/qa-reasonably/reasons-qagent/actions/workflows/test.yml/badge.svg)

![Agent Demo](docs/demo.gif)

## Dashboard
![Dashboard](docs/dashboard.png)

## Suite Report
![Suite Report](docs/sample_report.png)

An agentic QA testing tool powered by Claude AI and Playwright. Point it at any URL and it plans, executes, and reports on test coverage automatically.

---

## How it works

Three agents, one pipeline:

**1. Planner** — scrapes the target page HTML, identifies testable elements, and generates prioritized test cases without any manual input.

**2. Agent** — receives a goal, launches a real browser, and decides what to do at each step by analyzing screenshots. No hardcoded scripts.

**3. Orchestrator** — coordinates the planner and agent, rebuilds the report index, and tracks token usage across the run.

The core loop:
1. Planner reads the page and writes the test goals
2. Agent takes a screenshot → sends it to Claude → Claude decides the next action
3. Repeat until goal is complete or step limit reached
4. JSON and HTML reports saved with per-step reasoning and pass/fail verdicts

> This is exploratory agentic testing, not deterministic regression. The agent reasons about what to do at each step rather than following a fixed script — which means it can handle unexpected UI states, but outcomes may vary between runs.

---

## Usage

**Run full suite — planner generates and executes all test cases:**
```bash
python suite_runner.py --url "https://yoursite.com"
```

**Run single test with auto-generated goal:**
```bash
python run.py --plan --url "https://yoursite.com"
```

**Run single test with specific goal:**
```bash
python run.py --url "https://yoursite.com" --goal "Test the checkout flow"
```

**View the dashboard:**
```bash
python -m http.server 8000
# Open http://localhost:8000/dashboard.html
```

---

## CI/CD

The suite runs automatically on pull requests and can be triggered manually via GitHub Actions.

Reports are uploaded as artifacts after each run and are downloadable from the Actions tab.

To trigger manually: **GitHub → Actions → QA Suite → Run workflow**

Exit codes are wired up correctly — the workflow returns `0` on suite pass and `1` on any failure or timeout, so CI status reflects real test outcomes.

---

## Engineering decisions

**Context window optimization**
Early runs consumed ~55,000 tokens per test. Images from prior conversation turns were being sent to Claude repeatedly even though only the current screenshot matters. Stripping images from previous messages while preserving the text reasoning trail reduced average token usage to ~8,800 per run — an 84% reduction with no measurable impact on test quality. Full suite runs (8 test cases) average ~62,000 tokens total (~$0.94) with each test case running in a fresh isolated context.

**Timeout handling**
Each browser action (click, fill, navigate) has an individual timeout, and each test has a total runtime ceiling. When exceeded, the step is marked as a forced fail with a screenshot of the last known state, the suite continues to the next test, and timeouts are tracked separately in the suite report alongside pass/fail/error counts.

**Separation of concerns**
`agent_test.py` is a reusable module. `run.py` is the single-goal entry point. `suite_runner.py` runs the full planner-generated suite. `build_index.py` is infrastructure. Each file has one job. The planner is its own module so it can be swapped, extended, or called independently.

**Folder-per-run structure**
Each test run creates its own folder under `runs/` containing screenshots and reports. Suite runs nest individual test folders inside a parent suite folder. This prevents file collisions, makes runs self-contained, and scales cleanly to parallel execution in the future.

**CSS selector guidance**
Claude's default tendency is to use bare tag selectors (`a`, `button`) which match multiple elements and cause timeouts. Prompting Claude to prefer id and attribute selectors (`#username`, `button[type=submit]`) eliminated this failure mode without requiring post-processing of its output.

**Headless detection**
The agent runs headed locally (visible browser window) and headless automatically in CI. This is detected via the `CI` environment variable that GitHub Actions sets, requiring no manual config change between environments.

**JPEG compression**
Screenshots sent to Claude use JPEG at 40% quality instead of PNG. Claude can still read the page clearly at this quality level and the token cost of image blocks drops significantly.

---

## Known limitations

- **Headless selector variance** — CSS ribbon elements and overlapping UI components may not be clickable in headless mode, causing timeouts on tests that target them
- **New tab links** — the agent cannot follow links that open in a new tab; these tests will time out
- **Non-deterministic** — as an exploratory agent, test outcomes can vary between runs depending on Claude's reasoning
- **Authentication flows** — multi-step auth (OAuth, MFA) is not currently supported
- **Dynamic UI** — heavily JavaScript-driven pages with delayed rendering may cause the agent to act on stale page state

---

## Project structure
```
reasons-qagent/
├── tests/
│   ├── agent_test.py      # Agentic browser runner
│   ├── planner.py         # HTML scraper and test case generator
│   └── test_login.py      # Simple single-run baseline test
├── runs/                  # All test runs and suite runs (auto-generated)
├── docs/                  # README assets
├── dashboard.html         # Visual report dashboard
├── build_index.py         # Indexes runs for dashboard
├── run.py                 # Single-test orchestrator and CLI
├── suite_runner.py        # Full suite executor
├── .env                   # API key (never committed)
├── .env.example           # Env variable template
└── README.md
```

---

## Setup

**1. Clone the repo**
```bash
git clone https://github.com/qa-reasonably/reasons-qagent.git
cd reasons-qagent
```

**2. Create virtual environment**
```bash
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Mac/Linux
```

**3. Install dependencies**
```bash
pip install anthropic playwright python-dotenv
playwright install chromium
```

**4. Add your API key**
```bash
cp .env.example .env
# Edit .env and add your Anthropic API key
```

---

## Tech stack
- [Claude API](https://anthropic.com) — AI decision making and test planning
- [Playwright](https://playwright.dev) — Browser automation
- [Python](https://python.org) — Core language
- [GitHub Actions](https://github.com/features/actions) — CI/CD

---

## Status

Core pipeline is working end-to-end, including CI/CD via GitHub Actions.

**Completed:**
- Planner → agent → suite runner → report → dashboard pipeline
- 84% token reduction via image stripping
- Timeout handling with forced fail verdicts and suite-level tracking
- Headless mode with automatic CI detection
- CI/CD via GitHub Actions (manual trigger + pull requests)

**In progress:**
- Dashboard integration for suite reports
- SauceDemo stable run as primary demo target

---

*Built to explore agentic QA workflows and demonstrate practical AI systems thinking.*
