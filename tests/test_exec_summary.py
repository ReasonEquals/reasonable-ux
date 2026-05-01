"""Tests for _exec_summary_content JSON parsing in generate_report.py.

Covers: valid JSON, markdown fence stripping, array truncation, missing keys,
invalid JSON fallback, empty response fallback, API exception fallback, and
tech_summary=None path. anthropic.Anthropic is patched at the source module.
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from generate_report import _exec_summary_content


def _mock_anthropic(text):
    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=text)]
    mock_client.messages.create.return_value = mock_msg
    return mock_client


_PAGE_SUMMARIES = [{"path": "/", "overall": 3.5, "verdict": "ok", "top_finding": "slow CTA"}]

_FULL_RESPONSE = json.dumps({
    "findings": ["slow load", "no CTA", "confusing nav"],
    "recommendations": ["add CTA", "improve copy", "fix nav"],
    "technical_health": "Two JS errors on homepage",
    "overall_assessment": "Needs significant work on CTA clarity.",
})


def test_valid_json_all_fields():
    with patch("anthropic.Anthropic", return_value=_mock_anthropic(_FULL_RESPONSE)):
        findings, recs, tech, assessment = _exec_summary_content(_PAGE_SUMMARIES)
    assert findings == ["slow load", "no CTA", "confusing nav"]
    assert recs == ["add CTA", "improve copy", "fix nav"]
    assert tech == "Two JS errors on homepage"
    assert assessment == "Needs significant work on CTA clarity."


def test_markdown_fenced_json():
    fenced = "```json\n" + _FULL_RESPONSE + "\n```"
    with patch("anthropic.Anthropic", return_value=_mock_anthropic(fenced)):
        findings, recs, _, _ = _exec_summary_content(_PAGE_SUMMARIES)
    assert findings == ["slow load", "no CTA", "confusing nav"]


def test_markdown_fence_no_language_tag():
    fenced = "```\n" + _FULL_RESPONSE + "\n```"
    with patch("anthropic.Anthropic", return_value=_mock_anthropic(fenced)):
        findings, recs, _, _ = _exec_summary_content(_PAGE_SUMMARIES)
    assert findings == ["slow load", "no CTA", "confusing nav"]


def test_truncates_to_three_items():
    long_response = json.dumps({
        "findings": ["a", "b", "c", "d", "e"],
        "recommendations": ["r1", "r2", "r3", "r4"],
        "technical_health": "",
        "overall_assessment": "",
    })
    with patch("anthropic.Anthropic", return_value=_mock_anthropic(long_response)):
        findings, recs, _, _ = _exec_summary_content(_PAGE_SUMMARIES)
    assert len(findings) == 3
    assert len(recs) == 3


def test_missing_keys_returns_defaults():
    with patch("anthropic.Anthropic", return_value=_mock_anthropic("{}")):
        findings, recs, tech, assessment = _exec_summary_content(_PAGE_SUMMARIES)
    assert findings == []
    assert recs == []
    assert tech == ""
    assert assessment == ""


def test_invalid_json_returns_fallback():
    with patch("anthropic.Anthropic", return_value=_mock_anthropic("not json at all")):
        result = _exec_summary_content(_PAGE_SUMMARIES)
    assert result == ([], [], "", "")


def test_empty_string_returns_fallback():
    mock_client = MagicMock()
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="")]
    mock_client.messages.create.return_value = mock_msg
    with patch("anthropic.Anthropic", return_value=mock_client):
        result = _exec_summary_content(_PAGE_SUMMARIES)
    assert result == ([], [], "", "")


def test_api_exception_returns_fallback():
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API error")
    with patch("anthropic.Anthropic", return_value=mock_client):
        result = _exec_summary_content(_PAGE_SUMMARIES)
    assert result == ([], [], "", "")


def test_tech_summary_none_no_error():
    with patch("anthropic.Anthropic", return_value=_mock_anthropic(_FULL_RESPONSE)):
        result = _exec_summary_content(_PAGE_SUMMARIES, tech_summary=None)
    assert len(result) == 4
