> Snapshot taken 2026-04-26. Re-run: `source .venv/bin/activate && python evals/run_evals.py --limit 1`

# Eval results sample — linear.app

**Harness:** `evals/run_evals.py` · **Labels SHA:** `18e29ac` · **Steps:** 3 of 4 max (agent terminated early with `action: done`) · **Wall clock:** 82.5s

## Result: PASS

| Check | Expected | Actual | Status |
|---|---|---|---|
| Score band | [50, 75] | 62.2 / 100 (avg of 5-pt scores × 20) | ✅ |
| Persona keywords | any of: engineer, designer, product, manager, lead | "engineering manager" — hits `engineer` + `manager` | ✅ |
| Friction keywords | pricing, cta | step 1: "pricing signal above the fold"; step 2: "CTA visibility" in note | ✅ |
| Nav drift | nav: prefix only, no CSS selectors | step 1: `nav:Pricing`; step 2: `nav:Customers` | ✅ |

## Inferred persona (step 1)

Mid-market SaaS engineering manager evaluating project/issue tracking tools to replace Jira or Asana for a 20-50 person team.

## Per-step scores

| Step | Page | CTA clarity | Copy quality | Flow smoothness |
|---|---|---|---|---|
| 1 | / (homepage) | 3 / 5 | 4 / 5 | 4 / 5 |
| 2 | /pricing | 2 / 5 | 3 / 5 | 3 / 5 |
| 3 | /customers | 2 / 5 | 4 / 5 | 3 / 5 |

**Aggregate:** 62.2 / 100 — within expected band [50, 75]

## Top friction points (step 2 — pricing page)

- No CTA buttons visible in the hero viewport; a purchase-intent evaluator can't act without scrolling
- Yearly billing pre-toggled with no monthly price shown alongside, obscuring the true monthly cost for budget justification
- "All Basic features +" chaining forces mental reconstruction of the full Business tier feature set
