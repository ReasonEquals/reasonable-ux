"""Post-extraction sanitization for model-extracted strings.

Guards against indirect prompt injection via scraped page content that is
later re-fed into downstream model calls (persona threading through per-step
prompts, below-fold analysis, and exec-summary synthesis).

Scope: persona strings and friction-point / recommendation strings only.
Intentionally narrow — see Phase 1.5 chip spec.
"""

from __future__ import annotations

import re

PERSONA_MAX_LEN = 200
FIELD_MAX_LEN = 500
ELLIPSIS = "..."
PERSONA_FALLBACK = "Generic website visitor"

_INJECTION_PATTERNS = [
    re.compile(r"^\s*(system|assistant|user)\s*:", re.IGNORECASE),
    re.compile(r"<\|?(system|user|assistant|im_start|im_end)\|?>", re.IGNORECASE),
    re.compile(r"\[/?INST\]", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+|previous\s+|prior\s+)?(instructions|prompts)", re.IGNORECASE),
    re.compile(r"disregard\s+(the\s+|all\s+)?(above|previous)", re.IGNORECASE),
]
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


def _strip_patterns(s: str) -> str:
    for pat in _INJECTION_PATTERNS:
        s = pat.sub("", s)
    s = _CONTROL_CHARS.sub("", s)
    return s


def _truncate(s: str, cap: int) -> str:
    if len(s) <= cap:
        return s
    return s[: cap - len(ELLIPSIS)] + ELLIPSIS


def sanitize_persona(persona: str | None) -> str:
    """Strip injection shapes, collapse to a one-liner, cap at 200 chars.

    Falls back to PERSONA_FALLBACK only when the sanitized string is empty,
    shorter than 10 chars, or has no alphanumeric content. Otherwise truncates
    with ellipsis — truncation preserves persona-keyword matching in downstream
    label scoring.
    """
    if not persona or not isinstance(persona, str):
        return PERSONA_FALLBACK
    s = _strip_patterns(persona)
    s = _WHITESPACE.sub(" ", s).strip()
    if len(s) < 10 or not re.search(r"[A-Za-z0-9]", s):
        return PERSONA_FALLBACK
    return _truncate(s, PERSONA_MAX_LEN)


def sanitize_field(value: str) -> str:
    """Strip injection shapes + control chars from a friction-point string,
    cap at 500 chars with ellipsis. Newlines are preserved as spaces so the
    field stays single-line in report.json.
    """
    if not isinstance(value, str):
        return value
    s = _strip_patterns(value)
    s = _WHITESPACE.sub(" ", s).strip()
    return _truncate(s, FIELD_MAX_LEN)


def sanitize_string_list(items) -> list:
    """Apply sanitize_field to every string element; leave non-strings intact."""
    if not isinstance(items, list):
        return items
    return [sanitize_field(x) if isinstance(x, str) else x for x in items]
