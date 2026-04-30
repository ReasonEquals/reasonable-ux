# LLM-Judge Rubric — reasonable-ux variant comparison

**Pre-registered 2026-04-29.** Locked before first judge call. Iteration is allowed only after the human-kappa pilot run (see `variant_judge_human_labels.md`); any change after the full N=1 sweep starts is logged as a new dated entry in `DECISIONS.md` and is presumed to be reverse-pre-registration unless rebutted in writing.

## Hypothesis (loosely held)

Advisor-on variants (v2, v4) produce more specific friction points and more actionable recommendations than baselines (v1, v3), but the cost premium overprices the gap relative to v1_baseline.

## Pairwise design

- Champion: `v1_baseline`
- Challengers: `v2_advisor`, `v3_8step`, `v4_8step_advisor`
- 9 pairs (3 sites × 3 challengers): stripe, linear, glossier
- A/B labels are randomized per call; the script remaps verdicts to (champion | challenger) after parsing
- N=1 default; pairs flagged "close call" (any dimension verdict is `tie` OR overall winner contradicts the majority of dimension winners) are re-run at N=3 within remaining cost budget

## Dimensions

### 1. Specificity
Friction points name concrete UI elements, specific copy phrases, or particular interaction flows — not vague platitudes.

- **High:** "The 'Contact sales' CTA lacks a self-serve demo path for enterprise buyers in research mode."
- **Low:** "The CTA could be clearer."

### 2. Actionability
Recommendations are implementable as a single design change a PM could ship in one sprint.

- **High:** "Add a secondary 'Request a demo' CTA below the hero to give research-phase buyers a lower-commitment entry point."
- **Low:** "Improve the onboarding experience."

### 3. Coverage
Report hits PM-priority surfaces (conversion-critical flows, above/below-fold gaps, navigation dead-ends), **normalized to opportunity, not raw step count.** A 4-step run that finds the conversion-killer in step 2 has higher coverage than an 8-step run wandering through `/about` pages.

- **High:** Navigates to pricing and signup, finds friction at the conversion step, notes below-fold trust signals — regardless of step budget.
- **Low:** Reports on font or color without conversion or persona context; or burns step budget on low-priority surfaces (about, careers, footer links) when conversion-critical pages are unvisited.

### 4. Persona fidelity
Findings are filtered through the inferred persona's perspective and priorities — not generic "user" framing, and not just *mentioning* the persona once before drifting.

- **High:** "A VP of Engineering evaluating enterprise pricing would need SLA guarantees above the fold; this page buries them."
- **Low:** Mentions the persona in framing but findings would read identically for any visitor — no role-specific stakes, priorities, or objections show through.

## Scoring schema

For each dimension, the judge returns: `winner` ∈ {`A`, `B`, `tie`} + one-sentence reason.

`tie` is only allowed when the reason explicitly names a "negligible" or "marginal" gap — a forced-tie escape hatch is not allowed. If the judge cannot articulate why the gap is negligible, it must declare a winner.

Overall verdict: same schema, holistic judgment.

## Inputs

The judge reads text-only excerpts from each variant's frozen evaluation corpus at `evals/variant_corpus/{variant}/{site}.json`. Per step it sees: `verdict`, `friction_points`, `recommendations`, `cta_clarity.note`, `copy_quality.note`, `flow_smoothness.note`, `persona` (step 1). It does NOT see screenshots, token counts, URLs, or pass/fail flags. Per-variant input is capped at ~5000 chars (truncated at step boundary, never mid-step).

## Model

`claude-opus-4-7`. Hard cost cap: $5.00 across all calls. Pre-call budget check; partial results written if cap reached.
