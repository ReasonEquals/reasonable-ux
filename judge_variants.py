"""LLM-judge pairwise comparison of reasonable-ux variant runs.

Reads the frozen evaluation corpus at evals/variant_corpus/{variant}/{site}.json
and asks claude-opus-4-7 to judge which of two variant reports did a better job
of UX evaluation, against the pre-registered rubric at
artifacts/variant_judge_rubric.md.

Champion: v1_baseline. Challengers: v2_advisor, v3_8step, v4_8step_advisor.
9 pairs (3 sites × 3 challengers). N=1 default with adaptive N=3 on close-call
pairs within the remaining cost budget. A/B labels randomized per call.

Outputs:
  artifacts/variant_judge.json   — raw verdicts (one entry per call)
  artifacts/variant_judge.md     — human-readable table + narrative

Usage:
  python judge_variants.py                                # full 9-pair run
  python judge_variants.py --site stripe --challenger v2_advisor --max-cost 1.00
  python judge_variants.py --max-cost 5.00 --no-adaptive  # skip N=3 sweep
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import litellm
from dotenv import load_dotenv

load_dotenv(override=True)

REPO_ROOT = Path(__file__).resolve().parent
CORPUS_DIR = REPO_ROOT / "evals" / "variant_corpus"
RUBRIC_PATH = REPO_ROOT / "artifacts" / "variant_judge_rubric.md"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"

CHAMPION = "v1_baseline"
CHALLENGERS = ["v2_advisor", "v3_8step", "v4_8step_advisor"]
SITES = ["stripe", "linear", "glossier"]

DIMENSIONS = ["specificity", "actionability", "coverage", "persona_fidelity"]
DIMENSION_LABELS = {
    "specificity": "Specificity",
    "actionability": "Actionability",
    "coverage": "Coverage",
    "persona_fidelity": "Persona fidelity",
}

INPUT_CHAR_CAP = 5000  # per side, truncated at step boundary


@dataclass
class JudgeRecord:
    site: str
    champion: str
    challenger: str
    pass_idx: int  # 0 for primary, 1-2 for adaptive N=3 follow-ups
    a_variant: str  # which variant got label A this call
    b_variant: str
    dimensions: dict
    overall: dict
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class PairAggregate:
    site: str
    champion: str
    challenger: str
    n: int
    dimensions: dict[str, dict]  # winner-by-vote + reasons list
    overall: dict
    total_cost_usd: float
    records: list[JudgeRecord] = field(default_factory=list)


def _read_rubric() -> str:
    return RUBRIC_PATH.read_text()


def _extract_report_text(corpus_path: Path) -> str:
    """Concatenate text fields from the frozen corpus into a budget-capped string.

    Per-step we keep verdict, friction_points, recommendations, scoring notes,
    and persona (step 1 only — same string repeats otherwise). Truncate at step
    boundary once char budget is reached so the judge never sees half a step.
    """
    if not corpus_path.exists():
        raise FileNotFoundError(f"corpus missing: {corpus_path}")
    steps = json.loads(corpus_path.read_text())
    parts: list[str] = []
    char_count = 0
    for step in steps:
        chunks = []
        if step.get("step") == 1 and step.get("persona"):
            chunks.append(f"PERSONA: {step['persona']}")
        if step.get("verdict"):
            chunks.append(f"VERDICT: {step['verdict']}")
        if step.get("friction_points"):
            chunks.append("FRICTION:\n- " + "\n- ".join(step["friction_points"]))
        if step.get("recommendations"):
            chunks.append("RECOMMENDATIONS:\n- " + "\n- ".join(step["recommendations"]))
        for axis in ("cta_clarity", "copy_quality", "flow_smoothness"):
            obj = step.get(axis)
            if isinstance(obj, dict) and obj.get("note"):
                chunks.append(f"{axis.upper()}: {obj['note']}")
        block = f"--- step {step.get('step', '?')} ---\n" + "\n".join(chunks)
        if char_count + len(block) > INPUT_CHAR_CAP and parts:
            break
        parts.append(block)
        char_count += len(block) + 1
    return "\n\n".join(parts)


def _build_prompt(rubric: str, text_a: str, text_b: str) -> str:
    return f"""You are an expert UX evaluator. Two reports from a UX-evaluation agent are below. Judge which report did a better job of evaluating UX, against the rubric.

RUBRIC:
{rubric}

REPORT A:
{text_a}

REPORT B:
{text_b}

Respond with JSON only — no preamble, no markdown fence:
{{
  "dimensions": {{
    "specificity":      {{"winner": "A|B|tie", "reason": "..."}},
    "actionability":    {{"winner": "A|B|tie", "reason": "..."}},
    "coverage":         {{"winner": "A|B|tie", "reason": "..."}},
    "persona_fidelity": {{"winner": "A|B|tie", "reason": "..."}}
  }},
  "overall": {{"winner": "A|B|tie", "reason": "..."}}
}}

Use "tie" only if the reason explicitly names the gap as "negligible" or "marginal" — a forced tie when you cannot articulate the gap is not allowed."""


def _parse_json_safe(text: str) -> dict:
    if not text:
        return {}
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return {}
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}


def _calc_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    try:
        inp, out = litellm.cost_per_token(
            model=model, prompt_tokens=input_tokens, completion_tokens=output_tokens
        )
        return inp + out
    except Exception:  # noqa: BLE001
        return 0.0


def _remap_ab_to_variants(verdicts: dict, a_variant: str, b_variant: str) -> dict:
    """Replace A/B with the actual variant names so downstream code never has to
    care which side got which label this call."""
    mapping = {"A": a_variant, "B": b_variant, "tie": "tie"}
    out = {"dimensions": {}, "overall": {}}
    for dim, v in (verdicts.get("dimensions") or {}).items():
        if not isinstance(v, dict):
            continue
        out["dimensions"][dim] = {
            "winner": mapping.get(v.get("winner", "tie"), "tie"),
            "reason": v.get("reason", ""),
        }
    overall = verdicts.get("overall", {}) or {}
    out["overall"] = {
        "winner": mapping.get(overall.get("winner", "tie"), "tie"),
        "reason": overall.get("reason", ""),
    }
    return out


async def _judge_pair(
    rubric: str,
    text_a: str,
    text_b: str,
    a_variant: str,
    b_variant: str,
    model: str,
) -> tuple[dict, int, int, float]:
    prompt = _build_prompt(rubric, text_a, text_b)
    response = await litellm.acompletion(
        model=f"anthropic/{model}",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1024,
    )
    raw = response.choices[0].message.content or ""
    in_tok = response.usage.prompt_tokens
    out_tok = response.usage.completion_tokens
    cost = _calc_cost_usd(model, in_tok, out_tok)
    parsed = _parse_json_safe(raw)
    remapped = _remap_ab_to_variants(parsed, a_variant, b_variant)
    return remapped, in_tok, out_tok, cost


def _is_close_call(record: JudgeRecord) -> bool:
    """A pair is a close call if any dimension is tie OR the overall winner
    contradicts the majority of dimension winners."""
    dim_winners = [v.get("winner") for v in record.dimensions.values()]
    if "tie" in dim_winners:
        return True
    if record.overall.get("winner") == "tie":
        return True
    counts = Counter(dim_winners)
    if not counts:
        return False
    majority = counts.most_common(1)[0][0]
    return record.overall.get("winner") != majority


def _aggregate(records: list[JudgeRecord]) -> PairAggregate:
    """Majority vote across records for the same (site, challenger) pair."""
    first = records[0]
    dim_votes: dict[str, list[str]] = {dim: [] for dim in DIMENSIONS}
    dim_reasons: dict[str, list[str]] = {dim: [] for dim in DIMENSIONS}
    overall_votes: list[str] = []
    overall_reasons: list[str] = []
    cost = 0.0
    for r in records:
        for dim in DIMENSIONS:
            v = r.dimensions.get(dim, {})
            if v.get("winner"):
                dim_votes[dim].append(v["winner"])
                dim_reasons[dim].append(v.get("reason", ""))
        if r.overall.get("winner"):
            overall_votes.append(r.overall["winner"])
            overall_reasons.append(r.overall.get("reason", ""))
        cost += r.cost_usd
    dims = {
        dim: {
            "winner": Counter(dim_votes[dim]).most_common(1)[0][0] if dim_votes[dim] else "tie",
            "votes": dim_votes[dim],
            "reasons": dim_reasons[dim],
        }
        for dim in DIMENSIONS
    }
    overall = {
        "winner": Counter(overall_votes).most_common(1)[0][0] if overall_votes else "tie",
        "votes": overall_votes,
        "reasons": overall_reasons,
    }
    return PairAggregate(
        site=first.site,
        champion=first.champion,
        challenger=first.challenger,
        n=len(records),
        dimensions=dims,
        overall=overall,
        total_cost_usd=cost,
        records=records,
    )


def _record_to_dict(r: JudgeRecord) -> dict:
    return asdict(r)


def _aggregate_to_dict(p: PairAggregate) -> dict:
    return {
        "site": p.site,
        "champion": p.champion,
        "challenger": p.challenger,
        "n": p.n,
        "dimensions": p.dimensions,
        "overall": p.overall,
        "total_cost_usd": p.total_cost_usd,
        "records": [_record_to_dict(r) for r in p.records],
    }


def build_judge_md(aggregates: list[PairAggregate], total_cost: float) -> str:
    """Markdown table + narrative win-counts vs the pre-registered hypothesis."""
    lines = [
        "# Variant judge — LLM-as-judge pairwise comparison",
        "",
        f"Champion: `{CHAMPION}`. Challengers: `v2_advisor`, `v3_8step`, `v4_8step_advisor`. "
        "Judge: `claude-opus-4-7`. Rubric pre-registered at "
        "[variant_judge_rubric.md](variant_judge_rubric.md).",
        "",
        f"Total cost: ${total_cost:.2f} across {sum(p.n for p in aggregates)} judge calls "
        f"({len(aggregates)} pairs, N=1 baseline + adaptive N=3 on close-call pairs).",
        "",
        "## Verdicts",
        "",
        "| Site | Challenger | N | Specificity | Actionability | Coverage | Persona fidelity | Overall |",
        "|---|---|---:|---|---|---|---|---|",
    ]
    for p in aggregates:
        cells = [p.site, p.challenger, str(p.n)]
        for dim in DIMENSIONS:
            w = p.dimensions[dim]["winner"]
            cells.append(_fmt_winner(w, p.champion, p.challenger))
        cells.append(_fmt_winner(p.overall["winner"], p.champion, p.challenger))
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    # Narrative: per-challenger dimension win counts vs champion
    lines.extend(["## Narrative", "", "Per-challenger dimension win counts vs `v1_baseline` (3 sites each):", ""])
    for challenger in CHALLENGERS:
        rows = [p for p in aggregates if p.challenger == challenger]
        if not rows:
            continue
        bullets = [f"- **{challenger}** ({len(rows)} sites):"]
        for dim in DIMENSIONS:
            wins = sum(1 for p in rows if p.dimensions[dim]["winner"] == challenger)
            losses = sum(1 for p in rows if p.dimensions[dim]["winner"] == p.champion)
            ties = sum(1 for p in rows if p.dimensions[dim]["winner"] == "tie")
            bullets.append(f"  - {DIMENSION_LABELS[dim]}: {wins}W / {losses}L / {ties}T")
        overall_w = sum(1 for p in rows if p.overall["winner"] == challenger)
        overall_l = sum(1 for p in rows if p.overall["winner"] == p.champion)
        overall_t = sum(1 for p in rows if p.overall["winner"] == "tie")
        bullets.append(f"  - **Overall: {overall_w}W / {overall_l}L / {overall_t}T**")
        lines.extend(bullets)
        lines.append("")

    lines.extend([
        "## Pre-registered hypothesis",
        "",
        "> Advisor-on variants (v2, v4) produce more specific friction points and more "
        "actionable recommendations than baselines (v1, v3), but the cost premium "
        "overprices the gap relative to v1_baseline.",
        "",
        "See `variant_judge.json` for raw per-call records (verdicts, reasons, token counts, costs).",
        "",
    ])
    return "\n".join(lines)


def _fmt_winner(winner: str, champion: str, challenger: str) -> str:
    if winner == champion:
        return f"**{champion}**"
    if winner == challenger:
        return f"**{challenger}**"
    return "tie"


async def _run_pair(
    rubric: str,
    site: str,
    challenger: str,
    pass_idx: int,
    model: str,
    rng: random.Random,
) -> JudgeRecord:
    text_champion = _extract_report_text(CORPUS_DIR / CHAMPION / f"{site}.json")
    text_challenger = _extract_report_text(CORPUS_DIR / challenger / f"{site}.json")
    # Randomize A/B labels
    if rng.random() < 0.5:
        a_variant, b_variant = CHAMPION, challenger
        text_a, text_b = text_champion, text_challenger
    else:
        a_variant, b_variant = challenger, CHAMPION
        text_a, text_b = text_challenger, text_champion
    verdict, in_tok, out_tok, cost = await _judge_pair(
        rubric, text_a, text_b, a_variant, b_variant, model
    )
    return JudgeRecord(
        site=site,
        champion=CHAMPION,
        challenger=challenger,
        pass_idx=pass_idx,
        a_variant=a_variant,
        b_variant=b_variant,
        dimensions=verdict.get("dimensions", {}),
        overall=verdict.get("overall", {}),
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
    )


async def run_judge(
    sites: list[str],
    challengers: list[str],
    max_cost: float,
    model: str,
    seed: int,
    adaptive: bool,
) -> tuple[list[PairAggregate], float, list[str]]:
    rubric = _read_rubric()
    rng = random.Random(seed)  # noqa: S311 — A/B label shuffle, not cryptographic
    pairs = [(s, c) for s in sites for c in challengers]
    skipped: list[str] = []

    # ---- N=1 primary pass ----
    primary_records: dict[tuple[str, str], list[JudgeRecord]] = {}
    total_cost = 0.0
    for site, challenger in pairs:
        # Pre-call estimate uses the previous call's cost as a rough budget probe;
        # for the first call, allow it through.
        rec = await _run_pair(rubric, site, challenger, 0, model, rng)
        if total_cost + rec.cost_usd > max_cost:
            skipped.append(f"{site}/{challenger} (N=1 primary, would have exceeded cap)")
            print(
                f"  ! cost cap reached before {site}/{challenger}: "
                f"${total_cost:.4f} + ${rec.cost_usd:.4f} > ${max_cost:.2f}"
            )
            break
        total_cost += rec.cost_usd
        primary_records.setdefault((site, challenger), []).append(rec)
        print(
            f"  N=1 {site}/{challenger}: overall={rec.overall.get('winner', '?')} "
            f"cost=${rec.cost_usd:.4f} running=${total_cost:.4f}"
        )

    # ---- Adaptive N=3 follow-up on close calls ----
    if adaptive:
        close_pairs = [
            (site, challenger)
            for (site, challenger), recs in primary_records.items()
            if recs and _is_close_call(recs[0])
        ]
        for site, challenger in close_pairs:
            for pass_idx in (1, 2):
                # Estimate next call cost as ~1.2× the primary call (output may vary)
                est = primary_records[(site, challenger)][0].cost_usd * 1.2
                if total_cost + est > max_cost:
                    skipped.append(
                        f"{site}/{challenger} (N=3 pass {pass_idx + 1}, "
                        f"close call, no remaining budget)"
                    )
                    print(
                        f"  ! skipping {site}/{challenger} N=3 pass {pass_idx + 1}: "
                        f"${total_cost:.4f} + ~${est:.4f} > ${max_cost:.2f}"
                    )
                    break
                rec = await _run_pair(rubric, site, challenger, pass_idx, model, rng)
                total_cost += rec.cost_usd
                primary_records[(site, challenger)].append(rec)
                print(
                    f"  N=3 {site}/{challenger} pass {pass_idx + 1}: "
                    f"overall={rec.overall.get('winner', '?')} "
                    f"cost=${rec.cost_usd:.4f} running=${total_cost:.4f}"
                )

    aggregates = [_aggregate(recs) for recs in primary_records.values() if recs]
    return aggregates, total_cost, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", choices=SITES, help="Filter to one site")
    parser.add_argument("--challenger", choices=CHALLENGERS, help="Filter to one challenger")
    parser.add_argument("--max-cost", type=float, default=5.00, help="Hard cost cap in USD")
    parser.add_argument("--model", default="claude-opus-4-7")
    parser.add_argument("--seed", type=int, default=20260429)
    parser.add_argument("--no-adaptive", action="store_true", help="Skip the N=3 close-call sweep")
    parser.add_argument("--out-dir", default=str(ARTIFACTS_DIR))
    args = parser.parse_args()

    sites = [args.site] if args.site else SITES
    challengers = [args.challenger] if args.challenger else CHALLENGERS

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    aggregates, total_cost, skipped = asyncio.run(
        run_judge(
            sites=sites,
            challengers=challengers,
            max_cost=args.max_cost,
            model=args.model,
            seed=args.seed,
            adaptive=not args.no_adaptive,
        )
    )

    json_path = out_dir / "variant_judge.json"
    md_path = out_dir / "variant_judge.md"
    json_path.write_text(json.dumps(
        {
            "model": args.model,
            "seed": args.seed,
            "max_cost_usd": args.max_cost,
            "total_cost_usd": total_cost,
            "skipped": skipped,
            "aggregates": [_aggregate_to_dict(p) for p in aggregates],
        },
        indent=2,
    ))
    md_path.write_text(build_judge_md(aggregates, total_cost))

    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"Total cost: ${total_cost:.4f} / ${args.max_cost:.2f}")
    if skipped:
        print(f"Skipped: {len(skipped)} pairs/passes due to cost cap")
        for s in skipped:
            print(f"  - {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
