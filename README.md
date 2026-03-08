# reasons-qagent 🧪

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

## Engineering decisions

**Context window optimization**
Early runs consumed ~55,000 tokens per test. Images from prior conversation turns were being sent to Claude repeatedly even though only the current screenshot matters. Stripping images from previous messages while preserving the text reasoning trail reduced average token usage to ~8,800 per run — an 84% reduction with no measurable impact on test quality. Full suite runs (8 test cases) average ~62,000 tokens total (~$0.94) with each test case running in a fresh isolated context.

**Separation of concerns**
`agent_test.py` is a reusable module. `run.py` is the single-goal entry point. `suite_runner.py` runs the full planner-generated suite. `build_index.py` is infrastructure. Each file has one job. The planner is its own module so it can be swapped, extended, or called independently.

**Folder-per-run structure**
Each test run creates its own folder under `runs/` containing screenshots and reports. Suite runs nest individual test folders inside a parent suite folder. This prevents file collisions, makes runs self-contained, and scales cleanly to parallel execution in the future.

**CSS selector guidance**
Claude's default tendency is to use bare tag selectors (`a`, `button`) which match multiple elements and cause timeouts. Prompting Claude to prefer id and class selectors eliminated this failure mode without requiring post-processing of its output.

**JPEG compression**
Screenshots sent to Claude use JPEG at 40% quality instead of PNG. Claude can still read the page clearly at this quality level and the token cost of image blocks drops significantly.

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

---

## Status
Active development. Core pipeline (planner → agent → suite runner → report → dashboard) is working.

**In progress:**
- Dashboard integration for suite reports
- Timeout handling with forced fail verdicts
- CI/CD integration

---

*Built to explore agentic QA workflows and demonstrate practical AI systems thinking.*