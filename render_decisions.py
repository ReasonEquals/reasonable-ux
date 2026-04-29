#!/usr/bin/env python3
"""Render DECISIONS.md → DECISIONS.pdf via the project's editorial CSS + Playwright pipeline."""

import re
from pathlib import Path

from generate_report import _render_jinja, _render_pdf_via_playwright

ROOT = Path(__file__).parent
DECISIONS_MD = ROOT / "DECISIONS.md"
OUTPUT_PDF = ROOT / "DECISIONS.pdf"
OUTPUT_HTML = ROOT / "DECISIONS.html"


# ── Inline markdown → HTML ───────────────────────────────────────────────────

def _inline(text: str) -> str:
    """Convert inline markdown tokens to HTML (result must be used with | safe in template)."""
    # Backtick markdown links: [`file.py:123`](url) → code span
    text = re.sub(r'\[`([^`\]]+)`\]\([^)]+\)', r'<code class="code-ref">\1</code>', text)
    # Plain markdown links: [text](url) → code-ref span
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'<span class="code-ref">\1</span>', text)
    # Code spans
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # Bold
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'\*([^*]+)\*', r'<em>\1</em>', text)
    return text


# ── Parser ───────────────────────────────────────────────────────────────────

def parse(md: str) -> dict:
    """Parse DECISIONS.md into {title, intro, sections:[{num,title,intro,decisions}]}."""
    doc = {"title": "", "_intro_buf": [], "sections": []}
    cur_sec = None
    cur_dec = None
    cur_field_label = None
    cur_field_lines: list[str] = []
    para_buf: list[str] = []

    def _flush_para() -> str:
        t = " ".join(para_buf).strip()
        para_buf.clear()
        return t

    def _commit_field():
        nonlocal cur_field_label, cur_field_lines
        if cur_dec is not None and cur_field_label:
            cur_dec["fields"].append({
                "label": cur_field_label,
                "content": _inline(" ".join(cur_field_lines).strip()),
            })
        cur_field_label = None
        cur_field_lines = []

    def _commit_dec():
        _commit_field()
        if cur_dec is not None and cur_sec is not None:
            cur_sec["decisions"].append(cur_dec)

    state = "pre_title"

    for raw in md.splitlines():
        line = raw.rstrip()

        if state == "pre_title":
            if line.startswith("# "):
                doc["title"] = line[2:].strip()
                state = "body"
            continue

        if line == "---":
            t = _flush_para()
            if t:
                if cur_field_label:
                    cur_field_lines.append(t)
                elif cur_sec and not cur_dec:
                    cur_sec["_intro_buf"].append(t)
                elif not cur_sec:
                    doc["_intro_buf"].append(t)
            continue

        if line.startswith("## "):
            t = _flush_para()
            if t and not cur_sec:
                doc["_intro_buf"].append(t)
            _commit_dec()
            cur_dec = None
            cur_field_label = None
            cur_field_lines = []
            m = re.match(r"(\d+)\.\s+(.*)", line[3:].strip())
            cur_sec = {
                "num": m.group(1) if m else "",
                "title": m.group(2) if m else line[3:].strip(),
                "_intro_buf": [],
                "decisions": [],
            }
            doc["sections"].append(cur_sec)
            continue

        if line.startswith("### "):
            t = _flush_para()
            if t and cur_sec and not cur_dec:
                cur_sec["_intro_buf"].append(t)
            _commit_dec()
            title = line[4:].strip()
            if ":" in title and title.split(":")[0].strip().lower() == "decision":
                title = title[title.index(":") + 1:].strip()
            cur_dec = {"title": title, "fields": []}
            cur_field_label = None
            cur_field_lines = []
            continue

        # Field label line: **Label:** content
        m = re.match(r"\*\*([^*:]+):\*\*\s*(.*)", line)
        if m and cur_dec is not None:
            t = _flush_para()
            if t and cur_field_label:
                cur_field_lines.append(t)
            _commit_field()
            cur_field_label = m.group(1)
            rest = m.group(2).strip()
            cur_field_lines = [_inline(rest)] if rest else []
            continue

        if not line.strip():
            t = _flush_para()
            if t:
                if cur_field_label:
                    cur_field_lines.append(t)
                elif cur_sec and not cur_dec:
                    cur_sec["_intro_buf"].append(t)
                elif not cur_sec:
                    doc["_intro_buf"].append(t)
            continue

        # Continuation of current field
        if cur_field_label:
            cur_field_lines.append(_inline(line.strip()))
            continue

        # Regular paragraph
        para_buf.append(line.strip())

    # Flush tail
    t = _flush_para()
    if t and cur_field_label:
        cur_field_lines.append(t)
    _commit_dec()

    # Flatten intro buffers to HTML strings
    doc["intro"] = _inline(" ".join(doc["_intro_buf"]))
    for s in doc["sections"]:
        s["intro"] = _inline(" ".join(s["_intro_buf"]))

    return doc


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    md = DECISIONS_MD.read_text(encoding="utf-8")
    doc = parse(md)
    html = _render_jinja("decisions.html.j2", doc)
    _render_pdf_via_playwright(html, ROOT, OUTPUT_PDF)
    print(f"✓  {OUTPUT_PDF.relative_to(ROOT)}")

    web_html = _render_jinja("decisions_web.html.j2", doc)
    OUTPUT_HTML.write_text(web_html, encoding="utf-8")
    print(f"✓  {OUTPUT_HTML.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
