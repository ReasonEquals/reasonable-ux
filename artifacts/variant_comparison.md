# Variant comparison

Four configurations of the multi-page suite, run across stripe / linear / glossier on 2026-04-28 → 2026-04-29.

| Variant | Site | Steps | Tokens | $ Cost | tok/step | CTA | Copy | Flow | Composite | Adv. calls |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v1_baseline | stripe | 36 | 348,512 | $1.41 | 9,681 | 2.83 | 3.56 | 2.47 | 2.95 | N/A |
| v1_baseline | linear | 41 | 385,812 | $1.54 | 9,410 | 1.73 | 3.68 | 2.56 | 2.66 | N/A |
| v1_baseline | glossier | 27 | 248,409 | $1.02 | 9,200 | 2.67 | 3.22 | 2.41 | 2.77 | N/A |
| v2_advisor | stripe | 42 | 511,060 | $1.94 | 12,168 | 2.93 | 3.19 | 2.79 | 2.97 | N/A |
| v2_advisor | linear | 34 | 431,846 | $1.59 | 12,701 | 2.09 | 3.53 | 2.71 | 2.77 | N/A |
| v2_advisor | glossier | 40 | 529,546 | $1.94 | 13,239 | 2.98 | 3.15 | 2.58 | 2.90 | N/A |
| v3_8step | stripe | 23 | 167,509 | $0.76 | 7,283 | 3.09 | 3.96 | 2.74 | 3.26 | N/A |
| v3_8step | linear | 27 | 184,932 | $0.82 | 6,849 | 1.78 | 3.81 | 2.63 | 2.74 | N/A |
| v3_8step | glossier | 18 | 110,267 | $0.52 | 6,126 | 2.11 | 2.00 | 1.89 | 2.00 | N/A |
| v4_8step_advisor | stripe | 29 | 296,197 | $1.15 | 10,214 | 2.62 | 3.34 | 2.69 | 2.89 | N/A |
| v4_8step_advisor | linear | 30 | 333,855 | $1.29 | 11,128 | 1.83 | 3.70 | 2.73 | 2.76 | N/A |
| v4_8step_advisor | glossier | 30 | 308,242 | $1.19 | 10,275 | 2.87 | 2.80 | 2.67 | 2.78 | N/A |

## Per-variant means (averaged across 3 sites)

| Variant | $ Cost | tok/step | Composite score |
|---|---:|---:|---:|
| v1_baseline | $1.32 | 9,430 | 2.79 |
| v2_advisor | $1.83 | 12,703 | 2.88 |
| v3_8step | $0.70 | 6,753 | 2.67 |
| v4_8step_advisor | $1.21 | 10,539 | 2.81 |

## Advisor invocation rate

*Data available from batch-70 forward; batch-68 baseline rows show N/A because `advisor_called_count` cannot be reconstructed from stored Langfuse traces.*

No runs in the current matrix carry advisor-fire data yet. Re-run v2_advisor / v4_8step_advisor variants after batch 70 lands to populate.
