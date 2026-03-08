# reasons-qagent 🧪

![Agent Demo](docs/demo.gif)

## Dashboard
![Dashboard](docs/dashboard.png)

## Sample Report
![Sample Report](docs/sample_report.png)

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

**Run with auto-generated test plan (recommended):**
```bash
python run.py --plan --url "https://yoursite.com"
```

**Run with a specific goal:**
```bash
python run.py --url "https://yoursite.com" --goal "Test the checkout flow"
```

**Run with default login test:**
```bash
python run.py
```

**View the dashboard:**
```bash
python -m http.server 8000
# Open http://localhost:8000/dashboard.html
```

---

## Engineering decisions

**Context window optimization**
Early runs consumed ~55,000 tokens per test. Images from prior conversation turns were being sent to Claude repeatedly even though only the current screenshot matters. Stripping images from previous messages while preserving the text reasoning trail reduced average token usage to ~8,800 per run — an 84% reduction with no measurable impact on test quality. Sequential multi-test suite execution is in progress; per-test cost will remain ~8,800 tokens regardless of suite size.

**Separation of concerns**
`agent_test.py` is a reusable module. `run.py` is the single entry point. `build_index.py` is infrastructure. Each file has one job. The planner is its own module so it can be swapped, extended, or called independently.

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
├── screenshots/           # Per-step screenshots (auto-generated)
├── reports/               # JSON + HTML reports (auto-generated)
├── docs/                  # README assets
├── dashboard.html         # Visual report dashboard
├── build_index.py         # Indexes reports for dashboard
├── run.py                 # Orchestrator and CLI entry point
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
Active development. Core pipeline (planner → agent → report → dashboard) is working. 

**In progress:**
- Sequential test suite execution (run all generated test cases in one command)
- Orchestrator improvements
- CI/CD integration

---

*Built to explore agentic QA workflows and demonstrate practical AI systems thinking.*