"""Tests for drift_report.py — load_cost_log and check_drift logic."""

import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# isort: split
from drift_report import check_drift, load_cost_log  # noqa: E402

_PROD_FIELDS = [
    "timestamp", "url", "run_type", "model",
    "input_tokens", "output_tokens", "total_tokens", "step_count",
    "langfuse_session_id", "langfuse_cost_usd",
]


def _write_csv(tmp_path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> Path:
    path = tmp_path / "cost_log.csv"
    fields = fieldnames or _PROD_FIELDS
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _row(timestamp: str, url: str, total: int, *, inp: int = 0, out: int = 0, run_type: str = "multi", step_count: int = 0) -> dict:
    """Build a 10-col row. Only fields drift_report reads need real values."""
    return {
        "timestamp": timestamp, "url": url, "run_type": run_type,
        "model": "", "input_tokens": inp, "output_tokens": out,
        "total_tokens": total, "step_count": step_count,
        "langfuse_session_id": "", "langfuse_cost_usd": "",
    }


def test_first_run_no_drift(tmp_path):
    """Single row for a URL → no comparison possible, returns None."""
    p = _write_csv(tmp_path, [
        {"timestamp": "2026-04-28T00:00:00", "url": "https://stripe.com",
         "run_type": "multi", "input_tokens": 200000, "output_tokens": 20000, "total_tokens": 220000},
    ])
    assert check_drift("https://stripe.com", "multi", 220000, str(p)) is None


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
    assert check_drift("https://stripe.com", "multi", current, str(p)) is None


def test_drift_over_threshold_positive(tmp_path):
    """Second run 25% higher than single baseline (0.20 threshold) → DRIFT with + direction."""
    baseline = 220000
    current = int(baseline * 1.25)
    p = _write_csv(tmp_path, [
        {"timestamp": "2026-04-28T00:00:00", "url": "https://stripe.com",
         "run_type": "single", "input_tokens": 180000, "output_tokens": 40000, "total_tokens": baseline},
        {"timestamp": "2026-04-28T01:00:00", "url": "https://stripe.com",
         "run_type": "single", "input_tokens": 225000, "output_tokens": 50000, "total_tokens": current},
    ])
    result = check_drift("https://stripe.com", "single", current, str(p))
    assert result is not None
    assert "DRIFT" in result
    assert "https://stripe.com" in result
    assert "+" in result


def test_drift_over_threshold_negative(tmp_path):
    """Second run 25% lower than single baseline → also flagged as drift."""
    baseline = 220000
    current = int(baseline * 0.75)
    p = _write_csv(tmp_path, [
        {"timestamp": "2026-04-28T00:00:00", "url": "https://stripe.com",
         "run_type": "single", "input_tokens": 180000, "output_tokens": 40000, "total_tokens": baseline},
        {"timestamp": "2026-04-28T01:00:00", "url": "https://stripe.com",
         "run_type": "single", "input_tokens": 135000, "output_tokens": 30000, "total_tokens": current},
    ])
    result = check_drift("https://stripe.com", "single", current, str(p))
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
    assert check_drift("https://linear.app", "multi", 280000, str(p)) is None


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


def test_round_trip_log_cost_to_load_cost_log(tmp_path, monkeypatch):
    """_log_cost writes a row that load_cost_log parses without column misalignment."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs").mkdir()
    run_dir = tmp_path / "runs" / "test_run"
    run_dir.mkdir()

    from run import _log_cost  # noqa: PLC0415

    _log_cost(
        run_dir,
        "https://example.com",
        "multi",
        {"input": 1234, "output": 567, "total": 1801, "step_count": 6},
        session_id="suite_20260428_999999",
        model="claude-sonnet-4-6",
    )

    rows = load_cost_log(str(tmp_path / "runs" / "cost_log.csv"))
    assert len(rows) == 1
    assert rows[0]["url"] == "https://example.com"
    assert rows[0]["run_type"] == "multi"
    assert rows[0]["input_tokens"] == 1234
    assert rows[0]["output_tokens"] == 567
    assert rows[0]["total_tokens"] == 1801
    assert rows[0]["step_count"] == 6


def test_log_cost_migrates_stale_header(tmp_path, monkeypatch):
    """Pre-existing 6-col CSV → _log_cost rewrites header, backfills legacy rows, appends new row."""
    monkeypatch.chdir(tmp_path)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    log_path = runs_dir / "cost_log.csv"

    legacy_fields = ["timestamp", "url", "run_type", "input_tokens", "output_tokens", "total_tokens"]
    with open(log_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=legacy_fields)
        writer.writeheader()
        writer.writerow({
            "timestamp": "2026-04-27T00:00:00", "url": "https://legacy.test", "run_type": "multi",
            "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
        })

    run_dir = runs_dir / "new_run"
    run_dir.mkdir()
    from run import _log_cost  # noqa: PLC0415
    _log_cost(
        run_dir,
        "https://new.test",
        "multi",
        {"input": 200, "output": 80, "total": 280, "step_count": 4},
        session_id="suite_test",
        model="claude-sonnet-4-6",
    )

    with open(log_path, newline="") as f:
        rows_raw = list(csv.reader(f))

    assert rows_raw[0] == _PROD_FIELDS  # header rewritten
    assert len(rows_raw) == 3  # header + legacy + new
    legacy = dict(zip(_PROD_FIELDS, rows_raw[1], strict=True))
    assert legacy["url"] == "https://legacy.test"
    assert legacy["input_tokens"] == "100"
    assert legacy["model"] == ""  # backfilled empty
    assert legacy["step_count"] == ""  # backfilled empty
    assert legacy["langfuse_session_id"] == ""
    assert legacy["langfuse_cost_usd"] == ""
    new = dict(zip(_PROD_FIELDS, rows_raw[2], strict=True))
    assert new["url"] == "https://new.test"
    assert new["model"] == "claude-sonnet-4-6"
    assert new["input_tokens"] == "200"
    assert new["step_count"] == "4"
    assert new["langfuse_session_id"] == "suite_test"


def test_log_cost_refuses_mixed_schema_file(tmp_path, monkeypatch):
    """Header narrower than data rows → _log_cost raises rather than entrenching misalignment."""
    monkeypatch.chdir(tmp_path)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    log_path = runs_dir / "cost_log.csv"

    # 6-col header + 1 row written with 9 fields — the v1/v2 corruption shape.
    log_path.write_text(
        "timestamp,url,run_type,input_tokens,output_tokens,total_tokens\n"
        "2026-04-28T20:42:00,https://stripe.com,multi,claude-sonnet-4-6,23239,4983,28222,suite_x,0.19\n",
        encoding="utf-8",
    )

    run_dir = runs_dir / "new_run"
    run_dir.mkdir()
    import pytest  # noqa: PLC0415

    from run import _log_cost  # noqa: PLC0415

    with pytest.raises(RuntimeError, match="schema-version skew"):
        _log_cost(
            run_dir, "https://x.test", "multi",
            {"input": 1, "output": 1, "total": 2},
            session_id="s", model="m",
        )


def test_load_cost_log_tolerates_missing_columns(tmp_path):
    """CSV missing optional token columns → load_cost_log returns rows with int defaults, no crash."""
    minimal_fields = ["timestamp", "url", "total_tokens"]
    p = _write_csv(
        tmp_path,
        [{"timestamp": "2026-04-28T00:00:00", "url": "https://x.test", "total_tokens": 100}],
        fieldnames=minimal_fields,
    )
    rows = load_cost_log(str(p))
    assert len(rows) == 1
    assert rows[0]["total_tokens"] == 100
    assert rows[0]["input_tokens"] == 0
    assert rows[0]["output_tokens"] == 0
    assert rows[0]["run_type"] == ""


def test_run_type_scoping_no_cross_contamination(tmp_path):
    """Same URL, single and multi rows — check_drift scopes by run_type, ignores other types."""
    p = _write_csv(tmp_path, [
        _row("2026-04-28T00:00:00", "https://stripe.com", 50000, run_type="single"),
        _row("2026-04-28T01:00:00", "https://stripe.com", 200000, run_type="multi"),
        _row("2026-04-28T02:00:00", "https://stripe.com", 220000, run_type="multi"),
    ])
    # multi baseline=200000; 220000 is +10%, within 30% threshold → no drift
    assert check_drift("https://stripe.com", "multi", 220000, str(p)) is None
    # single has only 1 row → baseline only, no comparison
    assert check_drift("https://stripe.com", "single", 50000, str(p)) is None


def test_per_type_threshold_single_vs_multi(tmp_path):
    """Single uses 0.20 threshold; multi uses 0.30 — same +25% delta treated differently."""
    baseline = 100000
    current = int(baseline * 1.25)  # +25%

    s_dir = tmp_path / "s"
    s_dir.mkdir()
    m_dir = tmp_path / "m"
    m_dir.mkdir()
    p_single = _write_csv(s_dir, [
        _row("2026-04-28T00:00:00", "https://x.test", baseline, run_type="single"),
        _row("2026-04-28T01:00:00", "https://x.test", current, run_type="single"),
    ])
    p_multi = _write_csv(m_dir, [
        _row("2026-04-28T00:00:00", "https://x.test", baseline, run_type="multi"),
        _row("2026-04-28T01:00:00", "https://x.test", current, run_type="multi"),
    ])

    # single: 25% > 20% threshold → DRIFT
    assert check_drift("https://x.test", "single", current, str(p_single)) is not None
    # multi: 25% < 30% threshold → no drift
    assert check_drift("https://x.test", "multi", current, str(p_multi)) is None


def test_step_normalization_eliminates_false_positive(tmp_path):
    """4-step baseline at 50k/step vs 8-step run at 50k/step — raw +100% but per-step 0% → no drift."""
    p = _write_csv(tmp_path, [
        _row("2026-04-29T00:00:00", "https://x.test", 200000, run_type="single", step_count=4),
        _row("2026-04-29T01:00:00", "https://x.test", 400000, run_type="single", step_count=8),
    ])
    # Raw comparison would flag +100% (> 20% threshold); per-step is 0% → no drift.
    assert check_drift("https://x.test", "single", 400000, str(p)) is None


def test_step_normalization_catches_per_step_regression(tmp_path):
    """Same step count, 30% more tokens per step → DRIFT flagged."""
    baseline_total = 200000
    current_total = int(baseline_total * 1.30) + 1  # just over 20% threshold
    p = _write_csv(tmp_path, [
        _row("2026-04-29T00:00:00", "https://x.test", baseline_total, run_type="single", step_count=4),
        _row("2026-04-29T01:00:00", "https://x.test", current_total, run_type="single", step_count=4),
    ])
    result = check_drift("https://x.test", "single", current_total, str(p))
    assert result is not None
    assert "DRIFT" in result
    assert "/step" in result  # normalized message format
