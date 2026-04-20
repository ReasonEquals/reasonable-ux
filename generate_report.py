import argparse
import asyncio
import html
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import jinja2
from playwright.async_api import async_playwright
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus import (
    Image as RLImage,
)

# ── Palette ───────────────────────────────────────────────────────────────────
NAVY       = colors.HexColor("#0f3460")
GREEN      = colors.HexColor("#2ecc71")
AMBER      = colors.HexColor("#f39c12")
RED        = colors.HexColor("#e74c3c")
LIGHT_GREY = colors.HexColor("#e8e8e8")
MID_GREY   = colors.HexColor("#888888")
WHITE      = colors.white
BLACK      = colors.HexColor("#1c1c1c")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm


# ── Style helpers ─────────────────────────────────────────────────────────────
def styles():
    def s(name, **kw):
        return ParagraphStyle(name, **kw)
    return {
        "site":       s("site",       fontName="Helvetica-Bold",    fontSize=20, textColor=NAVY,    spaceAfter=2,  leading=24),
        "subtitle":   s("subtitle",   fontName="Helvetica",         fontSize=12, textColor=MID_GREY, spaceAfter=2,  leading=16),
        "meta":       s("meta",       fontName="Helvetica",         fontSize=9,  textColor=MID_GREY, spaceAfter=2,  leading=12),
        "section":    s("section",    fontName="Helvetica-Bold",    fontSize=9,  textColor=NAVY,     spaceBefore=12, spaceAfter=4, letterSpacing=1),
        "body":       s("body",       fontName="Helvetica",         fontSize=10, textColor=BLACK,    spaceAfter=4,  leading=15),
        "italic":     s("italic",     fontName="Helvetica-Oblique", fontSize=10, textColor=BLACK,    spaceAfter=4,  leading=15),
        "bullet":     s("bullet",     fontName="Helvetica",         fontSize=10, textColor=BLACK,    spaceAfter=3,  leading=14, leftIndent=12),
        "rec":        s("rec",        fontName="Helvetica-Oblique", fontSize=9,  textColor=colors.HexColor("#005f7a"), spaceAfter=5, leading=13, leftIndent=24),
        "footer":     s("footer",     fontName="Helvetica",         fontSize=8,  textColor=MID_GREY, alignment=TA_CENTER),
        "th":         s("th",         fontName="Helvetica-Bold",    fontSize=8,  textColor=WHITE,    alignment=TA_CENTER),
        "td":         s("td",         fontName="Helvetica",         fontSize=8,  textColor=BLACK,    alignment=TA_CENTER),
        "td_left":    s("td_left",    fontName="Helvetica",         fontSize=8,  textColor=BLACK,    alignment=TA_LEFT),
        "log_cell":   s("log_cell",   fontName="Helvetica",         fontSize=7.5, textColor=BLACK,   leading=10),
        "log_center": s("log_center", fontName="Helvetica",         fontSize=7.5, textColor=MID_GREY, alignment=TA_CENTER, leading=10),
        "persona":    s("persona",    fontName="Helvetica-Oblique", fontSize=9,  textColor=MID_GREY, spaceAfter=6, leading=13),
    }


def section_header(text, st):
    return [
        Paragraph(text.upper(), st["section"]),
        HRFlowable(width="100%", thickness=0.75, color=NAVY, spaceAfter=5),
    ]


def score_color(score):
    if score >= 4:
        return GREEN
    if score >= 3:
        return AMBER
    return RED


def score_bar(score, max_score=5, bar_w=80, bar_h=10):
    filled = int(round(bar_w * score / max_score))
    filled = max(1, min(filled, bar_w - 1))
    empty  = bar_w - filled
    bar = Table([["", ""]], colWidths=[filled, empty], rowHeights=[bar_h])
    bar.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (0, 0),  score_color(score)),
        ("BACKGROUND",    (1, 0), (1, 0),  LIGHT_GREY),
        ("LINEABOVE",     (0, 0), (-1, -1), 0, WHITE),
        ("LINEBELOW",     (0, 0), (-1, -1), 0, WHITE),
        ("LINEBEFORE",    (0, 0), (-1, -1), 0, WHITE),
        ("LINEAFTER",     (0, 0), (-1, -1), 0, WHITE),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
    ]))
    return bar


# ── Data helpers ──────────────────────────────────────────────────────────────
def find_latest_ux_run(runs_dir="runs"):
    candidates = []
    for folder in sorted(Path(runs_dir).iterdir(), reverse=True):
        if not folder.is_dir():
            continue
        rp = folder / "report.json"
        if not rp.exists():
            continue
        try:
            data = json.loads(rp.read_text(encoding="utf-8"))
            if data and "cta_clarity" in data[0]:
                candidates.append((folder, data))
        except Exception:
            continue
    if not candidates:
        sys.exit("No UX mode runs found in runs/")
    return candidates[0]


def avg_scores(report):
    dims = {"cta_clarity": [], "copy_quality": [], "flow_smoothness": []}
    for entry in report:
        for dim in dims:
            val = entry.get(dim)
            if val and isinstance(val.get("score"), (int, float)):
                dims[dim].append(val["score"])
    averages = {dim: (sum(v) / len(v) if v else 0) for dim, v in dims.items()}
    all_vals = [s for v in dims.values() for s in v]
    overall  = sum(all_vals) / len(all_vals) if all_vals else 0
    return overall, averages


def derive_date(run_folder):
    name = run_folder.name
    try:
        dt = datetime.strptime(name[:15], "%Y%m%d_%H%M%S")
        return dt.strftime("%B %-d, %Y")
    except ValueError:
        try:
            # folder names like 2026-04-12_1234_single_page
            dt = datetime.strptime(name[:10], "%Y-%m-%d")
            return dt.strftime("%B %-d, %Y")
        except ValueError:
            return ""


# ── Score dimension bars ──────────────────────────────────────────────────────
def _dim_bars(averages, overall, st):
    """Overall bar + three sub-dimension bars, vertically stacked."""
    usable_w  = PAGE_W - 2 * MARGIN
    label_w   = 110
    score_w   = 48
    bar_w_pts = usable_w - label_w - score_w

    def _bar_row(label, score, bar_h, label_size, score_size, bold_score=False):
        c   = score_color(score)
        b   = score_bar(score, bar_w=bar_w_pts, bar_h=bar_h)
        lp  = Paragraph(label, ParagraphStyle(
            f"dbl_{label}", fontName="Helvetica-Bold" if bold_score else "Helvetica",
            fontSize=label_size, textColor=NAVY if bold_score else MID_GREY,
        ))
        fmt = f'<b>{score:.1f}/5</b>' if bold_score else f'{score:.1f}/5'
        sp  = Paragraph(
            f'<font color="#{c.hexval()[2:].upper()}">{fmt}</font>',
            ParagraphStyle(f"dbs_{label}", fontName="Helvetica-Bold", fontSize=score_size, textColor=c),
        )
        row = Table([[lp, b, sp]], colWidths=[label_w, bar_w_pts, score_w])
        row.setStyle(TableStyle([
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING",   (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ]))
        return row

    return [
        _bar_row("Overall",         overall,                    14, 11, 13, bold_score=True),
        _bar_row("CTA Clarity",     averages.get("cta_clarity",     0), 8,  8.5, 8.5),
        _bar_row("Copy Quality",    averages.get("copy_quality",    0), 8,  8.5, 8.5),
        _bar_row("Flow Smoothness", averages.get("flow_smoothness", 0), 8,  8.5, 8.5),
    ]


# ── Step log table ────────────────────────────────────────────────────────────
def _step_log_table(report, st):
    usable_w = PAGE_W - 2 * MARGIN
    num_w    = 22
    act_w    = 58
    obs_w    = (usable_w - num_w - act_w) * 0.40
    vrd_w    = (usable_w - num_w - act_w) * 0.60

    rows = [[
        Paragraph("#",                st["th"]),
        Paragraph("What the agent saw", st["th"]),
        Paragraph("Action",           st["th"]),
        Paragraph("Verdict",          st["th"]),
    ]]
    for entry in report:
        obs     = entry.get("observation", "")
        verdict = entry.get("verdict", "")
        rows.append([
            Paragraph(str(entry["step"]), st["log_center"]),
            Paragraph(html.escape((obs[:110] + "…") if len(obs) > 110 else obs), st["log_cell"]),
            Paragraph(entry.get("action", ""), st["log_center"]),
            Paragraph(html.escape((verdict[:140] + "…") if len(verdict) > 140 else verdict), st["log_cell"]),
        ])

    t = Table(rows, colWidths=[num_w, obs_w, act_w, vrd_w], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  NAVY),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, colors.HexColor("#f7f7f7")]),
        ("GRID",          (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]))
    return t


# ── Per-page flowable builder ─────────────────────────────────────────────────
def _page_elems(run_folder, report, url_hint, st, overall, averages, run_date,
                page_label=None, include_footer=True):
    elems = []

    # Header
    if page_label:
        elems.append(Spacer(1, 10))
        elems += section_header(f"Page: {page_label}", st)
        elems.append(Paragraph(url_hint, st["meta"]))
        elems.append(Spacer(1, 4))
    else:
        hostname = urlparse(url_hint).hostname or url_hint if url_hint.startswith("http") else url_hint
        elems.append(Paragraph(hostname or "UX Evaluation", st["site"]))
        elems.append(Paragraph(url_hint, st["meta"]))
        elems.append(Paragraph(run_date, st["meta"]))
        elems.append(Spacer(1, 8))
        elems.append(HRFlowable(width="100%", thickness=2, color=NAVY, spaceAfter=12))

    # Persona
    persona_str = next((e.get("persona") for e in report if e.get("persona")), None)
    if persona_str:
        elems.append(Paragraph(
            f'Evaluating as: {html.escape(persona_str)}',
            st["persona"],
        ))

    # Screenshot
    screenshots_dir = run_folder / "screenshots"
    screenshot_path = None
    for ext in ("jpg", "png"):
        if page_label:
            candidate = screenshots_dir / f"{page_label.lstrip('/').replace('/', '_')}_step_1.{ext}"
            if candidate.exists():
                screenshot_path = candidate
                break
        candidate = screenshots_dir / f"step_1.{ext}"
        if candidate.exists():
            screenshot_path = candidate
            break

    if screenshot_path:
        try:
            ir = ImageReader(str(screenshot_path))
            iw, ih = ir.getSize()
            usable_w = PAGE_W - 2 * MARGIN
            img_h = min(usable_w * (ih / iw), 100 * mm)
            elems.append(RLImage(str(screenshot_path), width=usable_w, height=img_h))
            elems.append(Spacer(1, 10))
        except Exception:
            pass

    # Scores
    elems += section_header("Scores", st)
    elems += _dim_bars(averages, overall, st)
    elems.append(Spacer(1, 10))

    # At a Glance — first impression + final verdict
    first_imp   = next((e.get("first_impression", "") for e in report if e.get("first_impression")), "")
    final_vrd   = next((e.get("verdict", "") for e in reversed(report) if e.get("action") == "done" and e.get("verdict")), "")
    if not final_vrd:
        final_vrd = next((e.get("verdict", "") for e in reversed(report) if e.get("verdict")), "")

    if first_imp or final_vrd:
        elems += section_header("At a Glance", st)
        if first_imp:
            elems.append(Paragraph(f'\u201c{html.escape(first_imp)}\u201d', st["italic"]))
            elems.append(Spacer(1, 4))
        if final_vrd:
            elems.append(Paragraph(html.escape(final_vrd), st["body"]))
        elems.append(Spacer(1, 6))

    # What We Found + What We Recommend
    all_friction = []
    for entry in report:
        fps  = entry.get("friction_points", [])
        recs = entry.get("recommendations", [])
        for i, fp in enumerate(fps):
            all_friction.append((fp, recs[i] if i < len(recs) else None))

    if all_friction:
        elems += section_header("What We Found", st)
        for fp, _ in all_friction:
            elems.append(Paragraph(f"\u2022 {html.escape(fp)}", st["bullet"]))
        elems.append(Spacer(1, 8))

        elems += section_header("What We Recommend", st)
        for _, rec in all_friction:
            if rec:
                elems.append(Paragraph(f"\u2192 {html.escape(rec)}", st["rec"]))
        elems.append(Spacer(1, 6))

    # Below the fold
    bf_path = run_folder / "below_fold.json"
    if bf_path.exists():
        try:
            bf = json.loads(bf_path.read_text(encoding="utf-8"))
            findings = bf.get("below_fold_findings", [])
            if findings:
                elems += section_header("Below the Fold", st)
                for f in findings:
                    elems.append(Paragraph(f"\u2022 {html.escape(f)}", st["bullet"]))
                elems.append(Spacer(1, 6))
        except Exception:
            pass

    # Step log
    if report:
        elems += section_header("Step Log", st)
        elems.append(_step_log_table(report, st))
        elems.append(Spacer(1, 10))

    # Technical health
    console_path = run_folder / "console.json"
    network_path = run_folder / "network.json"
    tech_lines   = []

    if console_path.exists():
        try:
            cd      = json.loads(console_path.read_text(encoding="utf-8"))
            errors  = [e for e in cd if e.get("type") == "error"]
            warnings= [e for e in cd if e.get("type") == "warning"]
            if not errors and not warnings:
                tech_lines.append("\u2705 No console errors or warnings")
            else:
                tech_lines.append(f"\u26a0\ufe0f {len(errors)} console error(s), {len(warnings)} warning(s)")
                for e in errors[:5]:
                    msg = e.get("text", "")
                    tech_lines.append(f"  \u2022 {(msg[:100] + '\u2026') if len(msg) > 100 else msg}")
        except Exception:
            pass

    if network_path.exists():
        try:
            nd     = json.loads(network_path.read_text(encoding="utf-8"))
            failed = [r for r in nd if r.get("status", 0) >= 400]
            slow   = [r for r in nd if (r.get("duration_ms") or 0) > 2000]
            if not failed and not slow:
                tech_lines.append("\u2705 No failed or slow network requests")
            else:
                tech_lines.append(f"\u26a0\ufe0f {len(failed)} failed request(s) (\u2265400), {len(slow)} slow request(s) (>2s)")
        except Exception:
            pass

    if tech_lines:
        elems += section_header("Technical Health", st)
        for line in tech_lines:
            elems.append(Paragraph(html.escape(line), st["body"]))
        elems.append(Spacer(1, 6))

    # Footer
    if not page_label and include_footer:
        elems.append(Spacer(1, 14))
        elems.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT_GREY, spaceAfter=6))
        elems.append(Paragraph("Generated by reasonable-ux · claude-opus-4-5", st["footer"]))

    return elems


# ── Persona analysis section ──────────────────────────────────────────────────
def add_persona_section(elems, persona_results, st):
    elems += section_header("Multi-Lens Persona Analysis", st)
    elems.append(Spacer(1, 4))

    usable_w  = PAGE_W - 2 * MARGIN
    bar_w_pts = usable_w - 80

    for i, pr in enumerate(persona_results):
        score = float(pr.get("score", 0))
        c     = score_color(score)

        name_p  = Paragraph(f'<b>{html.escape(pr.get("persona_name", ""))}</b>', ParagraphStyle(
            "pn", fontName="Helvetica-Bold", fontSize=11, textColor=NAVY, spaceBefore=8, spaceAfter=2, leading=14,
        ))
        score_p = Paragraph(f'<font color="#{c.hexval()[2:].upper()}"><b>{score:.1f} / 5</b></font>', ParagraphStyle(
            "ps", fontName="Helvetica-Bold", fontSize=11, textColor=c, alignment=TA_LEFT, spaceBefore=8, leading=14,
        ))
        name_row = Table([[name_p, score_p]], colWidths=[usable_w - 70, 70])
        name_row.setStyle(TableStyle([
            ("VALIGN", (0,0),(-1,-1),"BOTTOM"),
            ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
            ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),2),
        ]))
        elems.append(name_row)

        bar     = score_bar(score, bar_w=bar_w_pts, bar_h=8)
        bar_lbl = Paragraph(f'<font color="#{c.hexval()[2:].upper()}">{score:.1f}/5</font>',
                            ParagraphStyle("pbl", fontName="Helvetica", fontSize=8, textColor=c))
        bar_row = Table([[bar, bar_lbl]], colWidths=[bar_w_pts, 80])
        bar_row.setStyle(TableStyle([
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
            ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),4),
        ]))
        elems.append(bar_row)

        if pr.get("persona_description"):
            elems.append(Paragraph(html.escape(pr["persona_description"]), st["italic"]))
        for f in pr.get("key_findings", []):
            elems.append(Paragraph(f"\u2022 {html.escape(f)}", st["bullet"]))
        for r in pr.get("recommendations", []):
            elems.append(Paragraph(f"\u2192 {html.escape(r)}", st["rec"]))

        if i < len(persona_results) - 1:
            elems.append(Spacer(1, 8))
            elems.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT_GREY, spaceAfter=4))

    elems.append(Spacer(1, 10))


# ── Executive summary via Claude Haiku ───────────────────────────────────────
def _exec_summary_content(page_summaries, tech_summary=None):
    import anthropic

    pages_text = "\n".join(
        f"- {ps['path']}: score {ps['overall']:.1f}/5, verdict: {ps.get('verdict') or ps['top_finding']}"
        for ps in page_summaries
    )
    tech_block = ""
    if tech_summary:
        tech_block = (
            f"\n\nTechnical health across all pages: "
            f"{tech_summary['total_errors']} console errors, "
            f"{tech_summary['total_warnings']} warnings, "
            f"{tech_summary['total_failed_requests']} failed requests, "
            f"{tech_summary['total_slow_requests']} slow requests. "
            "Write 1-2 sentences for technical_health — name significant issues or confirm clean results."
        )

    prompt = (
        f"You evaluated {len(page_summaries)} pages of a website for UX quality.\n\n"
        f"Page results:\n{pages_text}{tech_block}\n\n"
        "Write a 2-3 sentence plain-English overall_assessment a non-technical founder can understand. "
        "List the top 3 most important UX findings and top 3 most actionable recommendations — "
        "be specific, name actual UI elements, not generic advice. "
        'Respond ONLY with valid JSON: {"findings": ["...","...","..."], "recommendations": ["...","...","..."], '
        '"technical_health": "...", "overall_assessment": "..."}'
    )
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=768,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip() if msg.content else ""
        if not raw:
            raise ValueError("Empty response")
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rstrip("`").strip()
        data = json.loads(raw)
        return (
            data.get("findings", [])[:3],
            data.get("recommendations", [])[:3],
            data.get("technical_health", ""),
            data.get("overall_assessment", ""),
        )
    except Exception as e:
        print(f"⚠️  Executive summary generation failed: {e}")
        return [], [], "", ""


# ── HTML+Playwright renderer (Phase C+) ──────────────────────────────────────
_TEMPLATE_DIR = Path(__file__).parent / "templates"


def _resolve_personas(run_folder, persona_results):
    """Priority: persona_results (Phase D) → run-dir metadata → DEFAULT_PERSONAS."""
    import report_data as _rd
    if persona_results:
        resolved = []
        for pr in persona_results:
            if isinstance(pr, dict) and pr.get("id") and pr.get("color"):
                resolved.append(pr)
        if resolved:
            return resolved
    if run_folder:
        meta_path = Path(run_folder) / "personas.json"
        if meta_path.exists():
            try:
                loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list) and loaded:
                    return loaded
            except (json.JSONDecodeError, OSError):
                pass
    return _rd.DEFAULT_PERSONAS


def _render_jinja(template_name, ctx):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=jinja2.select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template(template_name).render(**ctx)


def _rewrite_screenshot_paths(normalized, run_folder):
    """Convert report.json screenshot paths (repo-root-relative) to file:// URLs
    so Chromium can load them from the tmp HTML inside run_folder."""
    repo_root = Path.cwd()
    for step in normalized.get("steps", []):
        src = step.get("screenshot")
        if not src:
            continue
        p = Path(src)
        if not p.is_absolute():
            candidates = [repo_root / p, Path(run_folder) / p.name]
            for cand in candidates:
                if cand.exists():
                    step["screenshot"] = cand.resolve().as_uri()
                    break


def _render_pdf_via_playwright(html_str, run_folder, output_path):
    # Write into _TEMPLATE_DIR so relative `styles/` and `../fonts/` hrefs
    # inside the template resolve correctly from the served HTML. Screenshot
    # paths are already rewritten to absolute file:// URLs by caller.
    tmp_html = _TEMPLATE_DIR / "_tmp_report.html"
    tmp_html.write_text(html_str, encoding="utf-8")

    async def _inner():
        async with async_playwright() as p:
            headless = os.environ.get("CI", "true").lower() != "false"
            browser = await p.chromium.launch(headless=headless)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(tmp_html.resolve().as_uri(), wait_until="networkidle")
            await page.evaluate("() => document.fonts.ready")
            await page.pdf(
                path=str(output_path),
                format="Letter",
                print_background=True,
                prefer_css_page_size=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
            await browser.close()

    try:
        asyncio.run(_inner())
    finally:
        if tmp_html.exists():
            tmp_html.unlink()


# ── PDF builder (single-page) ─────────────────────────────────────────────────
def _date_from_run_folder(run_folder) -> str:
    """Parse 'YYYY-MM-DD_HHMM_run_type' folder name into 'Month DD, YYYY'.
    Falls back to today if the folder name doesn't match."""
    try:
        name = Path(run_folder).name
        stamp = "_".join(name.split("_")[:2])
        return datetime.strptime(stamp, "%Y-%m-%d_%H%M").strftime("%B %d, %Y")
    except (ValueError, AttributeError, IndexError):
        return datetime.now().strftime("%B %d, %Y")


def build_pdf(run_folder, report, url_hint, output_path, persona_results=None, *, compact=False):
    import report_data as _rd
    normalized = _rd.load(
        report,
        personas=_resolve_personas(run_folder, persona_results),
        site={"name": url_hint, "url": url_hint},
        date=_date_from_run_folder(run_folder),
    )
    _rewrite_screenshot_paths(normalized, run_folder)
    if not compact:
        raise NotImplementedError(
            "Full template arrives in Phase E; use --compact for now."
        )
    html_str = _render_jinja("compact.html.j2", normalized)
    _render_pdf_via_playwright(html_str, Path(run_folder), Path(output_path))
    print(f"✅ PDF saved: {output_path}")


# ── Multi-page PDF stitcher ───────────────────────────────────────────────────
def stitch_reports(page_results, base_url, output_path, persona_results=None):
    st  = styles()
    doc = SimpleDocTemplate(
        str(output_path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=MARGIN,
        title="Multi-Page UX Evaluation Report", author="reasonable-ux",
    )

    # Compute per-page scores and collect verdicts
    page_summaries = []
    all_scores     = []
    for pr in page_results:
        overall_page, averages_page = avg_scores(pr["report"])
        all_scores.append(overall_page)

        top_finding = next(
            (fp for e in pr["report"] for fp in e.get("friction_points", []) if fp), ""
        )
        if not top_finding:
            top_finding = next((e.get("first_impression", "") for e in pr["report"] if e.get("first_impression")), "")

        verdict = next(
            (e.get("verdict", "") for e in reversed(pr["report"]) if e.get("action") == "done" and e.get("verdict")),
            next((e.get("verdict", "") for e in reversed(pr["report"]) if e.get("verdict")), ""),
        )

        page_summaries.append({
            "path":       pr["path"],
            "url":        pr["url"],
            "run_folder": pr["run_folder"],
            "report":     pr["report"],
            "overall":    overall_page,
            "averages":   averages_page,
            "run_date":   derive_date(pr["run_folder"]),
            "top_finding":top_finding,
            "verdict":    verdict,
        })

    grand_overall = sum(all_scores) / len(all_scores) if all_scores else 0
    grand_averages = {
        dim: sum(ps["averages"].get(dim, 0) for ps in page_summaries) / len(page_summaries)
        for dim in ("cta_clarity", "copy_quality", "flow_smoothness")
    }
    hostname = urlparse(base_url).hostname or base_url.replace("https://", "").replace("http://", "")
    run_date = datetime.now().strftime("%B %-d, %Y")

    # Aggregate tech data
    tech = {"total_errors": 0, "total_warnings": 0, "total_failed_requests": 0, "total_slow_requests": 0, "top_offender_urls": []}
    url_counts = {}
    for ps in page_summaries:
        if (ps["run_folder"] / "console.json").exists():
            try:
                cd = json.loads((ps["run_folder"] / "console.json").read_text(encoding="utf-8"))
                tech["total_errors"]   += sum(1 for e in cd if e.get("type") == "error")
                tech["total_warnings"] += sum(1 for e in cd if e.get("type") == "warning")
            except Exception:
                pass
        if (ps["run_folder"] / "network.json").exists():
            try:
                nd = json.loads((ps["run_folder"] / "network.json").read_text(encoding="utf-8"))
                tech["total_failed_requests"] += sum(1 for r in nd if r.get("status", 0) >= 400)
                tech["total_slow_requests"]   += sum(1 for r in nd if (r.get("duration_ms") or 0) > 2000)
                for r in nd:
                    u = r.get("url", "")
                    url_counts[u] = url_counts.get(u, 0) + 1
            except Exception:
                pass
    tech["top_offender_urls"] = [u for u, _ in sorted(url_counts.items(), key=lambda x: -x[1])[:5]]

    # Executive summary
    print("🧠 Generating executive summary...")
    exec_findings, exec_recs, exec_tech_health, exec_overall = _exec_summary_content(page_summaries, tech_summary=tech)

    elems = []

    # ── SUMMARY PAGE ─────────────────────────────────────────────────────────
    elems.append(Paragraph(hostname or "UX Evaluation", st["site"]))
    elems.append(Paragraph("UX Evaluation Report", st["subtitle"]))
    elems.append(Paragraph(run_date, st["meta"]))
    elems.append(Paragraph(f"{len(page_summaries)} page(s) evaluated", st["meta"]))
    elems.append(Spacer(1, 6))
    elems.append(HRFlowable(width="100%", thickness=2, color=NAVY, spaceAfter=12))

    elems += section_header("Overall Score", st)
    elems += _dim_bars(grand_averages, grand_overall, st)
    elems.append(Spacer(1, 10))

    if exec_overall:
        elems.append(Paragraph(html.escape(exec_overall), st["body"]))
        elems.append(Spacer(1, 8))

    if exec_findings:
        elems += section_header("What We Found", st)
        for f in exec_findings:
            elems.append(Paragraph(f"\u2022 {html.escape(f)}", st["bullet"]))
        elems.append(Spacer(1, 8))

    if exec_recs:
        elems += section_header("What We Recommend", st)
        for r in exec_recs:
            elems.append(Paragraph(f"\u2192 {html.escape(r)}", st["rec"]))
        elems.append(Spacer(1, 10))

    # Pages at a glance
    elems += section_header("Pages Evaluated", st)
    usable_w = PAGE_W - 2 * MARGIN
    pg_col_w = [60, 45, usable_w - 105]
    pg_data  = [[Paragraph("Page", st["th"]), Paragraph("Score", st["th"]), Paragraph("Verdict", st["th"])]]
    for ps in page_summaries:
        c   = score_color(ps["overall"])
        txt = ps["verdict"] or ps["top_finding"]
        pg_data.append([
            Paragraph(ps["path"], st["td_left"]),
            Paragraph(f'<font color="#{c.hexval()[2:].upper()}"><b>{ps["overall"]:.1f}/5</b></font>', st["td"]),
            Paragraph(html.escape((txt[:160] + "\u2026") if len(txt) > 160 else txt), st["td_left"]),
        ])
    pg_t = Table(pg_data, colWidths=pg_col_w, repeatRows=1)
    pg_t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  NAVY),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, colors.HexColor("#f5f5f5")]),
        ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
    ]))
    elems.append(pg_t)
    elems.append(Spacer(1, 10))

    # Tech health on summary page
    elems += section_header("Technical Health", st)
    if exec_tech_health:
        elems.append(Paragraph(html.escape(exec_tech_health), st["body"]))
    else:
        e, w, f, s = tech["total_errors"], tech["total_warnings"], tech["total_failed_requests"], tech["total_slow_requests"]
        if e == 0 and w == 0 and f == 0 and s == 0:
            elems.append(Paragraph("\u2705 No console errors, warnings, or network issues detected.", st["body"]))
        else:
            elems.append(Paragraph(f"{e} console error(s), {w} warning(s), {f} failed request(s), {s} slow request(s).", st["body"]))

    # ── PER-PAGE DETAIL ───────────────────────────────────────────────────────
    for ps in page_summaries:
        elems.append(PageBreak())
        elems += _page_elems(
            ps["run_folder"], ps["report"], ps["url"],
            st, ps["overall"], ps["averages"], ps["run_date"],
            page_label=ps["path"],
        )

    # Persona section
    if persona_results:
        elems.append(PageBreak())
        add_persona_section(elems, persona_results, st)

    elems.append(Spacer(1, 14))
    elems.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT_GREY, spaceAfter=6))
    elems.append(Paragraph("Generated by reasonable-ux · claude-opus-4-5", st["footer"]))

    doc.build(elems)
    print(f"✅ Multi-page PDF saved: {output_path}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=str, default=None)
    parser.add_argument("--url", type=str, default=None)
    args = parser.parse_args()

    if args.run:
        run_folder = Path(args.run)
        rp = run_folder / "report.json"
        if not rp.exists():
            sys.exit(f"No report.json found in {run_folder}")
        report = json.loads(rp.read_text(encoding="utf-8"))
        if not report or "cta_clarity" not in report[0]:
            sys.exit("Not a UX mode run")
    else:
        run_folder, report = find_latest_ux_run()

    url_hint    = args.url or "https://depreciationpro.com"
    safe_domain = url_hint.replace("https://", "").replace("http://", "").rstrip("/").replace(".", "_").replace("/", "_")
    iso_date    = run_folder.name[:8]
    iso_date    = f"{iso_date[:4]}-{iso_date[4:6]}-{iso_date[6:8]}"
    output_path = run_folder / f"{safe_domain}_{iso_date}.pdf"

    print(f"📄 Generating PDF for: {run_folder.name}")
    build_pdf(run_folder, report, url_hint, output_path)
