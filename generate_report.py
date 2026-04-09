import argparse
import html
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    HRFlowable,
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Palette ───────────────────────────────────────────────────────────────────
NAVY       = colors.HexColor("#0f3460")
BLUE       = colors.HexColor("#00d4ff")
GREEN      = colors.HexColor("#2ecc71")
AMBER      = colors.HexColor("#f39c12")
RED        = colors.HexColor("#e74c3c")
LIGHT_GREY = colors.HexColor("#e8e8e8")
MID_GREY   = colors.HexColor("#888888")
DARK       = colors.HexColor("#1a1a2e")
WHITE      = colors.white
BLACK      = colors.HexColor("#1c1c1c")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm


# ── Style helpers ─────────────────────────────────────────────────────────────
def styles():
    base = getSampleStyleSheet()
    def s(name, **kw):
        return ParagraphStyle(name, **kw)

    return {
        "site":      s("site",      fontName="Helvetica-Bold",   fontSize=18, textColor=NAVY,     spaceAfter=20, leading=22),
        "meta":      s("meta",      fontName="Helvetica",        fontSize=10, textColor=MID_GREY,  spaceAfter=4,  leading=14),
        "section":   s("section",   fontName="Helvetica-Bold",   fontSize=10, textColor=NAVY,      spaceBefore=14, spaceAfter=6, letterSpacing=1),
        "body":      s("body",      fontName="Helvetica",        fontSize=10, textColor=BLACK,     spaceAfter=4,   leading=15),
        "italic":    s("italic",    fontName="Helvetica-Oblique",fontSize=10, textColor=BLACK,     spaceAfter=4,   leading=15),
        "bullet":    s("bullet",    fontName="Helvetica",        fontSize=10, textColor=BLACK,     spaceAfter=3,   leading=14, leftIndent=12),
        "rec":       s("rec",       fontName="Helvetica-Oblique",fontSize=9,  textColor=colors.HexColor("#005f7a"), spaceAfter=5, leading=13, leftIndent=24),
        "footer":    s("footer",    fontName="Helvetica",        fontSize=8,  textColor=MID_GREY,  alignment=TA_CENTER),
        "score_big": s("score_big", fontName="Helvetica-Bold",   fontSize=28, textColor=NAVY,      alignment=TA_CENTER),
        "th":        s("th",        fontName="Helvetica-Bold",   fontSize=9,  textColor=WHITE,     alignment=TA_CENTER),
        "td":        s("td",        fontName="Helvetica",        fontSize=9,  textColor=BLACK,     alignment=TA_CENTER),
        "td_left":   s("td_left",   fontName="Helvetica",        fontSize=9,  textColor=BLACK,     alignment=TA_LEFT),
    }


def section_header(text, st):
    return [
        Paragraph(text.upper(), st["section"]),
        HRFlowable(width="100%", thickness=1, color=NAVY, spaceAfter=6),
    ]


def score_color(score):
    if score >= 4:
        return GREEN
    if score >= 3:
        return AMBER
    return RED


def confidence_label(conf):
    mapping = {"high": ("HIGH", GREEN), "medium": ("MED", AMBER), "low": ("LOW", RED)}
    return mapping.get(conf.lower(), (conf.upper(), MID_GREY)) if conf else ("—", MID_GREY)


def score_bar(score, max_score=5, bar_w=80, bar_h=10):
    """Return a Table that renders as a colored progress bar."""
    filled = int(round(bar_w * score / max_score))
    empty  = bar_w - filled
    bar = Table(
        [["", ""]],
        colWidths=[filled, empty],
        rowHeights=[bar_h],
    )
    bar.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), score_color(score)),
        ("BACKGROUND", (1, 0), (1, 0), LIGHT_GREY),
        ("LINEABOVE",    (0, 0), (-1, -1), 0, WHITE),
        ("LINEBELOW",    (0, 0), (-1, -1), 0, WHITE),
        ("LINEBEFORE",   (0, 0), (-1, -1), 0, WHITE),
        ("LINEAFTER",    (0, 0), (-1, -1), 0, WHITE),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return bar


# ── Data loading ──────────────────────────────────────────────────────────────
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


def derive_url(run_folder):
    # Try to find URL from run folder's report — fall back to folder name
    rp = run_folder / "report.json"
    data = json.loads(rp.read_text(encoding="utf-8"))
    # URL isn't stored in JSON currently; derive a presentable label from folder name
    parts = run_folder.name.split("_")
    # Folder format: YYYYMMDD_HHMMSS_word_word_...
    return " ".join(parts[2:]).replace("_", " ").title() if len(parts) > 2 else run_folder.name


def derive_date(run_folder):
    name = run_folder.name
    try:
        dt = datetime.strptime(name[:15], "%Y%m%d_%H%M%S")
        return dt.strftime("%B %-d, %Y")
    except ValueError:
        return ""


# ── Per-page flowable builder ─────────────────────────────────────────────────
def _page_elems(run_folder, report, url_hint, st, overall, averages, run_date, page_label=None, include_footer=True):
    """
    Returns the list of flowables for a single page's UX content.

    page_label: if set (e.g. "/pricing"), renders a compact section header
                instead of the full site title block. Used by stitch_reports().
                When None, renders the full standalone header + footer.
    include_footer: when False, omits the footer so the caller can append
                    additional sections (e.g. persona analysis) before adding it.
    """
    elems = []

    if page_label:
        # Stitched mode: compact page-path header
        elems.append(Spacer(1, 10))
        elems += section_header(f"Page: {page_label}", st)
        elems.append(Paragraph(url_hint, st["meta"]))
        elems.append(Spacer(1, 6))
    else:
        # Standalone mode: full site header
        hostname = urlparse(url_hint).hostname or url_hint if url_hint.startswith("http") else url_hint
        elems.append(Paragraph(hostname or "UX Evaluation", st["site"]))
        elems.append(Paragraph(url_hint, st["meta"]))
        elems.append(Paragraph(run_date, st["meta"]))
        elems.append(Spacer(1, 8))
        elems.append(HRFlowable(width="100%", thickness=2, color=NAVY, spaceAfter=14))

    # ── Screenshot embed ──────────────────────────────────────────────────────
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
            aspect = ih / iw
            img_h = min(usable_w * aspect, 120 * mm)
            img = RLImage(str(screenshot_path), width=usable_w, height=img_h)
            elems.append(img)
            elems.append(Spacer(1, 10))
        except Exception:
            pass

    # ── Overall score ─────────────────────────────────────────────────────────
    elems += section_header("Overall UX Score", st)

    bar_w_pts = PAGE_W - 2 * MARGIN - 80
    bar = score_bar(overall, bar_w=bar_w_pts, bar_h=14)
    score_label = Paragraph(f"<b>{overall:.1f} / 5.0</b>", ParagraphStyle(
        "sl", fontName="Helvetica-Bold", fontSize=14,
        textColor=score_color(overall), alignment=TA_LEFT,
    ))
    row = Table([[bar, score_label]], colWidths=[bar_w_pts, 80])
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    elems.append(row)
    elems.append(Spacer(1, 10))

    # ── Score breakdown table ─────────────────────────────────────────────────
    elems += section_header("Score Breakdown", st)

    usable_w = PAGE_W - 2 * MARGIN
    col_w = [55, (usable_w - 55) / 3, (usable_w - 55) / 3, (usable_w - 55) / 3]

    thead = [
        Paragraph("Step", st["th"]),
        Paragraph("CTA Clarity", st["th"]),
        Paragraph("Copy Quality", st["th"]),
        Paragraph("Flow Smoothness", st["th"]),
    ]
    table_data = [thead]

    for entry in report:
        conf = entry.get("confidence", "")
        label, color = confidence_label(conf)
        step_cell = Paragraph(
            f'Step {entry["step"]} <font color="#{color.hexval()[2:].upper()}">[{label}]</font>',
            st["td_left"],
        )

        def score_cell(dim):
            val = entry.get(dim)
            if not val:
                return Paragraph("—", st["td"])
            s = val.get("score", 0)
            note = val.get("note", "")
            c = score_color(s)
            note_escaped = html.escape(note[:80]) + ("…" if len(note) > 80 else "")
            return Paragraph(
                f'<font color="#{c.hexval()[2:].upper()}"><b>{s}/5</b></font><br/>'
                f'<font color="#888888" size="7">{note_escaped}</font>',
                st["td_left"],
            )

        table_data.append([step_cell, score_cell("cta_clarity"), score_cell("copy_quality"), score_cell("flow_smoothness")])

    # Averages row
    def avg_cell(dim):
        s = averages[dim]
        c = score_color(s)
        return Paragraph(f'<font color="#{c.hexval()[2:].upper()}"><b>Avg: {s:.1f}/5</b></font>', st["td"])

    table_data.append([
        Paragraph("<b>Averages</b>", st["td_left"]),
        avg_cell("cta_clarity"), avg_cell("copy_quality"), avg_cell("flow_smoothness"),
    ])

    t = Table(table_data, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),  NAVY),
        ("BACKGROUND",   (0, -1),(-1, -1), LIGHT_GREY),
        ("ROWBACKGROUNDS",(0, 1), (-1, -2), [WHITE, colors.HexColor("#f5f5f5")]),
        ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("FONTNAME",     (0, -1),(-1, -1), "Helvetica-Bold"),
    ]))
    elems.append(t)
    elems.append(Spacer(1, 10))

    # ── First impression ──────────────────────────────────────────────────────
    first_impressions = [e.get("first_impression", "") for e in report if e.get("first_impression")]
    if first_impressions:
        elems += section_header("First Impression", st)
        elems.append(Paragraph(f"\u201c{html.escape(first_impressions[-1])}\u201d", st["italic"]))
        elems.append(Spacer(1, 6))

    # ── Friction points & recommendations ────────────────────────────────────
    all_friction = []
    for entry in report:
        fps = entry.get("friction_points", [])
        recs = entry.get("recommendations", [])
        for i, fp in enumerate(fps):
            rec = recs[i] if i < len(recs) else None
            all_friction.append((fp, rec))

    if all_friction:
        elems += section_header("Friction Points & Recommendations", st)
        for fp, rec in all_friction:
            elems.append(Paragraph(f"\u2022 {html.escape(fp)}", st["bullet"]))
            if rec:
                elems.append(Paragraph(f"\u2192 {html.escape(rec)}", st["rec"]))
        elems.append(Spacer(1, 6))

    # ── Below-the-fold analysis ───────────────────────────────────────────────
    bf_path = run_folder / "below_fold.json"
    if bf_path.exists():
        below_fold = json.loads(bf_path.read_text(encoding="utf-8"))
        findings = below_fold.get("below_fold_findings", [])
        adjustments = below_fold.get("below_fold_score_adjustments", {})

        if findings or adjustments:
            elems += section_header("Below the Fold — Full-Page Analysis", st)

        if findings:
            for f in findings:
                elems.append(Paragraph(f"\u2022 {html.escape(f)}", st["bullet"]))
            elems.append(Spacer(1, 8))

        if adjustments:
            elems.append(Paragraph("Score Adjustments", ParagraphStyle(
                "adj_hdr", fontName="Helvetica-Bold", fontSize=9,
                textColor=NAVY, spaceBefore=4, spaceAfter=4,
            )))
            dim_labels = {"cta_clarity": "CTA Clarity", "copy_quality": "Copy Quality", "flow_smoothness": "Flow Smoothness"}
            adj_thead = [Paragraph(h, st["th"]) for h in ["Dimension", "Adjusted Score", "Reason"]]
            adj_data = [adj_thead]
            usable_w = PAGE_W - 2 * MARGIN
            for dim, val in adjustments.items():
                label = dim_labels.get(dim, dim.replace("_", " ").title())
                score = (val.get("adjusted_score") or val.get("adjustment", "—")) if isinstance(val, dict) else val
                reason = val.get("reason", "") if isinstance(val, dict) else ""
                c = score_color(score) if isinstance(score, (int, float)) else MID_GREY
                adj_data.append([
                    Paragraph(label, st["td_left"]),
                    Paragraph(f'<font color="#{c.hexval()[2:].upper()}"><b>{score}/5</b></font>', st["td"]),
                    Paragraph(html.escape(reason), st["td_left"]),
                ])
            adj_t = Table(adj_data, colWidths=[80, 60, usable_w - 140], repeatRows=1)
            adj_t.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, 0),  NAVY),
                ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, colors.HexColor("#f5f5f5")]),
                ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING",    (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING",   (0, 0), (-1, -1), 6),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
            ]))
            elems.append(adj_t)
            elems.append(Spacer(1, 6))

    # ── Technical Health ──────────────────────────────────────────────────────
    console_path = run_folder / "console.json"
    if console_path.exists():
        try:
            console_data = json.loads(console_path.read_text(encoding="utf-8"))
            errors = [e for e in console_data if e.get("type") == "error"]
            warnings = [e for e in console_data if e.get("type") == "warning"]
            elems += section_header("Technical Health — Console", st)
            if not errors and not warnings:
                elems.append(Paragraph("\u2705 No console errors or warnings detected", st["body"]))
            else:
                elems.append(Paragraph(
                    f"{len(errors)} error(s), {len(warnings)} warning(s) detected",
                    st["body"],
                ))
                for e in errors[:10]:
                    msg = e.get("text", "")
                    truncated = (msg[:120] + "\u2026") if len(msg) > 120 else msg
                    elems.append(Paragraph(f"\u2022 {html.escape(truncated)}", st["bullet"]))
            elems.append(Spacer(1, 6))
        except Exception:
            pass

    # ── Network Health ────────────────────────────────────────────────────────
    network_path = run_folder / "network.json"
    if network_path.exists():
        try:
            network_data = json.loads(network_path.read_text(encoding="utf-8"))
            elems += section_header("Technical Health — Network", st)
            if not network_data:
                elems.append(Paragraph("\u2705 No failed or slow network requests detected", st["body"]))
            else:
                failed = [r for r in network_data if r.get("status", 0) >= 400]
                slow = [r for r in network_data if (r.get("duration_ms") or 0) > 2000]
                elems.append(Paragraph(
                    f"{len(failed)} failed request(s) (\u2265400), {len(slow)} slow request(s) (>2s)",
                    st["body"],
                ))
                for r in network_data[:10]:
                    url_txt = r.get("url", "")
                    url_short = (url_txt[:80] + "\u2026") if len(url_txt) > 80 else url_txt
                    status = r.get("status", "?")
                    dur = r.get("duration_ms")
                    dur_txt = f" — {dur}ms" if dur is not None else ""
                    elems.append(Paragraph(
                        f"\u2022 {html.escape(url_short)} [{status}]{html.escape(dur_txt)}",
                        st["bullet"],
                    ))
            elems.append(Spacer(1, 6))
        except Exception:
            pass

    # ── Footer (standalone mode only) ─────────────────────────────────────────
    if not page_label and include_footer:
        elems.append(Spacer(1, 14))
        elems.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT_GREY, spaceAfter=6))
        elems.append(Paragraph("Generated by reasonable-ux · claude-opus-4-5", st["footer"]))

    return elems


# ── Persona analysis section ──────────────────────────────────────────────────
def add_persona_section(elems, persona_results, st):
    """Append a Multi-Lens Persona Analysis section to elems in-place."""
    elems += section_header("Multi-Lens Persona Analysis", st)
    elems.append(Spacer(1, 4))

    usable_w = PAGE_W - 2 * MARGIN
    bar_w_pts = usable_w - 80

    for i, pr in enumerate(persona_results):
        score = float(pr.get("score", 0))
        c = score_color(score)

        # Persona name left, score right
        name_p = Paragraph(
            f'<b>{html.escape(pr.get("persona_name", ""))}</b>',
            ParagraphStyle("pn", fontName="Helvetica-Bold", fontSize=11,
                           textColor=NAVY, spaceBefore=8, spaceAfter=2, leading=14),
        )
        score_p = Paragraph(
            f'<font color="#{c.hexval()[2:].upper()}"><b>{score:.1f} / 5</b></font>',
            ParagraphStyle("ps", fontName="Helvetica-Bold", fontSize=11,
                           textColor=c, alignment=TA_LEFT, spaceBefore=8, leading=14),
        )
        name_row = Table([[name_p, score_p]], colWidths=[usable_w - 70, 70])
        name_row.setStyle(TableStyle([
            ("VALIGN",       (0, 0), (-1, -1), "BOTTOM"),
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING",   (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        ]))
        elems.append(name_row)

        # Score bar
        bar = score_bar(score, bar_w=bar_w_pts, bar_h=8)
        bar_lbl = Paragraph(
            f'<font color="#{c.hexval()[2:].upper()}">{score:.1f}/5</font>',
            ParagraphStyle("pbl", fontName="Helvetica", fontSize=8,
                           textColor=c, alignment=TA_LEFT),
        )
        bar_row = Table([[bar, bar_lbl]], colWidths=[bar_w_pts, 80])
        bar_row.setStyle(TableStyle([
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING",   (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]))
        elems.append(bar_row)

        desc = pr.get("persona_description", "")
        if desc:
            elems.append(Paragraph(html.escape(desc), st["italic"]))

        findings = pr.get("key_findings", [])
        if findings:
            elems.append(Paragraph("Key Findings", ParagraphStyle(
                "pfh", fontName="Helvetica-Bold", fontSize=9,
                textColor=NAVY, spaceBefore=6, spaceAfter=3,
            )))
            for f in findings:
                elems.append(Paragraph(f"\u2022 {html.escape(f)}", st["bullet"]))

        recs = pr.get("recommendations", [])
        if recs:
            elems.append(Paragraph("Recommendations", ParagraphStyle(
                "prh", fontName="Helvetica-Bold", fontSize=9,
                textColor=NAVY, spaceBefore=6, spaceAfter=3,
            )))
            for r in recs:
                elems.append(Paragraph(f"\u2192 {html.escape(r)}", st["rec"]))

        # Divider between personas, not after the last one
        if i < len(persona_results) - 1:
            elems.append(Spacer(1, 8))
            elems.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT_GREY, spaceAfter=4))

    elems.append(Spacer(1, 10))


# ── Executive summary via Claude Haiku ───────────────────────────────────────
def _exec_summary_content(page_summaries, tech_summary=None):
    """Call Claude Haiku to synthesize top 3 findings and top 3 recommendations."""
    import anthropic

    pages_text = "\n".join(
        f"- {ps['path']}: score {ps['overall']:.1f}/5, top finding: {ps['top_finding']}"
        for ps in page_summaries
    )

    tech_block = ""
    if tech_summary:
        top_urls = ", ".join(tech_summary.get("top_offender_urls", [])[:5]) or "none"
        tech_block = (
            f"\n\nTechnical data across all pages: "
            f"{tech_summary['total_errors']} console errors, "
            f"{tech_summary['total_warnings']} warnings, "
            f"{tech_summary['total_failed_requests']} failed network requests, "
            f"{tech_summary['total_slow_requests']} slow requests (>2s). "
            f"Top offenders: {top_urls}. "
            "Use this to add a 'Technical Health' paragraph to the executive summary — "
            "flag the most significant issues, or note clean results if counts are zero. "
            "Keep it 2-3 sentences."
        )

    prompt = (
        f"You evaluated {len(page_summaries)} pages of a web application for UX quality.\n\n"
        f"Page results:\n{pages_text}"
        f"{tech_block}\n\n"
        "Synthesize the top 3 most severe UX findings and top 3 most actionable recommendations "
        "across all pages. Also write a 'technical_health' paragraph (2-3 sentences) about "
        "console and network health. "
        "Respond ONLY with valid JSON in this exact shape: "
        '{"findings": ["...", "...", "..."], "recommendations": ["...", "...", "..."], '
        '"technical_health": "...", "overall_assessment": "..."}'
    )
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=768,
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(msg.content[0].text)
        return (
            data.get("findings", [])[:3],
            data.get("recommendations", [])[:3],
            data.get("technical_health", ""),
            data.get("overall_assessment", ""),
        )
    except Exception as e:
        print(f"⚠️  Executive summary generation failed: {e}")
        return [], [], "", ""


# ── PDF builder ───────────────────────────────────────────────────────────────
def build_pdf(run_folder, report, url_hint, output_path, persona_results=None):
    st = styles()
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=MARGIN,
        title="UX Evaluation Report",
        author="reasonable-ux",
    )

    overall, averages = avg_scores(report)
    run_date = derive_date(run_folder)

    elems = _page_elems(run_folder, report, url_hint, st, overall, averages, run_date,
                        include_footer=persona_results is None)
    if persona_results:
        add_persona_section(elems, persona_results, st)
        elems.append(Spacer(1, 14))
        elems.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT_GREY, spaceAfter=6))
        elems.append(Paragraph("Generated by reasonable-ux · claude-opus-4-5", st["footer"]))

    doc.build(elems)
    print(f"✅ PDF saved: {output_path}")


# ── Multi-page PDF stitcher ───────────────────────────────────────────────────
def stitch_reports(page_results, base_url, output_path, persona_results=None):
    """
    Produce a single unified PDF from multiple per-page UX runs.

    page_results   : list of dicts — {path, url, run_folder, report}
    base_url       : root URL string, used in the cover header
    output_path    : Path for the output PDF
    persona_results: optional list of persona result dicts from orchestrate()
    """
    st = styles()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=MARGIN,
        title="Multi-Page UX Evaluation Report",
        author="reasonable-ux",
    )

    # ── Compute per-page scores ───────────────────────────────────────────────
    page_summaries = []
    all_scores = []
    for pr in page_results:
        overall_page, averages_page = avg_scores(pr["report"])
        all_scores.append(overall_page)

        # Top finding: first friction point from any step, else first impression
        top_finding = ""
        for entry in pr["report"]:
            fps = entry.get("friction_points", [])
            if fps:
                top_finding = fps[0]
                break
        if not top_finding:
            for entry in pr["report"]:
                fi = entry.get("first_impression", "")
                if fi:
                    top_finding = fi
                    break

        page_summaries.append({
            "path":        pr["path"],
            "url":         pr["url"],
            "run_folder":  pr["run_folder"],
            "report":      pr["report"],
            "overall":     overall_page,
            "averages":    averages_page,
            "run_date":    derive_date(pr["run_folder"]),
            "top_finding": top_finding,
        })

    grand_overall = sum(all_scores) / len(all_scores) if all_scores else 0
    hostname = urlparse(base_url).hostname or base_url.replace("https://", "").replace("http://", "")
    run_date = datetime.now().strftime("%B %-d, %Y")

    elems = []

    # ── Aggregate technical data across all pages ─────────────────────────────
    tech_summary = {
        "total_errors": 0,
        "total_warnings": 0,
        "total_failed_requests": 0,
        "total_slow_requests": 0,
        "top_offender_urls": [],
    }
    url_counts = {}
    for ps in page_summaries:
        console_path = ps["run_folder"] / "console.json"
        if console_path.exists():
            try:
                console_data = json.loads(console_path.read_text(encoding="utf-8"))
                tech_summary["total_errors"] += sum(1 for e in console_data if e.get("type") == "error")
                tech_summary["total_warnings"] += sum(1 for e in console_data if e.get("type") == "warning")
            except Exception:
                pass
        network_path = ps["run_folder"] / "network.json"
        if network_path.exists():
            try:
                network_data = json.loads(network_path.read_text(encoding="utf-8"))
                tech_summary["total_failed_requests"] += sum(1 for r in network_data if r.get("status", 0) >= 400)
                tech_summary["total_slow_requests"] += sum(1 for r in network_data if (r.get("duration_ms") or 0) > 2000)
                for r in network_data:
                    u = r.get("url", "")
                    url_counts[u] = url_counts.get(u, 0) + 1
            except Exception:
                pass
    tech_summary["top_offender_urls"] = [
        u for u, _ in sorted(url_counts.items(), key=lambda x: -x[1])[:5]
    ]

    # ── Executive summary (first page) ───────────────────────────────────────
    print("🧠 Generating executive summary...")
    exec_findings, exec_recs, exec_tech_health, exec_overall = _exec_summary_content(
        page_summaries, tech_summary=tech_summary
    )

    elems.append(Paragraph(hostname or "UX Evaluation", st["site"]))
    elems.append(Paragraph("Executive Summary", ParagraphStyle(
        "exec_subtitle", fontName="Helvetica", fontSize=13, textColor=MID_GREY,
        spaceAfter=4, leading=16,
    )))
    elems.append(Paragraph(run_date, st["meta"]))
    elems.append(Paragraph(f"{len(page_summaries)} page(s) evaluated", st["meta"]))
    elems.append(Spacer(1, 8))
    elems.append(HRFlowable(width="100%", thickness=2, color=NAVY, spaceAfter=14))

    elems += section_header("Overall Score", st)
    exec_bar_w = PAGE_W - 2 * MARGIN - 80
    exec_bar = score_bar(grand_overall, bar_w=exec_bar_w, bar_h=14)
    exec_score_lbl = Paragraph(f"<b>{grand_overall:.1f} / 5.0</b>", ParagraphStyle(
        "exec_sl", fontName="Helvetica-Bold", fontSize=14,
        textColor=score_color(grand_overall), alignment=TA_LEFT,
    ))
    exec_score_row = Table([[exec_bar, exec_score_lbl]], colWidths=[exec_bar_w, 80])
    exec_score_row.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    elems.append(exec_score_row)
    elems.append(Spacer(1, 14))

    elems += section_header("UX Summary", st)
    if exec_findings:
        elems.append(Paragraph("<b>Top 3 Findings</b>", st["body"]))
        for f in exec_findings:
            elems.append(Paragraph(f"\u2022 {html.escape(f)}", st["bullet"]))
        elems.append(Spacer(1, 6))
    if exec_recs:
        elems.append(Paragraph("<b>Top 3 Recommendations</b>", st["body"]))
        for r in exec_recs:
            elems.append(Paragraph(f"\u2192 {html.escape(r)}", st["rec"]))
        elems.append(Spacer(1, 10))

    elems += section_header("Technical Health", st)
    if exec_tech_health:
        elems.append(Paragraph(html.escape(exec_tech_health), st["body"]))
    else:
        errors = tech_summary["total_errors"]
        warnings = tech_summary["total_warnings"]
        failed = tech_summary["total_failed_requests"]
        slow = tech_summary["total_slow_requests"]
        if errors == 0 and warnings == 0 and failed == 0 and slow == 0:
            elems.append(Paragraph("\u2705 No console errors, warnings, or network issues detected across all pages.", st["body"]))
        else:
            elems.append(Paragraph(
                f"{errors} console error(s), {warnings} warning(s), "
                f"{failed} failed network request(s), {slow} slow request(s) detected.",
                st["body"],
            ))
    elems.append(Spacer(1, 10))

    if exec_overall:
        elems += section_header("Overall Assessment", st)
        elems.append(Paragraph(html.escape(exec_overall), st["body"]))
        elems.append(Spacer(1, 10))

    elems.append(PageBreak())

    # ── Cover: site header ────────────────────────────────────────────────────
    elems.append(Paragraph(hostname or "UX Evaluation", st["site"]))
    elems.append(Paragraph("Multi-Page UX Evaluation", ParagraphStyle(
        "subtitle", fontName="Helvetica", fontSize=13, textColor=MID_GREY,
        spaceAfter=4, leading=16,
    )))
    elems.append(Paragraph(run_date, st["meta"]))
    elems.append(Spacer(1, 8))
    elems.append(HRFlowable(width="100%", thickness=2, color=NAVY, spaceAfter=14))

    # ── Cover: overall score bar ──────────────────────────────────────────────
    elems += section_header("Overall Score Across All Pages", st)
    bar_w_pts = PAGE_W - 2 * MARGIN - 80
    bar = score_bar(grand_overall, bar_w=bar_w_pts, bar_h=14)
    score_lbl = Paragraph(f"<b>{grand_overall:.1f} / 5.0</b>", ParagraphStyle(
        "sl2", fontName="Helvetica-Bold", fontSize=14,
        textColor=score_color(grand_overall), alignment=TA_LEFT,
    ))
    cover_row = Table([[bar, score_lbl]], colWidths=[bar_w_pts, 80])
    cover_row.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    elems.append(cover_row)
    elems.append(Spacer(1, 14))

    # ── Cover: per-page summary table ─────────────────────────────────────────
    elems += section_header("Page Summary", st)
    usable_w = PAGE_W - 2 * MARGIN
    sum_col_w = [80, 55, usable_w - 135]
    sum_thead = [
        Paragraph("Page", st["th"]),
        Paragraph("Score", st["th"]),
        Paragraph("Top Finding", st["th"]),
    ]
    sum_data = [sum_thead]
    for ps in page_summaries:
        c = score_color(ps["overall"])
        top = ps["top_finding"]
        sum_data.append([
            Paragraph(ps["path"], st["td_left"]),
            Paragraph(
                f'<font color="#{c.hexval()[2:].upper()}"><b>{ps["overall"]:.1f}/5</b></font>',
                st["td"],
            ),
            Paragraph(
                (top[:120] + "\u2026") if len(top) > 120 else top,
                st["td_left"],
            ),
        ])
    sum_t = Table(sum_data, colWidths=sum_col_w, repeatRows=1)
    sum_t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  NAVY),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [WHITE, colors.HexColor("#f5f5f5")]),
        ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
    ]))
    elems.append(sum_t)

    # ── Per-page detail sections ──────────────────────────────────────────────
    for ps in page_summaries:
        elems.append(PageBreak())
        elems += _page_elems(
            ps["run_folder"], ps["report"], ps["url"],
            st, ps["overall"], ps["averages"], ps["run_date"],
            page_label=ps["path"],
        )

    # ── Persona analysis ──────────────────────────────────────────────────────
    if persona_results:
        elems.append(PageBreak())
        add_persona_section(elems, persona_results, st)

    # ── Footer ────────────────────────────────────────────────────────────────
    elems.append(Spacer(1, 14))
    elems.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT_GREY, spaceAfter=6))
    elems.append(Paragraph("Generated by reasonable-ux · claude-opus-4-5", st["footer"]))

    doc.build(elems)
    print(f"✅ Multi-page PDF saved: {output_path}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a UX evaluation PDF from a run report")
    parser.add_argument("--run", type=str, default=None, help="Path to a specific run folder (default: most recent UX run)")
    parser.add_argument("--url", type=str, default=None, help="Site URL to display in the report header")
    args = parser.parse_args()

    if args.run:
        run_folder = Path(args.run)
        rp = run_folder / "report.json"
        if not rp.exists():
            sys.exit(f"No report.json found in {run_folder}")
        report = json.loads(rp.read_text(encoding="utf-8"))
        if not report or "cta_clarity" not in report[0]:
            sys.exit("Specified run does not appear to be a UX mode run")
    else:
        run_folder, report = find_latest_ux_run()

    url_hint = args.url or "https://depreciationpro.com"
    safe_domain = url_hint.replace("https://", "").replace("http://", "").rstrip("/")
    safe_domain = safe_domain.replace(".", "_").replace("/", "_")
    iso_date = run_folder.name[:8]  # YYYYMMDD from folder name e.g. 20260407_...
    iso_date = f"{iso_date[:4]}-{iso_date[4:6]}-{iso_date[6:8]}"
    output_path = run_folder / f"{safe_domain}_{iso_date}.pdf"

    print(f"📄 Generating PDF for: {run_folder.name}")
    build_pdf(run_folder, report, url_hint, output_path)
