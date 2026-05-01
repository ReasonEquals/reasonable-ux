"""Token-cost drift detection for reasonable-ux.

Reads cost_log.csv, groups rows by (url, run_type), and flags runs that deviate
more than the per-type threshold from the first-run baseline for that (url, run_type).

Baseline strategy: first chronological run per (url, run_type). Appropriate for
sparse data (few same-URL repeats). Upgrade to rolling median when 5+ same-type
runs accumulate.

Thresholds: calibrated against cost_log.csv (2026-05-01, 24 runs). Normal multi-page
variance: ±33%; single-page: <20%. Advisor variant runs intentionally exceed the multi
threshold (+90–110% per-step vs baseline) — drift warnings during variant experiments
are expected. Multi-page has inherently higher token variance (more pages, stagger
jitter) so uses a looser threshold. When both rows carry step_count > 0, drift is
measured on tokens/step rather than raw total — a run that spent more steps costs more
tokens, which is expected.
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

DRIFT_THRESHOLDS: dict[str, float] = {
    "single": 0.20,
    "multi": 0.30,
    "": 0.20,  # legacy / unknown run_type
}


def _threshold(run_type: str) -> float:
    return DRIFT_THRESHOLDS.get(run_type, DRIFT_THRESHOLDS[""])


def _per_step(row: dict) -> float:
    sc = row.get("step_count", 0)
    return row["total_tokens"] / sc if sc > 0 else float(row["total_tokens"])


def load_cost_log(path: str = "runs/cost_log.csv") -> list[dict]:
    """Return rows from cost_log.csv sorted ascending by timestamp."""
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({
                "timestamp": row["timestamp"],
                "url": row["url"],
                "run_type": row.get("run_type", ""),
                "input_tokens": int(row.get("input_tokens") or 0),
                "output_tokens": int(row.get("output_tokens") or 0),
                "total_tokens": int(row.get("total_tokens") or 0),
                "step_count": int(row.get("step_count") or 0),
            })
    return sorted(rows, key=lambda r: r["timestamp"])


def check_drift(
    url: str,
    run_type: str,
    current_tokens: int,
    log_path: str = "runs/cost_log.csv",
) -> str | None:
    """Check whether the most recent run for (url, run_type) has drifted beyond threshold.

    The current run must already be appended to the CSV before calling this.
    Prior history = all rows for (url, run_type) except the last one (the current run).
    Returns a warning string when drift is detected, None otherwise.
    """
    if not Path(log_path).exists():
        return None

    all_rows = [
        r for r in load_cost_log(log_path)
        if r["url"] == url and r["run_type"] == run_type
    ]

    if len(all_rows) <= 1:
        return None  # current run is the baseline; no comparison possible

    prior = all_rows[:-1]
    baseline_row = prior[0]
    current_row = all_rows[-1]
    threshold = _threshold(run_type)

    normalized = baseline_row.get("step_count", 0) > 0 and current_row.get("step_count", 0) > 0
    if normalized:
        baseline_val = _per_step(baseline_row)
        current_val = _per_step(current_row)
    else:
        baseline_val = float(baseline_row["total_tokens"])
        current_val = float(current_tokens)

    delta = (current_val - baseline_val) / baseline_val
    if abs(delta) <= threshold:
        return None

    direction = "+" if delta > 0 else ""
    if normalized:
        return (
            f"DRIFT: {url}  "
            f"current={current_val:,.0f}/step ({current_row['step_count']} steps)  "
            f"baseline={baseline_val:,.0f}/step ({baseline_row['step_count']} steps)  "
            f"({direction}{delta:.1%} vs baseline, threshold {threshold:.0%})"
        )
    return (
        f"DRIFT: {url}  current={int(current_val):,}  "
        f"baseline={int(baseline_val):,}  ({direction}{delta:.1%} vs baseline, "
        f"threshold {threshold:.0%})"
    )


def report(path: str = "runs/cost_log.csv") -> None:
    """Print a per-URL drift table to stdout."""
    if not Path(path).exists():
        print(f"No cost log found at {path}")
        return

    rows = load_cost_log(path)
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        by_key[(row["url"], row["run_type"])].append(row)

    for (url, run_type), runs in sorted(by_key.items()):
        print(f"\n{url}  [{run_type or 'unknown'}]")
        baseline_row = runs[0]
        threshold = _threshold(run_type)
        normalized = all(r.get("step_count", 0) > 0 for r in runs)
        for i, r in enumerate(runs):
            sc = r.get("step_count", 0)
            per_step_str = f"  {r['total_tokens'] // sc:,}/step" if sc > 0 else ""
            if i == 0:
                label = "(baseline)"
            else:
                if normalized:
                    baseline_val = _per_step(baseline_row)
                    current_val = _per_step(r)
                else:
                    baseline_val = float(baseline_row["total_tokens"])
                    current_val = float(r["total_tokens"])
                delta = (current_val - baseline_val) / baseline_val
                flag = "  DRIFT" if abs(delta) > threshold else ""
                direction = "+" if delta > 0 else ""
                label = f"({direction}{delta:.1%} vs baseline){flag}"
            print(f"  {r['timestamp']}  {r['total_tokens']:,} tokens{per_step_str}  {label}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "runs/cost_log.csv"
    report(path)
