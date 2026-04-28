"""Token-cost drift detection for reasonable-ux.

Reads cost_log.csv, groups rows by URL, and flags runs that deviate more than
DRIFT_THRESHOLD from the first-run baseline for that URL.

Baseline strategy: first chronological run per URL. Appropriate for sparse
data (few same-URL repeats). Upgrade to rolling median when 5+ same-URL runs
accumulate.

Threshold: 20% — uncalibrated placeholder. Per-step normalization
(total_tokens / step_count) would be more precise but requires a cost_log.csv
schema change; deferred until step_count is tracked.
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

DRIFT_THRESHOLD = 0.20  # 20% — placeholder, not empirically calibrated


def load_cost_log(path: str = "runs/cost_log.csv") -> list[dict]:
    """Return rows from cost_log.csv sorted ascending by timestamp."""
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({
                "timestamp": row["timestamp"],
                "url": row["url"],
                "run_type": row.get("run_type", ""),
                "input_tokens": int(row["input_tokens"]),
                "output_tokens": int(row["output_tokens"]),
                "total_tokens": int(row["total_tokens"]),
            })
    return sorted(rows, key=lambda r: r["timestamp"])


def check_drift(
    url: str,
    current_tokens: int,
    log_path: str = "runs/cost_log.csv",
) -> str | None:
    """Check whether the most recent run for url has drifted beyond DRIFT_THRESHOLD.

    The current run must already be appended to the CSV before calling this.
    Prior history = all rows for url except the last one (the current run).
    Returns a warning string when drift is detected, None otherwise.
    """
    if not Path(log_path).exists():
        return None

    all_rows = [r for r in load_cost_log(log_path) if r["url"] == url]

    if len(all_rows) <= 1:
        return None  # current run is the baseline; no comparison possible

    prior = all_rows[:-1]
    baseline = prior[0]["total_tokens"]

    delta = (current_tokens - baseline) / baseline
    if abs(delta) <= DRIFT_THRESHOLD:
        return None

    direction = "+" if delta > 0 else ""
    return (
        f"DRIFT: {url}  current={current_tokens:,}  "
        f"baseline={baseline:,}  ({direction}{delta:.1%} vs baseline, "
        f"threshold {DRIFT_THRESHOLD:.0%})"
    )


def report(path: str = "runs/cost_log.csv") -> None:
    """Print a per-URL drift table to stdout."""
    if not Path(path).exists():
        print(f"No cost log found at {path}")
        return

    rows = load_cost_log(path)
    by_url: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_url[row["url"]].append(row)

    for url, runs in sorted(by_url.items()):
        print(f"\n{url}")
        baseline = runs[0]["total_tokens"]
        for i, r in enumerate(runs):
            if i == 0:
                label = "(baseline)"
            else:
                delta = (r["total_tokens"] - baseline) / baseline
                flag = "  DRIFT" if abs(delta) > DRIFT_THRESHOLD else ""
                direction = "+" if delta > 0 else ""
                label = f"({direction}{delta:.1%} vs baseline){flag}"
            print(f"  {r['timestamp']}  {r['total_tokens']:,} tokens  {label}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "runs/cost_log.csv"
    report(path)
