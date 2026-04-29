"""Compare the 4 multi-page suite variants run on 2026-04-28/29.

v1_baseline       : 4 steps, no advisor
v2_advisor        : 4 steps, advisor on
v3_8step          : 8 steps, no advisor
v4_8step_advisor  : 8 steps, advisor on

Three sites per variant: stripe, linear, glossier. 12 suites total.

Reads cost_log.csv for cost/token data and per-page report.json for scores
(cta_clarity, copy_quality, flow_smoothness). Emits a markdown table and a
matplotlib PNG into artifacts/.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent
RUNS_DIR = REPO_ROOT / "runs"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
COST_LOG = RUNS_DIR / "cost_log.csv"

# Chronological mapping confirmed by Ryan: variants ran in order v1, v2, v3, v4.
SUITE_VARIANTS: dict[str, tuple[str, str]] = {
    # v1_baseline — 2026-04-28 evening
    "suite_20260428_212327": ("v1_baseline", "stripe"),
    "suite_20260428_213703": ("v1_baseline", "linear"),
    "suite_20260428_215019": ("v1_baseline", "glossier"),
    # v2_advisor — 2026-04-29 ~9:42-10:14
    "suite_20260429_092848": ("v2_advisor", "stripe"),
    "suite_20260429_094309": ("v2_advisor", "linear"),
    "suite_20260429_095610": ("v2_advisor", "glossier"),
    # v3_8step — 2026-04-29 ~10:21-10:42
    "suite_20260429_101439": ("v3_8step", "stripe"),
    "suite_20260429_102855": ("v3_8step", "linear"),
    "suite_20260429_103641": ("v3_8step", "glossier"),
    # v4_8step_advisor — 2026-04-29 ~10:54-11:16
    "suite_20260429_104348": ("v4_8step_advisor", "stripe"),
    "suite_20260429_105420": ("v4_8step_advisor", "linear"),
    "suite_20260429_110424": ("v4_8step_advisor", "glossier"),
}

SITE_DOMAINS: dict[str, str] = {
    "stripe": "stripe_com",
    "linear": "linear_app",
    "glossier": "glossier_com",
}

VARIANT_ORDER = ["v1_baseline", "v2_advisor", "v3_8step", "v4_8step_advisor"]
SITE_ORDER = ["stripe", "linear", "glossier"]

# Page folders are stamped within seconds of the suite_id and never bleed past
# the next suite for the same site (suites ran sequentially, ~10+ min apart).
SUITE_PAGE_WINDOW = timedelta(minutes=30)


@dataclass
class VariantRow:
    variant: str
    site: str
    suite_id: str
    total_tokens: int
    cost_usd: float
    step_count: int
    cta_clarity: float
    copy_quality: float
    flow_smoothness: float
    n_score_steps: int
    persona: str

    @property
    def tokens_per_step(self) -> float:
        return self.total_tokens / self.step_count if self.step_count else 0.0

    @property
    def composite_score(self) -> float:
        return (self.cta_clarity + self.copy_quality + self.flow_smoothness) / 3.0


def _parse_suite_dt(suite_id: str) -> datetime:
    return datetime.strptime(suite_id, "suite_%Y%m%d_%H%M%S")


def _parse_page_dt(folder_name: str) -> datetime | None:
    stem = folder_name.replace("_single_page", "")
    try:
        return datetime.strptime(stem, "%Y-%m-%d_%H%M%S")
    except ValueError:
        return None


def find_page_dirs(suite_id: str, site: str, runs_dir: Path = RUNS_DIR) -> list[Path]:
    """Return per-page run dirs whose timestamps fall in the suite's window."""
    domain_dir = runs_dir / SITE_DOMAINS[site]
    if not domain_dir.exists():
        return []
    suite_dt = _parse_suite_dt(suite_id)
    window_end = suite_dt + SUITE_PAGE_WINDOW
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


def aggregate_scores(page_dirs: Iterable[Path]) -> dict:
    """Average per-step axis scores across all report.json files. Step-1 persona
    of the first page is taken as the suite-level inferred persona."""
    cta, copy, flow = [], [], []
    persona = ""
    for i, page_dir in enumerate(page_dirs):
        report_path = page_dir / "report.json"
        if not report_path.exists():
            continue
        with report_path.open() as fh:
            steps = json.load(fh)
        if not isinstance(steps, list):
            continue
        if i == 0 and steps and not persona:
            persona = (steps[0].get("persona") or "").strip()
        for step in steps:
            for axis_list, key in (
                (cta, "cta_clarity"),
                (copy, "copy_quality"),
                (flow, "flow_smoothness"),
            ):
                axis = step.get(key)
                if isinstance(axis, dict) and isinstance(axis.get("score"), (int, float)):
                    axis_list.append(float(axis["score"]))
    n = min(len(cta), len(copy), len(flow))

    def _avg(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "cta_clarity": _avg(cta),
        "copy_quality": _avg(copy),
        "flow_smoothness": _avg(flow),
        "n_score_steps": n,
        "persona": persona,
    }


def load_cost_log(path: Path = COST_LOG) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as fh:
        return list(csv.DictReader(fh))


def build_rows(cost_rows: list[dict] | None = None, runs_dir: Path = RUNS_DIR) -> list[VariantRow]:
    if cost_rows is None:
        cost_rows = load_cost_log(runs_dir / "cost_log.csv")
    by_session = {row["langfuse_session_id"]: row for row in cost_rows if row.get("langfuse_session_id")}
    out: list[VariantRow] = []
    for suite_id, (variant, site) in SUITE_VARIANTS.items():
        cost_row = by_session.get(suite_id)
        if not cost_row:
            continue
        page_dirs = find_page_dirs(suite_id, site, runs_dir=runs_dir)
        scores = aggregate_scores(page_dirs)
        out.append(VariantRow(
            variant=variant,
            site=site,
            suite_id=suite_id,
            total_tokens=int(cost_row.get("total_tokens") or 0),
            cost_usd=float(cost_row.get("langfuse_cost_usd") or 0.0),
            step_count=int(cost_row.get("step_count") or 0),
            cta_clarity=scores["cta_clarity"],
            copy_quality=scores["copy_quality"],
            flow_smoothness=scores["flow_smoothness"],
            n_score_steps=scores["n_score_steps"],
            persona=scores["persona"],
        ))
    return out


def build_markdown(rows: list[VariantRow]) -> str:
    lines = [
        "# Variant comparison",
        "",
        "Four configurations of the multi-page suite, run across stripe / linear / glossier on 2026-04-28 → 2026-04-29.",
        "",
        "| Variant | Site | Steps | Tokens | $ Cost | tok/step | CTA | Copy | Flow | Composite |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row.variant} | {row.site} | {row.step_count} | {row.total_tokens:,} | "
            f"${row.cost_usd:.2f} | {row.tokens_per_step:,.0f} | "
            f"{row.cta_clarity:.2f} | {row.copy_quality:.2f} | {row.flow_smoothness:.2f} | "
            f"{row.composite_score:.2f} |"
        )

    lines.extend(["", "## Per-variant means (averaged across 3 sites)", ""])
    lines.append("| Variant | $ Cost | tok/step | Composite score |")
    lines.append("|---|---:|---:|---:|")
    for variant in VARIANT_ORDER:
        members = [r for r in rows if r.variant == variant]
        if not members:
            continue
        cost = sum(r.cost_usd for r in members) / len(members)
        tps = sum(r.tokens_per_step for r in members) / len(members)
        comp = sum(r.composite_score for r in members) / len(members)
        lines.append(f"| {variant} | ${cost:.2f} | {tps:,.0f} | {comp:.2f} |")
    lines.append("")
    return "\n".join(lines)


def build_chart(rows: list[VariantRow], out_path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_key = {(r.variant, r.site): r for r in rows}
    n_variants = len(VARIANT_ORDER)
    bar_width = 0.25
    x = list(range(n_variants))

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    panels = [
        ("Cost (USD)", lambda r: r.cost_usd),
        ("Tokens / step", lambda r: r.tokens_per_step),
        ("Composite score (1-5)", lambda r: r.composite_score),
    ]

    for ax, (title, getter) in zip(axes, panels, strict=True):
        for i, site in enumerate(SITE_ORDER):
            values = [getter(by_key[(v, site)]) if (v, site) in by_key else 0 for v in VARIANT_ORDER]
            offsets = [xv + (i - 1) * bar_width for xv in x]
            ax.bar(offsets, values, bar_width, label=site)
        ax.set_xticks(x)
        ax.set_xticklabels([v.replace("_", "\n", 1) for v in VARIANT_ORDER], fontsize=9)
        ax.set_title(title)
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        if title.startswith("Composite"):
            ax.set_ylim(0, 5)

    axes[0].legend(loc="upper left", fontsize=9)
    fig.suptitle("reasonable-ux variant comparison — 4 configs × 3 sites", fontsize=13)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(ARTIFACTS_DIR))
    parser.add_argument("--runs-dir", default=str(RUNS_DIR))
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = build_rows(runs_dir=Path(args.runs_dir))
    if not rows:
        print("No variant rows found in cost_log.csv — nothing to compare.")
        return 1

    md_path = out_dir / "variant_comparison.md"
    png_path = out_dir / "variant_comparison.png"
    md_path.write_text(build_markdown(rows))
    build_chart(rows, png_path)

    print(f"Wrote {md_path} ({len(rows)} rows)")
    print(f"Wrote {png_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
