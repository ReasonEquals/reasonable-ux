"""Build the frozen variant evaluation corpus.

Reads the 12 batch-68 suite reports via compare_variants.find_page_dirs() and writes
text-only excerpts to evals/variant_corpus/{variant}/{site}.json. The corpus is the
reproducible input to judge_variants.py — once committed, the LLM-judge run is
re-runnable even after the agent prompt changes.

Strips: screenshot paths, input_tokens, output_tokens.
Keeps:  step, url, observation, action, target, pass_fail, verdict,
        cta_clarity, copy_quality, flow_smoothness, severity, first_impression,
        friction_points, recommendations, confidence, persona.

Run once after the 12 batch-68 suites land. Re-run to regenerate from updated runs.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# isort: split
from compare_variants import (  # noqa: E402
    SITE_DOMAINS,
    SUITE_VARIANTS,
    _parse_page_dt,
    _parse_suite_dt,
)

CORPUS_DIR = REPO_ROOT / "evals" / "variant_corpus"
RUNS_DIR = REPO_ROOT / "runs"

DROP_KEYS = {"screenshot", "input_tokens", "output_tokens"}


def _strip_step(step: dict) -> dict:
    return {k: v for k, v in step.items() if k not in DROP_KEYS}


def _suite_end_times() -> dict[str, datetime]:
    """Window upper bound = min(next-same-site suite start, suite_start + 30min).

    compare_variants.find_page_dirs uses a flat 30-min window, which leaks v4 pages
    into v3 corpora when same-site spacing is <30min (v3→v4 stripe = 29min). A pure
    next-suite cap pulls in stray ad-hoc runs (e.g., v1→v2 linear = 12h includes a
    middle-of-the-night test). Take the tighter of the two.
    """
    by_site: dict[str, list[tuple[datetime, str]]] = {}
    for suite_id, (_variant, site) in SUITE_VARIANTS.items():
        by_site.setdefault(site, []).append((_parse_suite_dt(suite_id), suite_id))
    ends: dict[str, datetime] = {}
    for items in by_site.values():
        items.sort()
        for i, (start, sid) in enumerate(items):
            cap = start + timedelta(minutes=30)
            next_start = items[i + 1][0] if i + 1 < len(items) else None
            ends[sid] = min(cap, next_start) if next_start else cap
    return ends


def _page_dirs_for(suite_id: str, site: str, window_end: datetime) -> list[Path]:
    domain_dir = RUNS_DIR / SITE_DOMAINS[site]
    if not domain_dir.exists():
        return []
    suite_dt = _parse_suite_dt(suite_id)
    matches = []
    for child in sorted(domain_dir.iterdir()):
        if not child.is_dir() or not child.name.endswith("_single_page"):
            continue
        page_dt = _parse_page_dt(child.name)
        if page_dt is None:
            continue
        if suite_dt <= page_dt < window_end:
            matches.append(child)
    return matches


def build() -> int:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    end_times = _suite_end_times()
    written = 0
    for suite_id, (variant, site) in SUITE_VARIANTS.items():
        page_dirs = _page_dirs_for(suite_id, site, end_times[suite_id])
        if not page_dirs:
            print(f"  ! no page dirs for {variant}/{site} ({suite_id})")
            continue
        all_steps = []
        for page_dir in page_dirs:
            report_path = page_dir / "report.json"
            if not report_path.exists():
                continue
            with report_path.open() as fh:
                steps = json.load(fh)
            if not isinstance(steps, list):
                continue
            for step in steps:
                all_steps.append(_strip_step(step))
        if not all_steps:
            continue
        out_dir = CORPUS_DIR / variant
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{site}.json"
        out_path.write_text(json.dumps(all_steps, indent=2))
        size_kb = out_path.stat().st_size / 1024
        print(f"  wrote {variant}/{site}.json ({len(all_steps)} steps, {size_kb:.1f}KB)")
        written += 1
    print(f"\n{written}/12 corpus files written to {CORPUS_DIR}")
    return 0 if written == 12 else 1


if __name__ == "__main__":
    raise SystemExit(build())
