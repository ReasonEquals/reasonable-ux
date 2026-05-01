"""Tests for _log_cost cost_summary.json output.

CSV path is already covered by test_drift_report.py. These tests verify the JSON
artifact written to run_dir/cost_summary.json and confirm CSV rows are written
alongside it — using session_id=None to skip the Langfuse call entirely.
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from run import _log_cost


def _tokens(
    input=100, output=50, total=150, step_count=4, advisor_called_count=0, advisor_eligible_steps=0
):
    return {
        "input": input,
        "output": output,
        "total": total,
        "step_count": step_count,
        "advisor_called_count": advisor_called_count,
        "advisor_eligible_steps": advisor_eligible_steps,
    }


def test_writes_cost_summary_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs").mkdir()
    run_dir = tmp_path / "runs" / "test_run"
    run_dir.mkdir()

    _log_cost(run_dir, "https://example.com", "single", _tokens(), session_id=None, model="claude-test")

    summary_path = run_dir / "cost_summary.json"
    assert summary_path.exists()
    data = json.loads(summary_path.read_text())

    assert data["url"] == "https://example.com"
    assert data["run_type"] == "single"
    assert data["model"] == "claude-test"
    assert data["input_tokens"] == 100
    assert data["output_tokens"] == 50
    assert data["total_tokens"] == 150
    assert data["step_count"] == 4
    assert data["langfuse_session_id"] == ""
    assert data["langfuse_cost_usd"] is None
    assert data["advisor_called_count"] == 0
    assert data["advisor_eligible_steps"] == 0
    assert "timestamp" in data


def test_summary_advisor_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs").mkdir()
    run_dir = tmp_path / "runs" / "test_run"
    run_dir.mkdir()

    tokens = _tokens(advisor_called_count=3, advisor_eligible_steps=7)
    _log_cost(run_dir, "https://example.com", "multi", tokens, session_id=None)

    data = json.loads((run_dir / "cost_summary.json").read_text())
    assert data["advisor_called_count"] == 3
    assert data["advisor_eligible_steps"] == 7


def test_summary_no_langfuse_cost(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs").mkdir()
    run_dir = tmp_path / "runs" / "test_run"
    run_dir.mkdir()

    _log_cost(run_dir, "https://example.com", "single", _tokens(), session_id=None)

    data = json.loads((run_dir / "cost_summary.json").read_text())
    assert data["langfuse_cost_usd"] is None
    assert data["langfuse_session_id"] == ""


def test_summary_model_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs").mkdir()
    run_dir = tmp_path / "runs" / "test_run"
    run_dir.mkdir()

    _log_cost(run_dir, "https://example.com", "single", _tokens())

    data = json.loads((run_dir / "cost_summary.json").read_text())
    assert data["model"] == "unknown"


def test_summary_csv_row_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs").mkdir()
    run_dir = tmp_path / "runs" / "test_run"
    run_dir.mkdir()

    _log_cost(run_dir, "https://example.com/pricing", "single", _tokens(), session_id=None, model="haiku")

    log_path = tmp_path / "runs" / "cost_log.csv"
    assert log_path.exists()
    rows = list(csv.DictReader(log_path.open()))
    assert len(rows) == 1
    assert rows[0]["url"] == "https://example.com/pricing"
    assert rows[0]["run_type"] == "single"
    assert rows[0]["model"] == "haiku"
