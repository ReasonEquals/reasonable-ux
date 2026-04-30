# Variant judge — LLM-as-judge pairwise comparison

Champion: `v1_baseline`. Challengers: `v2_advisor`, `v3_8step`, `v4_8step_advisor`. Judge: `claude-opus-4-7`. Rubric pre-registered at [variant_judge_rubric.md](variant_judge_rubric.md).

Total cost: $0.58 across 17 judge calls (9 pairs, N=1 baseline + adaptive N=3 on close-call pairs).

## Verdicts

| Site | Challenger | N | Specificity | Actionability | Coverage | Persona fidelity | Overall |
|---|---|---:|---|---|---|---|---|
| stripe | v2_advisor | 3 | **v2_advisor** | **v2_advisor** | **v1_baseline** | **v2_advisor** | **v2_advisor** |
| stripe | v3_8step | 3 | **v3_8step** | tie | **v3_8step** | **v1_baseline** | **v3_8step** |
| stripe | v4_8step_advisor | 1 | **v4_8step_advisor** | **v4_8step_advisor** | **v4_8step_advisor** | **v4_8step_advisor** | **v4_8step_advisor** |
| linear | v2_advisor | 1 | **v1_baseline** | **v1_baseline** | **v1_baseline** | **v1_baseline** | **v1_baseline** |
| linear | v3_8step | 3 | **v3_8step** | **v1_baseline** | tie | **v3_8step** | **v3_8step** |
| linear | v4_8step_advisor | 1 | **v1_baseline** | **v1_baseline** | **v1_baseline** | **v1_baseline** | **v1_baseline** |
| glossier | v2_advisor | 3 | **v2_advisor** | **v2_advisor** | **v1_baseline** | **v1_baseline** | **v1_baseline** |
| glossier | v3_8step | 1 | **v1_baseline** | **v1_baseline** | **v1_baseline** | **v1_baseline** | **v1_baseline** |
| glossier | v4_8step_advisor | 1 | **v4_8step_advisor** | **v4_8step_advisor** | **v4_8step_advisor** | **v1_baseline** | **v4_8step_advisor** |

## Narrative

Per-challenger dimension win counts vs `v1_baseline` (3 sites each):

- **v2_advisor** (3 sites):
  - Specificity: 2W / 1L / 0T
  - Actionability: 2W / 1L / 0T
  - Coverage: 0W / 3L / 0T
  - Persona fidelity: 1W / 2L / 0T
  - **Overall: 1W / 2L / 0T**

- **v3_8step** (3 sites):
  - Specificity: 2W / 1L / 0T
  - Actionability: 0W / 2L / 1T
  - Coverage: 1W / 1L / 1T
  - Persona fidelity: 1W / 2L / 0T
  - **Overall: 2W / 1L / 0T**

- **v4_8step_advisor** (3 sites):
  - Specificity: 2W / 1L / 0T
  - Actionability: 2W / 1L / 0T
  - Coverage: 2W / 1L / 0T
  - Persona fidelity: 1W / 2L / 0T
  - **Overall: 2W / 1L / 0T**

## Pre-registered hypothesis

> Advisor-on variants (v2, v4) produce more specific friction points and more actionable recommendations than baselines (v1, v3), but the cost premium overprices the gap relative to v1_baseline.

See `variant_judge.json` for raw per-call records (verdicts, reasons, token counts, costs).
