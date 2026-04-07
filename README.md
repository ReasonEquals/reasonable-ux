# reasons-qagent

Give it a URL. It figures out what to test, runs the tests, and tells you what broke.

I built this the week I started at [Juno.tax](https://juno.tax). When I asked the CTO what I should learn, he suggested I get up to speed on agentic testing — so I built a tool instead of reading about one. It's been the primary way I've been exploring what LLM-driven browser agents can actually do in practice.

**84% token reduction** via image stripping from conversation history — full details in Engineering Decisions below.

---

## What it does

**QA mode (default)** — the planner scrapes the target page, generates prioritized test cases, and the agent executes them in a real browser. At each step: screenshot → Claude → action → repeat. Reports are JSON + HTML with per-step reasoning and pass/fail verdicts.

**UX mode (`--mode ux`)** — instead of pass/fail, the agent evaluates the page through three personas simultaneously: a CPA vetting software for their firm's tech stack, a B2B SaaS specialist who evaluates against category norms rather than consumer standards, and an experienced SaaS UX designer. Each step produces scores (1–5) across CTA clarity, copy quality, and flow smoothness, plus friction points with specific actionable recommendations.

After the agent loop completes in UX mode, a second pass re-navigates to the original URL, takes a full-page screenshot, and sends it to Claude in a single call to surface anything below the fold the agent couldn't see — pricing signals, social proof, feature depth, trust indicators. Findings and score adjustments are merged into the report.

UX runs generate a PDF report via `generate_report.py` — clean enough to hand to a founder.

---

## Quick start

```bash
git clone https://github.com/qa-reasonably/reasons-qagent.git
cd reasons-qagent
python -m venv venv && source venv/bin/activate
pip install anthropic playwright python-dotenv reportlab
playwright install chromium
cp .env.example .env  # add your ANTHROPIC_API_KEY
```

**QA mode — planner picks the goal:**
```bash
python run.py --url https://yoursite.com --plan
```

**QA mode — specific goal:**
```bash
python run.py --url https://yoursite.com --goal "Test the checkout flow with a valid card"
```

**UX mode:**
```bash
python run.py --url https://yoursite.com --mode ux --goal "Evaluate the landing page for a first-time visitor" --token-budget 30000
```

**UX mode with auth credentials:**
```bash
python run.py --url https://yoursite.com --mode ux --email you@example.com --password yourpassword
```

**Generate PDF from latest UX run:**
```bash
python generate_report.py --url https://yoursite.com
```

**Full suite (planner generates all test cases, runs them in sequence):**
```bash
python suite_runner.py --url https://yoursite.com --token-budget 15000
```

---

## Engineering decisions

- **Image stripping** — only the current screenshot is sent to Claude each step. Prior screenshots are stripped from conversation history while preserving the text reasoning trail. Reduced average token usage from ~55,000 to ~8,800 per run (84%) with no measurable impact on test quality.
- **JPEG at 40%** — screenshots are compressed before encoding. Claude reads the page clearly at this quality level; PNG would cost significantly more per image block.
- **Model tiering** — the planner runs on Sonnet 4.6, the agent on Opus 4.5. Planner work (HTML → test cases) doesn't need the same reasoning depth as vision-based step-by-step decisions.
- **URL anchor** — the target URL is injected into every step prompt. Prevents the agent from hallucinating a different domain and navigating away mid-evaluation.
- **Click timeout recovery** — if a CSS selector isn't found within 10 seconds, the agent gets a feedback message and continues rather than crashing the run. Applies to both direct clicks and navigate-converted-to-click cases.
- **Selector blocklist** — `:contains()` is stripped from selectors before Playwright executes them. The model uses it despite being told not to; the blocklist handles it at the dispatch layer.
- **Post-response token budget check** — budget is checked both before and after each API call. Prevents overruns where a single step consumes more than the remaining budget.

---

## Known limitations

- Non-deterministic — outcomes can vary between runs. This is exploratory reasoning, not scripted regression.
- New tab links can't be followed — the agent operates in a single page context.
- Multi-step auth (OAuth, MFA) isn't supported — `--email` and `--password` work for standard login forms only.
- Heavily JS-driven pages with delayed rendering can cause the agent to act on stale state.
- The planner generates technically-valid but sometimes untestable goals (third-party widgets, async scripts) — worth reviewing before running a full suite.

---

## Tech stack

| | |
|---|---|
| **AI** | Claude API — Opus 4.5 (agent), Sonnet 4.6 (planner) |
| **Browser** | Playwright (Chromium) |
| **PDF** | ReportLab |
| **CI** | GitHub Actions |
| **Language** | Python 3.14 |
