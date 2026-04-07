import argparse
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
from reportlab.platypus import (
    HRFlowable,
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


# ── PDF builder ───────────────────────────────────────────────────────────────
def build_pdf(run_folder, report, url_hint, output_path):
    st = styles()
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN,  bottomMargin=MARGIN,
        title="UX Evaluation Report",
        author="reasons-qagent",
    )

    overall, averages = avg_scores(report)
    run_date = derive_date(run_folder)

    elems = []

    # ── Header ────────────────────────────────────────────────────────────────
    hostname = urlparse(url_hint).hostname or url_hint if url_hint.startswith("http") else url_hint
    elems.append(Paragraph(hostname or "UX Evaluation", st["site"]))
    elems.append(Paragraph(url_hint, st["meta"]))
    elems.append(Paragraph(run_date, st["meta"]))
    elems.append(Spacer(1, 8))
    elems.append(HRFlowable(width="100%", thickness=2, color=NAVY, spaceAfter=14))

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
            return Paragraph(
                f'<font color="#{c.hexval()[2:].upper()}"><b>{s}/5</b></font><br/>'
                f'<font color="#888888" size="7">{note[:80]}{"…" if len(note) > 80 else ""}</font>',
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
    # Use the last step's first_impression that has one
    first_impressions = [e.get("first_impression", "") for e in report if e.get("first_impression")]
    if first_impressions:
        elems += section_header("First Impression", st)
        elems.append(Paragraph(f"\u201c{first_impressions[-1]}\u201d", st["italic"]))
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
            elems.append(Paragraph(f"\u2022 {fp}", st["bullet"]))
            if rec:
                elems.append(Paragraph(f"\u2192 {rec}", st["rec"]))
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
                elems.append(Paragraph(f"\u2022 {f}", st["bullet"]))
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
                    Paragraph(reason, st["td_left"]),
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

    # ── Footer ────────────────────────────────────────────────────────────────
    elems.append(Spacer(1, 14))
    elems.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT_GREY, spaceAfter=6))
    elems.append(Paragraph("Generated by reasons-qagent · claude-opus-4-5", st["footer"]))

    doc.build(elems)
    print(f"✅ PDF saved: {output_path}")


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
