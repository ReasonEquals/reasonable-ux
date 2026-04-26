"""Tests for _deduplicate_findings in generate_report.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from generate_report import _deduplicate_findings


def test_dedup_removes_duplicate():
    pages = [
        {"top_finding": "slow load"},
        {"top_finding": "slow load"},
    ]
    _deduplicate_findings(pages)
    assert pages[0]["top_finding"] == "slow load"
    assert pages[1]["top_finding"] == ""


def test_dedup_first_occurrence_survives():
    pages = [
        {"top_finding": "confusing CTA"},
        {"top_finding": "confusing CTA"},
        {"top_finding": "confusing CTA"},
    ]
    _deduplicate_findings(pages)
    assert pages[0]["top_finding"] == "confusing CTA"
    assert pages[1]["top_finding"] == ""
    assert pages[2]["top_finding"] == ""


def test_dedup_preserves_unique():
    pages = [
        {"top_finding": "slow load"},
        {"top_finding": "broken CTA"},
        {"top_finding": "no social proof"},
    ]
    _deduplicate_findings(pages)
    assert pages[0]["top_finding"] == "slow load"
    assert pages[1]["top_finding"] == "broken CTA"
    assert pages[2]["top_finding"] == "no social proof"


def test_dedup_empty_list():
    _deduplicate_findings([])  # must not raise


def test_dedup_empty_string_ignored():
    # Empty strings are not treated as duplicates of each other
    pages = [
        {"top_finding": ""},
        {"top_finding": ""},
    ]
    _deduplicate_findings(pages)
    assert pages[0]["top_finding"] == ""
    assert pages[1]["top_finding"] == ""


def test_dedup_missing_key_ignored():
    pages = [
        {"url": "/pricing"},
        {"top_finding": "confusing CTA"},
    ]
    _deduplicate_findings(pages)
    assert "top_finding" not in pages[0]
    assert pages[1]["top_finding"] == "confusing CTA"


def test_dedup_mutates_in_place():
    pages = [
        {"top_finding": "slow load"},
        {"top_finding": "slow load"},
    ]
    original_id = id(pages)
    result = _deduplicate_findings(pages)
    assert result is None
    assert id(pages) == original_id
    assert pages[1]["top_finding"] == ""
