"""Pinning tests for build_index.py — dual-path iteration over runs/.

Scope: ensure the index writer recurses domain folders for nested-layout runs
(current production layout) AND still picks up flat-layout runs at the top
level (defensive). Suite folders without suite_report.html should still be
indexed when a *_multi_*.pdf or cost_summary.json is present.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# isort: split
from build_index import main as build_index_main  # noqa: E402


def _write_report(path: Path, status: str = "pass", verdict: str = "ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([{"step": 1, "pass_fail": status, "verdict": verdict}]))


def test_nested_domain_layout_is_indexed(tmp_path):
    runs = tmp_path / "runs"
    _write_report(runs / "stripe_com" / "2026-04-22_1925_single_page" / "report.json")
    _write_report(runs / "linear_app" / "2026-04-25_2233_single_page" / "report.json", status="fail")

    build_index_main(runs)

    idx = json.loads((runs / "index.json").read_text())
    assert len(idx) == 2
    run_ids = {r["run_id"] for r in idx}
    assert "stripe_com/2026-04-22_1925" in run_ids
    assert "linear_app/2026-04-25_2233" in run_ids
    assert all(r["test_name"] == "single_page" for r in idx)


def test_flat_top_level_layout_is_indexed(tmp_path):
    runs = tmp_path / "runs"
    _write_report(runs / "20260101_1200_some_test" / "report.json")

    build_index_main(runs)

    idx = json.loads((runs / "index.json").read_text())
    assert len(idx) == 1
    assert idx[0]["run_id"] == "20260101_1200"
    assert idx[0]["test_name"] == "some_test"


def test_suite_with_pdf_is_indexed_without_suite_report_html(tmp_path):
    """Suite folder has PDF + cost_summary.json; page dirs live in domain subfolder (current layout)."""
    runs = tmp_path / "runs"
    suite = runs / "suite_20260428_212327"
    suite.mkdir(parents=True)
    (suite / "stripe_com_2026-04-28_213442_multi_full_editorial.pdf").write_bytes(b"%PDF-")
    # Page dir timestamped within 30min of suite start (suite=21:23:27 → page=21:24:00 ✓)
    _write_report(runs / "stripe_com" / "2026-04-28_212400_single_page" / "report.json")

    build_index_main(runs)

    suite_idx = json.loads((runs / "suite_index.json").read_text())
    assert len(suite_idx) == 1
    assert suite_idx[0]["suite_id"] == "20260428_212327"
    assert suite_idx[0]["total"] == 1
    assert suite_idx[0]["passed"] == 1
    assert suite_idx[0]["html_path"].endswith("_multi_full_editorial.pdf")


def test_suite_token_sum_from_page_dirs(tmp_path):
    """Suite total_tokens is summed from per-page cost_summary.json, not the suite-level file."""
    runs = tmp_path / "runs"
    suite = runs / "suite_20260430_120000"
    suite.mkdir(parents=True)
    # Suite-level cost_summary.json — should be overridden by per-page sum
    (suite / "cost_summary.json").write_text(json.dumps({"total_tokens": 999}))
    # Two page dirs within the 30-min window (suite=12:00:00 → pages at 12:00:05 and 12:00:10)
    for ts, tokens in [("2026-04-30_120005", 1000), ("2026-04-30_120010", 2000)]:
        page_dir = runs / "fake_domain" / f"{ts}_single_page"
        _write_report(page_dir / "report.json")
        (page_dir / "cost_summary.json").write_text(json.dumps({"total_tokens": tokens}))

    build_index_main(runs)

    suite_idx = json.loads((runs / "suite_index.json").read_text())
    assert len(suite_idx) == 1
    assert suite_idx[0]["total_tokens"] == 3000  # 1000 + 2000, not 999
    assert suite_idx[0]["total"] == 2
