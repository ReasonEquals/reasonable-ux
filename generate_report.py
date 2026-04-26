import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import jinja2
from playwright.async_api import async_playwright


# ── Data helpers ──────────────────────────────────────────────────────────────
def find_latest_ux_run(runs_dir="runs"):
    import contextlib
    candidates = []
    for folder in sorted(Path(runs_dir).iterdir(), reverse=True):
        if not folder.is_dir():
            continue
        rp = folder / "report.json"
        if not rp.exists():
            continue
        with contextlib.suppress(OSError, json.JSONDecodeError, IndexError):
            data = json.loads(rp.read_text(encoding="utf-8"))
            if data and "cta_clarity" in data[0]:
                candidates.append((folder, data))
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


def build_pdf(run_folder, report, url_hint, output_path, persona_results=None, *, compact=False, theme="editorial"):
    import report_data as _rd
    normalized = _rd.load(
        report,
        personas=_resolve_personas(run_folder, persona_results),
        site={"name": url_hint, "url": url_hint},
        date=_date_from_run_folder(run_folder),
    )
    normalized["theme"] = theme
    _rewrite_screenshot_paths(normalized, run_folder)
    template_name = "compact.html.j2" if compact else "full.html.j2"
    html_str = _render_jinja(template_name, normalized)
    _render_pdf_via_playwright(html_str, Path(run_folder), Path(output_path))
    print(f"✅ PDF saved: {output_path}")


def _deduplicate_findings(page_summaries: list[dict]) -> None:
    """Clear duplicate top_finding strings across page summaries (mutates in place)."""
    seen: set[str] = set()
    for ps in page_summaries:
        tf = ps.get("top_finding", "")
        if tf and tf in seen:
            ps["top_finding"] = ""
        elif tf:
            seen.add(tf)


# ── Multi-page PDF stitcher ───────────────────────────────────────────────────
def stitch_reports(page_results, base_url, output_path, persona_results=None, *, theme="editorial"):
    """Render the full template over a merged view of every page's steps.

    Preserves `_exec_summary_content` — its output becomes the `execSummary`
    context key the full template renders on the exec-summary and appendix
    pages. Personas resolve in priority order: persona_results → the most
    recent page's personas.json → DEFAULT_PERSONAS.
    """
    import contextlib

    import report_data as _rd

    page_summaries = []
    for pr in page_results:
        overall_page, _ = avg_scores(pr["report"])
        top_finding = next(
            (fp for e in pr["report"] for fp in e.get("friction_points", []) if fp), "",
        )
        if not top_finding:
            top_finding = next(
                (e.get("first_impression", "") for e in pr["report"] if e.get("first_impression")),
                "",
            )
        verdict = next(
            (e.get("verdict", "") for e in reversed(pr["report"])
             if e.get("action") == "done" and e.get("verdict")),
            next((e.get("verdict", "") for e in reversed(pr["report"]) if e.get("verdict")), ""),
        )
        page_summaries.append({
            "path":        pr["path"],
            "url":         pr["url"],
            "run_folder":  pr["run_folder"],
            "overall":     overall_page,
            "top_finding": top_finding,
            "verdict":     verdict,
        })

    tech = {"total_errors": 0, "total_warnings": 0,
            "total_failed_requests": 0, "total_slow_requests": 0,
            "top_offender_urls": []}
    url_counts = {}
    for ps in page_summaries:
        console_path = ps["run_folder"] / "console.json"
        network_path = ps["run_folder"] / "network.json"
        if console_path.exists():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                cd = json.loads(console_path.read_text(encoding="utf-8"))
                tech["total_errors"]   += sum(1 for e in cd if e.get("type") == "error")
                tech["total_warnings"] += sum(1 for e in cd if e.get("type") == "warning")
        if network_path.exists():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                nd = json.loads(network_path.read_text(encoding="utf-8"))
                tech["total_failed_requests"] += sum(1 for r in nd if r.get("status", 0) >= 400)
                tech["total_slow_requests"]   += sum(1 for r in nd if (r.get("duration_ms") or 0) > 2000)
                for r in nd:
                    u = r.get("url", "")
                    url_counts[u] = url_counts.get(u, 0) + 1
    tech["top_offender_urls"] = [u for u, _ in sorted(url_counts.items(), key=lambda x: -x[1])[:5]]

    _deduplicate_findings(page_summaries)

    print("🧠 Generating executive summary...")
    exec_findings, exec_recs, exec_tech_health, exec_overall = _exec_summary_content(
        page_summaries, tech_summary=tech,
    )

    # Merge every page's raw steps into one ordered list. _derive_label keys
    # off each step's per-URL field (batch 20e), so page labels stay accurate.
    raw_steps = []
    for pr in page_results:
        raw_steps.extend(pr["report"])

    hostname = (urlparse(base_url).hostname
                or base_url.replace("https://", "").replace("http://", ""))

    latest_run_folder = (
        max((ps["run_folder"] for ps in page_summaries),
            key=lambda f: f.stat().st_mtime)
        if page_summaries else None
    )
    run_date = (
        _date_from_run_folder(latest_run_folder)
        if latest_run_folder
        else datetime.now().strftime("%B %d, %Y")
    )

    normalized = _rd.load(
        raw_steps,
        personas=_resolve_personas(latest_run_folder, persona_results),
        site={"name": hostname, "url": base_url},
        date=run_date,
    )
    normalized["execSummary"] = {
        "findings":          exec_findings,
        "recommendations":   exec_recs,
        "technicalHealth":   exec_tech_health,
        "overallAssessment": exec_overall,
    }
    normalized["theme"] = theme
    _rewrite_screenshot_paths(normalized, latest_run_folder)

    html_str = _render_jinja("full.html.j2", normalized)
    _render_pdf_via_playwright(html_str, latest_run_folder, Path(output_path))
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
