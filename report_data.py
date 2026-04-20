"""Normalize raw step-level report JSON into the designer's structured shape.

Python port of design_bundle/.../shared/report-data.js. Consumed by the Jinja
templates introduced in Phase C of the ReportLab → HTML+Playwright migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse


def scores_to_severity(avg: float) -> int:
    """Raw 1-5 subscore average (higher = better) → Nielsen 0-4 severity."""
    if avg >= 4.5:
        return 0
    if avg >= 4.0:
        return 1
    if avg >= 3.5:
        return 2
    if avg >= 2.5:
        return 3
    return 4


SEVERITY_META: list[dict[str, Any]] = [
    {"level": 0, "label": "Cosmetic", "short": "0", "tone": "#A8A89F"},
    {"level": 1, "label": "Minor",    "short": "1", "tone": "#B8A878"},
    {"level": 2, "label": "Moderate", "short": "2", "tone": "#C9843B"},
    {"level": 3, "label": "Major",    "short": "3", "tone": "#B84C2C"},
    {"level": 4, "label": "Critical", "short": "4", "tone": "#7E1A1A"},
]


DEFAULT_PERSONAS: list[dict[str, Any]] = [
    {
        "id": "team-lead",
        "role": "Design Team Lead",
        "company": "Series B fintech · 40-person design org",
        "archetype": "Evaluator",
        "goal": "Standardize on one collaborative design tool across 5 squads without blowing the Q3 budget.",
        "jtbd": "When my team outgrows Sketch files in Dropbox, I want to pilot a tool with real-time collab so I can ship faster without onboarding drag.",
        "frustrations": [
            "Opaque enterprise pricing that requires a sales call",
            "Feature bundles that hide what you're actually paying for",
            "Migrations that stall because ICs resist new tools",
        ],
        "success": [
            "Gets a per-seat cost she can defend to finance in under 4 min",
            "Can forward one link that sells the tool to her directors",
        ],
        "techSavvy": 5,
        "context": "Desktop, dual-monitor, mid-meeting. Often evaluates 3 tools in parallel.",
        "quote": "If I can't explain the pricing to my VP in one screenshot, I'm not buying it.",
        "color": "#7b2cbf",
    },
    {
        "id": "ic-designer",
        "role": "Senior Product Designer",
        "company": "Late-stage marketplace · design of 12",
        "archetype": "Practitioner",
        "goal": "Move faster on prototypes without fighting the tool.",
        "jtbd": "When I start a new flow, I want prebuilt components and fast handoff so I can focus on the problem, not the file.",
        "frustrations": [
            "Marketing pages that hide the actual product behind hero videos",
            "Having to scroll past three testimonials to see pricing",
            "Vague taglines that don't say what the tool does",
        ],
        "success": [
            "Sees a product demo above the fold in under 10 seconds",
            "Can try the tool without a signup wall",
        ],
        "techSavvy": 5,
        "context": "Laptop, personal tab-hoarding session at 10pm before a sprint.",
        "quote": "Show me the canvas, not another animated hero.",
        "color": "#2c7bbf",
    },
    {
        "id": "founder",
        "role": "Founder / Product Generalist",
        "company": "Pre-seed · team of 4",
        "archetype": "Outsider",
        "goal": "Ship a credible MVP without hiring a designer.",
        "jtbd": "When I need to mock a landing page before a pitch, I want templates and AI help so I don't have to learn vectors.",
        "frustrations": [
            "Eight products with names she can't parse (Make, Buzz, Draw…)",
            "Jargon like 'Full seat' with no definition",
            "Pricing tiers that assume you're already a team",
        ],
        "success": [
            "Understands in 30 sec which product solves her problem",
            "Finds a solo-founder-friendly tier without talking to sales",
        ],
        "techSavvy": 3,
        "context": "Phone first, laptop second. 5 minutes between investor calls.",
        "quote": "I just want to know which one of these eight things I need.",
        "color": "#bf2c7b",
    },
]


def _average(values: list[Any]) -> float:
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not nums:
        return 0.0
    return sum(nums) / len(nums)


def _avg_field(items: list[dict], pick) -> float:
    return _average([pick(s) for s in items])


def _round_to(n: float, places: int) -> float:
    factor = 10 ** places
    return round(n * factor) / factor


def _derive_site(raw_steps: list[dict]) -> dict[str, str]:
    for s in raw_steps:
        if s.get("action") == "navigate":
            tgt = s.get("target")
            if isinstance(tgt, str) and (tgt.startswith("http://") or tgt.startswith("https://")):
                u = urlparse(tgt)
                if u.hostname and u.scheme and u.netloc:
                    host = u.hostname[4:] if u.hostname.startswith("www.") else u.hostname
                    return {"name": host, "url": f"{u.scheme}://{u.netloc}"}
    return {"name": "site under review", "url": ""}


_PATH_LABELS = {
    "": "Home",
    "pricing": "Pricing",
    "features": "Features",
    "product": "Product",
    "products": "Products",
    "signup": "Signup",
    "sign-up": "Signup",
    "register": "Signup",
    "login": "Login",
    "signin": "Login",
    "sign-in": "Login",
    "dashboard": "Dashboard",
    "about": "About",
    "contact": "Contact",
    "blog": "Blog",
    "faq": "FAQ",
    "demo": "Demo",
}


def _derive_label(step: dict) -> str:
    url = step.get("url")
    if url:
        path = urlparse(url).path.strip("/")
        segment = path.split("/")[-1].lower() if path else ""
        if segment in _PATH_LABELS:
            return _PATH_LABELS[segment]
        if segment:
            return segment.replace("-", " ").replace("_", " ").title()
        return "Home"
    return f"Step {step.get('step')}"


_THEME_BUCKETS: list[dict[str, Any]] = [
    {"key": "messaging", "label": "Headline & messaging clarity",
     "kws": ["headline", "tagline", "vague", "generic", "unclear", "doesn't communicate"]},
    {"key": "pricing", "label": "Pricing transparency",
     "kws": ["pricing", "price", "cost", "seat", "billed", "annual", "monthly", "tier"]},
    {"key": "cta", "label": "CTA clarity & redundancy",
     "kws": ["cta", "get started", "button", "similar", "redundan"]},
    {"key": "terminology", "label": "Jargon & undefined terms",
     "kws": ["terminology", "unexplained", "not explained", "jargon", "full seat", "unclear what"]},
    {"key": "density", "label": "Information density & overload",
     "kws": ["overwhelm", "too many", "eight", "cognitive", "complexity", "overload", "cluttered"]},
    {"key": "discoverability", "label": "Discoverability & scannability",
     "kws": ["scroll", "below the fold", "visible", "hidden", "buried", "above the fold"]},
]


def _cluster_themes(steps: list[dict]) -> list[dict[str, Any]]:
    buckets = [
        {"key": b["key"], "label": b["label"], "kws": b["kws"], "hits": 0, "examples": []}
        for b in _THEME_BUCKETS
    ]
    for s in steps:
        for f in s["friction"]:
            t = f["text"].lower()
            for b in buckets:
                if any(k in t for k in b["kws"]):
                    b["hits"] += 1
                    if len(b["examples"]) < 2:
                        b["examples"].append(f)
                    break
    return sorted([b for b in buckets if b["hits"] > 0], key=lambda b: -b["hits"])


def load(
    raw_steps: list[dict],
    personas: list[dict] | None = None,
    site: dict | None = None,
    **opts: Any,
) -> dict[str, Any]:
    personas = personas if personas is not None else DEFAULT_PERSONAS
    site = site if site is not None else _derive_site(raw_steps)

    steps: list[dict[str, Any]] = []
    for i, s in enumerate(raw_steps):
        cta = (s.get("cta_clarity") or {}).get("score")
        copy = (s.get("copy_quality") or {}).get("score")
        flow = (s.get("flow_smoothness") or {}).get("score")
        subs = {"cta": cta, "copy": copy, "flow": flow}
        avg = _average([cta, copy, flow])
        agent_sev = s.get("severity")
        if isinstance(agent_sev, int) and not isinstance(agent_sev, bool) and 0 <= agent_sev <= 4:
            severity = agent_sev
        else:
            severity = scores_to_severity(avg)
        step_idx = s.get("step") if s.get("step") is not None else i + 1
        friction = [
            {"id": f"s{step_idx}-f{fi}", "text": text, "severity": severity, "stepIndex": step_idx}
            for fi, text in enumerate(s.get("friction_points") or [])
        ]
        recommendations = []
        for ri, text in enumerate(s.get("recommendations") or []):
            if len(text) > 120:
                effort = 3
            elif len(text) > 70:
                effort = 2
            else:
                effort = 1
            recommendations.append({
                "id": f"s{step_idx}-r{ri}",
                "text": text,
                "stepIndex": step_idx,
                "impact": min(4, severity + 1),
                "effort": effort,
            })
        steps.append({
            "index": step_idx,
            "screenshot": s.get("screenshot"),
            "observation": s.get("observation"),
            "firstImpression": s.get("first_impression"),
            "verdict": s.get("verdict"),
            "action": s.get("action"),
            "target": s.get("target"),
            "subs": subs,
            "subNotes": {
                "cta": (s.get("cta_clarity") or {}).get("note"),
                "copy": (s.get("copy_quality") or {}).get("note"),
                "flow": (s.get("flow_smoothness") or {}).get("note"),
            },
            "avg": avg,
            "severity": severity,
            "friction": friction,
            "recommendations": recommendations,
            "pageLabel": _derive_label(s),
            "confidence": s.get("confidence"),
        })

    for i, step in enumerate(steps):
        step["personaId"] = personas[i % len(personas)]["id"]

    avg_scores = {
        "cta": _round_to(_avg_field(steps, lambda s: s["subs"]["cta"]), 1),
        "copy": _round_to(_avg_field(steps, lambda s: s["subs"]["copy"]), 1),
        "flow": _round_to(_avg_field(steps, lambda s: s["subs"]["flow"]), 1),
    }
    overall = _round_to((avg_scores["cta"] + avg_scores["copy"] + avg_scores["flow"]) / 3, 1)

    friction_by_severity = [0, 0, 0, 0, 0]
    for s in steps:
        for f in s["friction"]:
            friction_by_severity[f["severity"]] += 1
    total_friction = sum(friction_by_severity)
    total_recs = sum(len(s["recommendations"]) for s in steps)

    themes = _cluster_themes(steps)

    all_recs: list[dict[str, Any]] = []
    for s in steps:
        for r in s["recommendations"]:
            all_recs.append({**r, "persona": s["personaId"]})
    prioritized = sorted(all_recs, key=lambda r: (-r["impact"], r["effort"]))
    quick_wins = [r for r in prioritized if r["effort"] <= 1 and r["impact"] >= 2]
    strategic = [r for r in prioritized if r["effort"] >= 3]

    return {
        "site": site,
        "personas": personas,
        "steps": steps,
        "metrics": {
            "stepsCount": len(steps),
            "personasCount": len(personas),
            "totalFriction": total_friction,
            "totalRecs": total_recs,
            "overall": overall,
            "avgScores": avg_scores,
            "frictionBySeverity": friction_by_severity,
        },
        "themes": themes,
        "recommendations": {"all": prioritized, "quickWins": quick_wins, "strategic": strategic},
        "severityMeta": SEVERITY_META,
        "meta": {
            "date": opts.get("date") or datetime.now().strftime("%B %d, %Y"),
            "evaluator": opts.get("evaluator") or "ReasonableUX",
            "duration": opts.get("duration") or f"{len(steps)} steps · ~{len(steps) * 2} min walkthrough",
        },
    }
