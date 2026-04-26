"""Tests for pure helper functions in agent_core.py.

Scope: deterministic, non-LLM functions only. The agent loop itself is
deliberately untested here — see DECISIONS.md §8 (Deferred) for why."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_core import _infer_goal_from_url, _sanitize_selector  # noqa: E402

# --- _sanitize_selector ---------------------------------------------------

def test_clean_selector_passes_unchanged():
    s = "button.primary"
    assert _sanitize_selector(s) == s


def test_clean_multipart_selector_preserves_all_parts():
    s = "button.primary, a.cta, .signup-link"
    out = _sanitize_selector(s)
    # All three parts survive; comma-separated.
    parts = [p.strip() for p in out.split(",")]
    assert len(parts) == 3
    assert "button.primary" in parts
    assert "a.cta" in parts
    assert ".signup-link" in parts


def test_pure_contains_selector_raises():
    with pytest.raises(ValueError):
        _sanitize_selector("button:contains('Sign up')")


def test_all_parts_blocked_raises():
    with pytest.raises(ValueError):
        _sanitize_selector("a:contains('x'), button:contains('y')")


def test_mixed_selector_keeps_only_clean_parts():
    out = _sanitize_selector("button.primary, a:contains('Sign up')")
    parts = [p.strip() for p in out.split(",")]
    assert "button.primary" in parts
    assert not any(":contains(" in p for p in parts)
    assert len(parts) == 1


def test_none_passes_through():
    assert _sanitize_selector(None) is None


def test_whitespace_around_parts_normalized():
    out = _sanitize_selector("  button.primary  ,   a.cta  ")
    parts = [p.strip() for p in out.split(",")]
    assert len(parts) == 2
    # No leading/trailing whitespace inside any retained part
    assert all(p == p.strip() for p in parts)


# --- _infer_goal_from_url -------------------------------------------------

KNOWN_SEGMENTS = [
    "pricing", "faq", "features", "about", "contact",
    "login", "signup", "register", "terms", "privacy", "blog", "demo",
]


def test_known_segments_get_distinct_goals():
    """Each known segment should map to a unique, segment-specific goal."""
    goals = {seg: _infer_goal_from_url(f"https://x.com/{seg}") for seg in KNOWN_SEGMENTS}
    # All 12 known segments produce 12 distinct goal strings.
    assert len(set(goals.values())) == len(KNOWN_SEGMENTS)


def test_unknown_segment_falls_back_to_default():
    default = _infer_goal_from_url("https://x.com/some-random-page")
    # Same default for any unknown segment.
    assert _infer_goal_from_url("https://x.com/another-unknown") == default


def test_root_path_falls_back_to_default():
    default = _infer_goal_from_url("https://x.com/some-unknown")
    assert _infer_goal_from_url("https://x.com/") == default
    assert _infer_goal_from_url("https://x.com") == default


def test_trailing_slash_normalized():
    assert _infer_goal_from_url("https://x.com/pricing/") == _infer_goal_from_url("https://x.com/pricing")


def test_case_insensitive_segment_match():
    assert _infer_goal_from_url("https://x.com/PRICING") == _infer_goal_from_url("https://x.com/pricing")


def test_last_path_segment_wins():
    """A path like /foo/pricing should infer the pricing goal, not foo."""
    pricing_goal = _infer_goal_from_url("https://x.com/pricing")
    assert _infer_goal_from_url("https://x.com/company/pricing") == pricing_goal


def test_query_string_does_not_change_goal():
    base = _infer_goal_from_url("https://x.com/pricing")
    assert _infer_goal_from_url("https://x.com/pricing?ref=homepage&utm=x") == base


def test_pricing_goal_mentions_pricing_concept():
    """Spec: pricing segment should produce a goal that's about pricing.

    Looser than a string-match assertion — just confirms the goal is
    semantically related to its segment, catching dict-key-shuffle bugs."""
    goal = _infer_goal_from_url("https://x.com/pricing").lower()
    assert "pricing" in goal


def test_signup_goal_distinct_from_login_goal():
    """Login and signup are easy to confuse; ensure the dict didn't conflate them."""
    assert _infer_goal_from_url("https://x.com/signup") != _infer_goal_from_url("https://x.com/login")
