# Variant judge — human-labeled calibration pilot

Ryan hand-labels 3-4 pilot pairs against the same rubric used by the LLM judge before locking the rubric. The point is calibration: do the dimensions discriminate when scored by a human reading the same corpus? If yes, the LLM judge is measuring something real. If the human and the judge disagree on >50% of any dimension, that anchor is fuzzy and gets sharpened (with a dated entry in `DECISIONS.md`) before the full 9-pair run.

**This is the only legitimate window for rubric edits.** Once the full N=1 sweep starts, the rubric is frozen.

## How to label

1. Read [`artifacts/variant_judge_rubric.md`](variant_judge_rubric.md) — the 4 dimensions and their high/low anchors.
2. For each pilot pair below, read the corpus text for both variants from `evals/variant_corpus/{variant}/{site}.json`. Same input the judge sees: verdict, friction_points, recommendations, scoring notes, persona.
3. Per dimension, pick a winner (`v1_baseline` | `<challenger>` | `tie`) and write a one-sentence reason. "Tie" requires the reason to name the gap as "negligible" or "marginal" — same rule as the judge.
4. Write an overall verdict + reason.
5. After the pilot judge call lands, fill in the agreement column (per dimension: ✓ or ✗ vs judge verdict).

## Pilot pairs

> Suggested coverage: one site per challenger to stress-test all 4 dimensions across the 3 verticals. Add a 4th pair if any dimension feels under-tested.

### Pair 1 — `stripe`, `v1_baseline` vs `v2_advisor`

| Dimension | Ryan's winner | Reason | Judge winner | Agree? |
|---|---|---|---|---|
| Specificity     |  |  |  |  |
| Actionability   |  |  |  |  |
| Coverage        |  |  |  |  |
| Persona fidelity|  |  |  |  |
| **Overall**     |  |  |  |  |

### Pair 2 — `linear`, `v1_baseline` vs `v3_8step`

| Dimension | Ryan's winner | Reason | Judge winner | Agree? |
|---|---|---|---|---|
| Specificity     |  |  |  |  |
| Actionability   |  |  |  |  |
| Coverage        |  |  |  |  |
| Persona fidelity|  |  |  |  |
| **Overall**     |  |  |  |  |

### Pair 3 — `glossier`, `v1_baseline` vs `v4_8step_advisor`

| Dimension | Ryan's winner | Reason | Judge winner | Agree? |
|---|---|---|---|---|
| Specificity     |  |  |  |  |
| Actionability   |  |  |  |  |
| Coverage        |  |  |  |  |
| Persona fidelity|  |  |  |  |
| **Overall**     |  |  |  |  |

### Pair 4 — *(optional, fill if any dimension under-tested)*

| Dimension | Ryan's winner | Reason | Judge winner | Agree? |
|---|---|---|---|---|

## Calibration outcome

> Fill after pilot judge calls land.

- Per-dimension agreement rate: Specificity __/N · Actionability __/N · Coverage __/N · Persona fidelity __/N · Overall __/N
- Cohen's kappa (optional, computed via `from sklearn.metrics import cohen_kappa_score` on per-dimension labels)
- Decision: **Lock** rubric and proceed to full 9-pair run, OR **iterate** the anchor on dimension(s) ___ before re-running the pilot.
- If iterated: link to the dated `DECISIONS.md` sub-entry describing the change.
