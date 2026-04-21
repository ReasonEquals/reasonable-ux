# Eval harness baselines

Preserved `manifest.json` snapshots from historical eval runs. Live runs land
in `eval_runs/` (gitignored); this folder is the committed record that
survives across machines.

## 2026-04-20_pre-calibration.json

First full baseline after the Phase 1 eval harness landed (batch 23) and
pre-flight + identifiable UA hardening (batch 26). Ran against `labels.jsonl`
at SHA `a96d80b2…` — the first label set drafted before any real pass-rate
data existed.

**Headline: 10 / 19 passed (52.6%)**, 1 skipped (HTTP 403 pre-flight).

Diagnosing the nine failures showed most were **label bugs**, not agent
weakness:

- `stripe.com` — friction keyword `docs|api` never appeared; Stripe's homepage
  friction is all pricing/enterprise/volume. Category-based keyword guess, not
  grounded in what the agent actually surfaces.
- `nytimes.com` — friction keyword `subscribe` is not a substring of
  `subscription`. Pure substring-matching bug.
- `arstechnica.com` — same `subscribe`/`subscription` mismatch + bare `ads`
  substring was too noisy to keep even if it had matched.
- `allbirds.com`, `casper.com`, `bombas.com` — persona keyword lists built
  around generic retail terms (`shopper|customer|buyer`), but the agent
  consistently infers richer demographic personas (`millennial`,
  `health-conscious`, `consumer`). The old keywords guarded the wrong shape.
- `notion.so`, `webflow.com`, `nytimes.com` — score bands set too tight
  around guesses, not observed scores.

Batch 28 (post-calibration) split these into **label bug fixes** (persona +
friction keyword corrections) and **band calibration** (±~12 around observed
scores). The resulting pass-rate becomes the trustworthy reference point for
the Phase 2 LiteLLM regression check (±5% threshold).

This file is kept as a portfolio artifact: it documents the eval harness
catching its own label bugs before the first real model-swap regression
test, which is the point of having an eval harness in the first place.
