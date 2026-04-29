"""Token-cost drift detection for reasonable-ux.

Reads cost_log.csv, groups rows by (url, run_type), and flags runs that deviate
more than the per-type threshold from the first-run baseline for that (url, run_type).

Baseline strategy: first chronological run per (url, run_type). Appropriate for
sparse data (few same-URL repeats). Upgrade to rolling median when 5+ same-type
runs accumulate.

Thresholds: per-type, uncalibrated placeholders. Multi-page runs have inherently
higher token variance (more pages, stagger jitter) so use a looser threshold.
Per-step normalization (total_tokens / step_count) would be more precise but
requires a cost_log.csv schema change; deferred until step_count is tracked.
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
    baseline = prior[0]["total_tokens"]
    threshold = _threshold(run_type)

    delta = (current_tokens - baseline) / baseline
    if abs(delta) <= threshold:
        return None

    direction = "+" if delta > 0 else ""
    return (
        f"DRIFT: {url}  current={current_tokens:,}  "
        f"baseline={baseline:,}  ({direction}{delta:.1%} vs baseline, "
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
        baseline = runs[0]["total_tokens"]
        threshold = _threshold(run_type)
        for i, r in enumerate(runs):
            if i == 0:
                label = "(baseline)"
            else:
                delta = (r["total_tokens"] - baseline) / baseline
                flag = "  DRIFT" if abs(delta) > threshold else ""
                direction = "+" if delta > 0 else ""
                label = f"({direction}{delta:.1%} vs baseline){flag}"
            print(f"  {r['timestamp']}  {r['total_tokens']:,} tokens  {label}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "runs/cost_log.csv"
    report(path)
