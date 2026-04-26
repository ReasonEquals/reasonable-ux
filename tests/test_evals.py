"""Tests for nav-drift detection in evals/run_evals.py.

This is the regression net for the `nav:<Label>` prompt contract — if the
agent regresses to emitting CSS selectors for nav links, these checks must
catch it. The eval harness exercises them indirectly against real model
output; these tests pin the regex/check semantics directly."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals"))

from run_evals import _NAV_DRIFT_RE, _nav_drift_check  # noqa: E402

# --- _NAV_DRIFT_RE — pattern coverage -------------------------------------

def test_matches_a_href_attribute_selector():
    assert _NAV_DRIFT_RE.search("a[href='/pricing']")
    assert _NAV_DRIFT_RE.search("a[href*=\"pricing\"]")


def test_matches_a_has_text():
    assert _NAV_DRIFT_RE.search("a:has-text('Pricing')")


def test_matches_a_contains():
    assert _NAV_DRIFT_RE.search("a:contains('Pricing')")


def test_matches_dot_nav_class():
    assert _NAV_DRIFT_RE.search(".nav-link")
    assert _NAV_DRIFT_RE.search(".nav_item")


def test_matches_nav_descendant_selector():
    assert _NAV_DRIFT_RE.search("nav a")
    assert _NAV_DRIFT_RE.search("nav    a")


def test_matches_header_descendant_selector():
    assert _NAV_DRIFT_RE.search("header a")


def test_case_insensitive():
    assert _NAV_DRIFT_RE.search("A[HREF='/x']")
    assert _NAV_DRIFT_RE.search("Nav A")
    assert _NAV_DRIFT_RE.search("HEADER A")


def test_does_not_match_nav_label_prefix():
    """`nav:Pricing` is the CORRECT form — must not be flagged as drift."""
    assert _NAV_DRIFT_RE.search("nav:Pricing") is None
    assert _NAV_DRIFT_RE.search("nav:Sign up") is None


def test_does_not_match_unrelated_button_class():
    assert _NAV_DRIFT_RE.search(".button.primary") is None
    assert _NAV_DRIFT_RE.search("#main-cta") is None


def test_anchored_patterns_only_match_at_start():
    """The patterns for `a[href`, `a:has-text`, `a:contains`, `nav a`, and
    `header a` are anchored to `^`. Strings that *contain* those patterns
    later in the selector (but don't start with them) must NOT trigger,
    otherwise legitimately complex selectors get false-positived."""
    # Without the ^ anchor these would all match — the anchor is load-bearing.
    assert _NAV_DRIFT_RE.search("ul.menu nav a") is None
    assert _NAV_DRIFT_RE.search("div a[href='/x']") is None
    assert _NAV_DRIFT_RE.search("section a:has-text('x')") is None
    assert _NAV_DRIFT_RE.search("div header a") is None


def test_does_not_match_dot_navigation_word():
    """`.nav-` requires a hyphen or underscore right after `nav`. A class
    like `.navigation` should not match (different word, different intent)."""
    assert _NAV_DRIFT_RE.search(".navigation") is None


# --- _nav_drift_check — aggregation behavior ------------------------------

def test_counts_nav_prefix_targets():
    report = [
        {"target": "nav:Pricing"},
        {"target": "nav:About"},
        {"target": "button.cta"},
    ]
    nav_count, suspicious = _nav_drift_check(report)
    assert nav_count == 2
    assert suspicious == []


def test_returns_suspicious_css_targets():
    report = [
        {"target": "a[href='/pricing']"},
        {"target": "nav a"},
        {"target": ".nav-item"},
    ]
    nav_count, suspicious = _nav_drift_check(report)
    assert nav_count == 0
    assert len(suspicious) == 3
    assert "a[href='/pricing']" in suspicious
    assert "nav a" in suspicious
    assert ".nav-item" in suspicious


def test_ignores_http_url_targets_even_when_pattern_would_match():
    """URLs (target starts with http) are navigations, not selectors — they
    must never be flagged as suspicious CSS, even when the URL string contains
    a substring that the drift regex would otherwise match. The http-filter
    in `_nav_drift_check` is what enforces this."""
    # `.nav-` matches the drift regex (literal dot + nav + hyphen).
    # Without the http filter, this URL would be flagged as suspicious.
    report = [
        {"target": "https://example.com/foo.nav-section"},
        {"target": "http://example.com/?ref=.nav-promo"},
    ]
    # Sanity check: the patterns DO match these strings on their own.
    assert _NAV_DRIFT_RE.search("https://example.com/foo.nav-section")
    assert _NAV_DRIFT_RE.search("http://example.com/?ref=.nav-promo")
    # But the check should still ignore them because they're URLs.
    nav_count, suspicious = _nav_drift_check(report)
    assert nav_count == 0
    assert suspicious == []


def test_handles_missing_target_field():
    report = [{"action": "scroll"}, {"target": None}]
    nav_count, suspicious = _nav_drift_check(report)
    assert nav_count == 0
    assert suspicious == []


def test_handles_non_string_target():
    report = [{"target": 42}, {"target": ["a", "b"]}, {"target": {}}]
    nav_count, suspicious = _nav_drift_check(report)
    assert nav_count == 0
    assert suspicious == []


def test_handles_empty_report():
    nav_count, suspicious = _nav_drift_check([])
    assert nav_count == 0
    assert suspicious == []


def test_mixed_report_separates_correctly():
    """A realistic report with some correct nav: clicks and one drift regression."""
    report = [
        {"target": "nav:Pricing"},
        {"target": "button.signup"},
        {"target": "a:has-text('Features')"},  # drift
        {"target": "nav:About"},
        {"target": "https://example.com/careers"},  # external nav, ignored
    ]
    nav_count, suspicious = _nav_drift_check(report)
    assert nav_count == 2  # Pricing, About
    assert suspicious == ["a:has-text('Features')"]
