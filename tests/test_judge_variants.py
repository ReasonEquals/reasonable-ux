"""Tests for judge_variants.py — pure data functions, no live API calls."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# isort: split
from judge_variants import (  # noqa: E402
    DIMENSIONS,
    JudgeRecord,
    _aggregate,
    _extract_report_text,
    _is_close_call,
    _parse_json_safe,
    _remap_ab_to_variants,
    build_judge_md,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RUBRIC_PATH = REPO_ROOT / "artifacts" / "variant_judge_rubric.md"


def test_rubric_file_exists_and_has_dimensions():
    """Rubric must be present and name all 4 dimensions used by the judge."""
    assert RUBRIC_PATH.exists(), f"missing rubric: {RUBRIC_PATH}"
    text = RUBRIC_PATH.read_text()
    for label in ("Specificity", "Actionability", "Coverage", "Persona fidelity"):
        assert label in text, f"rubric missing dimension: {label}"
    # Must call out the locked / no-mid-run-edits rule
    assert "Locked" in text or "locked" in text


def test_extract_report_text_keeps_text_fields(tmp_path):
    corpus = tmp_path / "v1.json"
    corpus.write_text(json.dumps([
        {
            "step": 1,
            "persona": "Mid-market SaaS founder",
            "verdict": "Strong hero but vague pricing.",
            "friction_points": ["No pricing above fold", "Single sales-gated CTA"],
            "recommendations": ["Add demo CTA", "Surface starter price"],
            "cta_clarity": {"score": 3, "note": "Only contact-sales path"},
            "copy_quality": {"score": 4, "note": "Punchy headline"},
            "flow_smoothness": {"score": 3, "note": "News ticker distracts"},
        },
        {
            "step": 2,
            "verdict": "Pricing page buries enterprise tier.",
            "friction_points": ["Pricing tiers feel SMB-scale"],
            "recommendations": ["Show enterprise volume tier"],
            "cta_clarity": {"score": 2, "note": "No enterprise CTA"},
        },
    ]))
    text = _extract_report_text(corpus)
    # Persona shows up only on step 1
    assert "Mid-market SaaS founder" in text
    # All friction strings preserved
    assert "No pricing above fold" in text
    assert "Single sales-gated CTA" in text
    assert "Pricing tiers feel SMB-scale" in text
    # All recommendations preserved
    assert "Add demo CTA" in text
    assert "Show enterprise volume tier" in text
    # Scoring notes preserved
    assert "News ticker distracts" in text


def test_parse_json_safe_recovers_from_preamble():
    raw = 'Sure! Here is the JSON:\n{"overall": {"winner": "A", "reason": "specific"}}\n\nLet me know.'
    parsed = _parse_json_safe(raw)
    assert parsed == {"overall": {"winner": "A", "reason": "specific"}}
    assert _parse_json_safe("") == {}
    assert _parse_json_safe("no json here") == {}


def test_remap_ab_to_variants_preserves_tie_and_swaps_winners():
    raw = {
        "dimensions": {
            "specificity": {"winner": "A", "reason": "r1"},
            "actionability": {"winner": "B", "reason": "r2"},
            "coverage": {"winner": "tie", "reason": "negligible gap"},
            "persona_fidelity": {"winner": "B", "reason": "r4"},
        },
        "overall": {"winner": "B", "reason": "r5"},
    }
    # Champion got label B this call
    out = _remap_ab_to_variants(raw, a_variant="v2_advisor", b_variant="v1_baseline")
    assert out["dimensions"]["specificity"]["winner"] == "v2_advisor"
    assert out["dimensions"]["actionability"]["winner"] == "v1_baseline"
    assert out["dimensions"]["coverage"]["winner"] == "tie"
    assert out["dimensions"]["persona_fidelity"]["winner"] == "v1_baseline"
    assert out["overall"]["winner"] == "v1_baseline"


def _record(dim_winners: list[str], overall: str) -> JudgeRecord:
    return JudgeRecord(
        site="stripe",
        champion="v1_baseline",
        challenger="v2_advisor",
        pass_idx=0,
        a_variant="v1_baseline",
        b_variant="v2_advisor",
        dimensions={
            dim: {"winner": w, "reason": "r"} for dim, w in zip(DIMENSIONS, dim_winners, strict=True)
        },
        overall={"winner": overall, "reason": "r"},
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.05,
    )


def test_close_call_detection_flags_ties_and_majority_contradictions():
    # Decisive win — all 4 dimensions and overall agree on challenger; not close
    decisive = _record(["v2_advisor"] * 4, "v2_advisor")
    assert _is_close_call(decisive) is False

    # Has a tie in one dimension — close call
    has_tie = _record(["v2_advisor", "v2_advisor", "tie", "v2_advisor"], "v2_advisor")
    assert _is_close_call(has_tie) is True

    # Overall contradicts dimension majority — close call
    contradicts = _record(
        ["v2_advisor", "v2_advisor", "v2_advisor", "v1_baseline"], "v1_baseline"
    )
    assert _is_close_call(contradicts) is True


def test_build_judge_md_renders_table_and_narrative():
    records = [_record(["v2_advisor"] * 4, "v2_advisor")]
    aggregate = _aggregate(records)
    md = build_judge_md([aggregate], total_cost=0.05)
    # Header + table column names
    assert "Variant judge" in md
    assert "Specificity" in md and "Persona fidelity" in md
    # Row for the aggregated pair
    assert "stripe" in md and "v2_advisor" in md
    # Narrative section + win-loss-tie format
    assert "Narrative" in md
    assert "1W / 0L / 0T" in md
    # Pre-registered hypothesis preserved
    assert "Pre-registered hypothesis" in md
