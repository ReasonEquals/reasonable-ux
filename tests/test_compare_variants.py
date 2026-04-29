"""Tests for compare_variants.py — pure aggregation + mapping logic."""

import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# isort: split
from compare_variants import (  # noqa: E402
    SUITE_VARIANTS,
    VariantRow,
    aggregate_scores,
    build_markdown,
    build_rows,
    find_page_dirs,
)


def _write_report(page_dir: Path, steps: list[dict]) -> None:
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "report.json").write_text(json.dumps(steps))


def _step(cta: int, copy: int, flow: int, *, persona: str = "") -> dict:
    out = {
        "step": 1,
        "cta_clarity": {"score": cta, "note": ""},
        "copy_quality": {"score": copy, "note": ""},
        "flow_smoothness": {"score": flow, "note": ""},
    }
    if persona:
        out["persona"] = persona
    return out


def test_find_page_dirs_matches_window(tmp_path):
    runs = tmp_path / "runs"
    domain = runs / "stripe_com"
    domain.mkdir(parents=True)
    # In-window page dirs (suite at 21:23:27, page dirs at 21:23:28 / 21:25 / 21:28)
    for stamp in ("2026-04-28_212328", "2026-04-28_212500", "2026-04-28_212834"):
        (domain / f"{stamp}_single_page").mkdir()
    # Out-of-window: a page dir from the NEXT suite (21:55, beyond 30-min window)
    (domain / "2026-04-28_215500_single_page").mkdir()
    # Out-of-window: a page dir from BEFORE the suite
    (domain / "2026-04-28_211000_single_page").mkdir()

    matches = find_page_dirs("suite_20260428_212327", "stripe", runs_dir=runs)
    names = sorted(p.name for p in matches)
    assert names == [
        "2026-04-28_212328_single_page",
        "2026-04-28_212500_single_page",
        "2026-04-28_212834_single_page",
    ]


def test_find_page_dirs_handles_missing_domain(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    assert find_page_dirs("suite_20260428_212327", "stripe", runs_dir=runs) == []


def test_aggregate_scores_averages_across_pages(tmp_path):
    page_a = tmp_path / "page_a"
    page_b = tmp_path / "page_b"
    _write_report(page_a, [_step(4, 5, 3, persona="SaaS founder"), _step(2, 3, 5)])
    _write_report(page_b, [_step(4, 5, 4)])

    result = aggregate_scores([page_a, page_b])
    # cta = (4+2+4)/3 = 3.333..., copy = (5+3+5)/3 = 4.333..., flow = (3+5+4)/3 = 4.0
    assert abs(result["cta_clarity"] - 10 / 3) < 1e-9
    assert abs(result["copy_quality"] - 13 / 3) < 1e-9
    assert abs(result["flow_smoothness"] - 4.0) < 1e-9
    assert result["n_score_steps"] == 3
    assert result["persona"] == "SaaS founder"


def test_aggregate_scores_handles_empty_input():
    result = aggregate_scores([])
    assert result == {
        "cta_clarity": 0.0,
        "copy_quality": 0.0,
        "flow_smoothness": 0.0,
        "n_score_steps": 0,
        "persona": "",
    }


def test_aggregate_scores_skips_missing_report(tmp_path):
    empty_dir = tmp_path / "no_report"
    empty_dir.mkdir()
    populated = tmp_path / "good"
    _write_report(populated, [_step(5, 5, 5)])
    result = aggregate_scores([empty_dir, populated])
    assert result["cta_clarity"] == 5.0
    assert result["n_score_steps"] == 1


def test_build_rows_uses_session_id_to_join(tmp_path):
    runs = tmp_path / "runs"
    domain = runs / "stripe_com"
    domain.mkdir(parents=True)
    page = domain / "2026-04-28_212328_single_page"
    _write_report(page, [_step(4, 5, 4, persona="SaaS founder")])

    cost_rows = [
        {
            "timestamp": "2026-04-28T21:34:58",
            "url": "https://stripe.com",
            "run_type": "multi",
            "model": "claude-sonnet-4-6",
            "input_tokens": "322492",
            "output_tokens": "26020",
            "total_tokens": "348512",
            "step_count": "36",
            "langfuse_session_id": "suite_20260428_212327",
            "langfuse_cost_usd": "1.406664",
        }
    ]
    rows = build_rows(cost_rows=cost_rows, runs_dir=runs)
    matched = [r for r in rows if r.suite_id == "suite_20260428_212327"]
    assert len(matched) == 1
    row = matched[0]
    assert row.variant == "v1_baseline"
    assert row.site == "stripe"
    assert row.total_tokens == 348512
    assert row.step_count == 36
    assert abs(row.cost_usd - 1.406664) < 1e-9
    assert abs(row.tokens_per_step - 348512 / 36) < 1e-6
    assert row.persona == "SaaS founder"


def test_build_rows_skips_unmatched_session_ids(tmp_path):
    cost_rows = [{"langfuse_session_id": "suite_99999999_999999", "total_tokens": "1"}]
    rows = build_rows(cost_rows=cost_rows, runs_dir=tmp_path)
    assert rows == []


def test_build_markdown_emits_table_header_and_per_variant_means():
    rows = [
        VariantRow("v1_baseline", "stripe", "s1", 100, 1.0, 10, 4.0, 4.5, 4.0, 10, "p"),
        VariantRow("v2_advisor", "stripe", "s2", 200, 2.0, 10, 4.5, 5.0, 4.5, 10, "p"),
    ]
    md = build_markdown(rows)
    assert "| Variant | Site |" in md
    assert "| v1_baseline | stripe |" in md
    assert "Per-variant means" in md
    # composite for v1 row: (4.0+4.5+4.0)/3 = 4.166...
    assert "4.17" in md


def test_suite_variants_covers_all_12_known_suites():
    by_variant: dict[str, list[str]] = {}
    for variant, site in SUITE_VARIANTS.values():
        by_variant.setdefault(variant, []).append(site)
    assert set(by_variant) == {"v1_baseline", "v2_advisor", "v3_8step", "v4_8step_advisor"}
    for sites in by_variant.values():
        assert sorted(sites) == ["glossier", "linear", "stripe"]


def test_load_cost_log_round_trip(tmp_path):
    from compare_variants import load_cost_log

    path = tmp_path / "cost_log.csv"
    fields = ["timestamp", "total_tokens", "langfuse_session_id"]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"timestamp": "x", "total_tokens": "1", "langfuse_session_id": "s"})
    rows = load_cost_log(path=path)
    assert rows == [{"timestamp": "x", "total_tokens": "1", "langfuse_session_id": "s"}]
