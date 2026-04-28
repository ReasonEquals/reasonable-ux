"""Tests for drift_report.py — load_cost_log and check_drift logic."""

import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# isort: split
from drift_report import check_drift, load_cost_log  # noqa: E402


def _write_csv(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "cost_log.csv"
    fieldnames = ["timestamp", "url", "run_type", "input_tokens", "output_tokens", "total_tokens"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_first_run_no_drift(tmp_path):
    """Single row for a URL → no comparison possible, returns None."""
    p = _write_csv(tmp_path, [
        {"timestamp": "2026-04-28T00:00:00", "url": "https://stripe.com",
         "run_type": "multi", "input_tokens": 200000, "output_tokens": 20000, "total_tokens": 220000},
    ])
    assert check_drift("https://stripe.com", 220000, str(p)) is None


def test_within_threshold(tmp_path):
    """Second run 15% higher than baseline → within 20% threshold, returns None."""
    baseline = 220000
    current = int(baseline * 1.15)
    p = _write_csv(tmp_path, [
        {"timestamp": "2026-04-28T00:00:00", "url": "https://stripe.com",
         "run_type": "multi", "input_tokens": 180000, "output_tokens": 40000, "total_tokens": baseline},
        {"timestamp": "2026-04-28T01:00:00", "url": "https://stripe.com",
         "run_type": "multi", "input_tokens": 207000, "output_tokens": 46000, "total_tokens": current},
    ])
    assert check_drift("https://stripe.com", current, str(p)) is None


def test_drift_over_threshold_positive(tmp_path):
    """Second run 25% higher than baseline → exceeds threshold, returns warning string."""
    baseline = 220000
    current = int(baseline * 1.25)
    p = _write_csv(tmp_path, [
        {"timestamp": "2026-04-28T00:00:00", "url": "https://stripe.com",
         "run_type": "multi", "input_tokens": 180000, "output_tokens": 40000, "total_tokens": baseline},
        {"timestamp": "2026-04-28T01:00:00", "url": "https://stripe.com",
         "run_type": "multi", "input_tokens": 225000, "output_tokens": 50000, "total_tokens": current},
    ])
    result = check_drift("https://stripe.com", current, str(p))
    assert result is not None
    assert "DRIFT" in result
    assert "https://stripe.com" in result
    assert "+" in result


def test_drift_over_threshold_negative(tmp_path):
    """Second run 25% lower than baseline → also flagged as drift."""
    baseline = 220000
    current = int(baseline * 0.75)
    p = _write_csv(tmp_path, [
        {"timestamp": "2026-04-28T00:00:00", "url": "https://stripe.com",
         "run_type": "multi", "input_tokens": 180000, "output_tokens": 40000, "total_tokens": baseline},
        {"timestamp": "2026-04-28T01:00:00", "url": "https://stripe.com",
         "run_type": "multi", "input_tokens": 135000, "output_tokens": 30000, "total_tokens": current},
    ])
    result = check_drift("https://stripe.com", current, str(p))
    assert result is not None
    assert "DRIFT" in result
    assert "-" in result


def test_different_url_ignored(tmp_path):
    """Two rows for URL-A, one row for URL-B → URL-B returns None (first run is baseline)."""
    p = _write_csv(tmp_path, [
        {"timestamp": "2026-04-28T00:00:00", "url": "https://stripe.com",
         "run_type": "multi", "input_tokens": 180000, "output_tokens": 40000, "total_tokens": 220000},
        {"timestamp": "2026-04-28T00:30:00", "url": "https://stripe.com",
         "run_type": "multi", "input_tokens": 225000, "output_tokens": 50000, "total_tokens": 275000},
        {"timestamp": "2026-04-28T01:00:00", "url": "https://linear.app",
         "run_type": "multi", "input_tokens": 250000, "output_tokens": 30000, "total_tokens": 280000},
    ])
    # linear.app has only 1 row → no comparison
    assert check_drift("https://linear.app", 280000, str(p)) is None


def test_load_cost_log_sorts_by_timestamp(tmp_path):
    """Out-of-order CSV rows → load_cost_log returns them sorted ascending by timestamp."""
    p = _write_csv(tmp_path, [
        {"timestamp": "2026-04-28T02:00:00", "url": "https://stripe.com",
         "run_type": "multi", "input_tokens": 200000, "output_tokens": 20000, "total_tokens": 220000},
        {"timestamp": "2026-04-28T00:00:00", "url": "https://stripe.com",
         "run_type": "multi", "input_tokens": 180000, "output_tokens": 40000, "total_tokens": 220000},
        {"timestamp": "2026-04-28T01:00:00", "url": "https://stripe.com",
         "run_type": "multi", "input_tokens": 190000, "output_tokens": 30000, "total_tokens": 220000},
    ])
    rows = load_cost_log(str(p))
    timestamps = [r["timestamp"] for r in rows]
    assert timestamps == sorted(timestamps)
