"""Phase 1 eval harness for reasonable-ux.

Loops evals/labels.jsonl, runs the existing single-page agent loop at 4 steps
per URL, and asserts persona / score-band / friction-keyword regressions.

Usage:
    python evals/run_evals.py [--limit N] [--category saas_landing]
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from agent_test import run as agent_run  # noqa: E402

LABELS_PATH = REPO_ROOT / "evals" / "labels.jsonl"
RUNS_DIR = REPO_ROOT / "runs"
WALL_CLOCK_WARN_SECONDS = 90.0
SUBSCORE_FIELDS = ("cta_clarity", "copy_quality", "flow_smoothness")


def _domain_slug(url: str) -> str:
    host = urlparse(url).hostname or url
    if host.startswith("www."):
        host = host[4:]
    return host.replace(".", "_").replace("-", "_")


def _find_latest_single_page_run(url: str, since_ts: float) -> Path | None:
    """Return the newest runs/{domain}/{ts}_single_page/ created after since_ts."""
    domain_dir = RUNS_DIR / _domain_slug(url)
    if not domain_dir.is_dir():
        return None
    candidates = [
        p for p in domain_dir.iterdir()
        if p.is_dir() and p.name.endswith("_single_page") and p.stat().st_mtime >= since_ts - 1
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _aggregate_score(report: list[dict]) -> float | None:
    """Mean of all per-step subscores across {cta_clarity, copy_quality, flow_smoothness}, times 20.

    Returns None if no numeric subscores exist.
    """
    values: list[float] = []
    for step in report:
        for field in SUBSCORE_FIELDS:
            block = step.get(field)
            if isinstance(block, dict) and isinstance(block.get("score"), (int, float)):
                values.append(float(block["score"]))
    if not values:
        return None
    return (sum(values) / len(values)) * 20.0


def _persona_string(report: list[dict]) -> str | None:
    if not report:
        return None
    persona = report[0].get("persona")
    return persona if isinstance(persona, str) else None


def _concatenated_friction(report: list[dict]) -> str:
    chunks: list[str] = []
    for step in report:
        for fp in step.get("friction_points") or []:
            if isinstance(fp, str):
                chunks.append(fp)
    return " ".join(chunks).lower()


def _assert_label(label: dict, report_path: Path, wall_clock_s: float) -> tuple[bool, list[str], list[str]]:
    """Run assertions against a report. Returns (passed, failures, warnings)."""
    failures: list[str] = []
    warnings: list[str] = []

    try:
        with open(report_path) as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"report.json did not parse: {exc}"], warnings

    if not isinstance(report, list) or not report:
        return False, ["report.json is not a non-empty list"], warnings

    persona = _persona_string(report)
    if not persona:
        failures.append("missing top-level persona on step 1")
    else:
        keywords = [k.lower() for k in label.get("expected_persona_keywords") or []]
        persona_lc = persona.lower()
        if keywords and not any(k in persona_lc for k in keywords):
            failures.append(
                f"persona keyword miss — expected any of {keywords}, got persona={persona!r}"
            )

    score = _aggregate_score(report)
    band = label.get("expected_score_band")
    if score is None:
        failures.append("no numeric subscores found; cannot compute aggregate")
    elif band and len(band) == 2:
        low, high = band
        if not (low <= score <= high):
            failures.append(f"score {score:.1f} outside band [{low}, {high}]")

    friction_keywords = [k.lower() for k in label.get("expected_friction_keywords") or []]
    friction_text = _concatenated_friction(report)
    if friction_keywords and not any(k in friction_text for k in friction_keywords):
        failures.append(
            f"no friction keyword matched — expected any of {friction_keywords}"
        )

    if wall_clock_s > WALL_CLOCK_WARN_SECONDS:
        warnings.append(f"wall clock {wall_clock_s:.1f}s > {WALL_CLOCK_WARN_SECONDS:.0f}s")

    return len(failures) == 0, failures, warnings


def _load_labels(path: Path) -> list[dict]:
    labels = []
    with open(path) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                labels.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"labels.jsonl line {i} invalid JSON: {exc}") from exc
    return labels


async def _evaluate_one(label: dict) -> dict:
    url = label["url"]
    since = time.time()
    t0 = time.monotonic()
    error: str | None = None
    try:
        await agent_run(url=url, max_steps=4)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    wall_clock = time.monotonic() - t0

    run_dir = _find_latest_single_page_run(url, since_ts=since)
    report_path = run_dir / "report.json" if run_dir else None

    if error and report_path is None:
        return {
            "label": label,
            "passed": False,
            "failures": [f"agent_run raised and produced no report: {error}"],
            "warnings": [],
            "wall_clock_s": wall_clock,
            "report_path": None,
        }

    if report_path is None or not report_path.is_file():
        return {
            "label": label,
            "passed": False,
            "failures": ["no single_page run dir produced after agent_run"],
            "warnings": [],
            "wall_clock_s": wall_clock,
            "report_path": None,
        }

    passed, failures, warnings = _assert_label(label, report_path, wall_clock)
    if error:
        failures.insert(0, f"agent_run raised (report produced anyway): {error}")
        passed = False
    return {
        "label": label,
        "passed": passed,
        "failures": failures,
        "warnings": warnings,
        "wall_clock_s": wall_clock,
        "report_path": str(report_path),
    }


def _print_summary(results: list[dict]) -> int:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    print()
    print("=" * 60)
    print(f"RESULT: {passed}/{total} passed")
    print("=" * 60)

    by_cat: dict[str, list[dict]] = {}
    for r in results:
        cat = r["label"].get("category", "uncategorized")
        by_cat.setdefault(cat, []).append(r)
    print("\nPer-category:")
    for cat, items in sorted(by_cat.items()):
        cat_passed = sum(1 for r in items if r["passed"])
        print(f"  {cat}: {cat_passed}/{len(items)}")

    failing = [r for r in results if not r["passed"]]
    if failing:
        print("\nFailures:")
        for r in failing:
            print(f"  {r['label']['url']} ({r['label'].get('category', '—')})")
            for msg in r["failures"]:
                print(f"    - {msg}")
    warnings = [(r, w) for r in results for w in r["warnings"]]
    if warnings:
        print("\nWarnings:")
        for r, w in warnings:
            print(f"  {r['label']['url']}: {w}")

    return 0 if passed == total else 1


async def _main_async(args) -> int:
    if not LABELS_PATH.is_file():
        raise SystemExit(f"labels file not found: {LABELS_PATH}")
    labels = _load_labels(LABELS_PATH)
    if args.category:
        labels = [lbl for lbl in labels if lbl.get("category") == args.category]
    if args.limit is not None:
        labels = labels[: args.limit]
    if not labels:
        raise SystemExit("no labels selected")

    print(f"Running {len(labels)} eval(s)…")
    results = []
    for i, label in enumerate(labels, 1):
        print(f"\n[{i}/{len(labels)}] {label['url']} ({label.get('category', '—')})")
        result = await _evaluate_one(label)
        status = "PASS" if result["passed"] else "FAIL"
        print(f"  → {status} ({result['wall_clock_s']:.1f}s)")
        for msg in result["failures"]:
            print(f"    fail: {msg}")
        for msg in result["warnings"]:
            print(f"    warn: {msg}")
        results.append(result)

    return _print_summary(results)


def main() -> int:
    parser = argparse.ArgumentParser(description="reasonable-ux Phase 1 eval harness")
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N labels")
    parser.add_argument("--category", type=str, default=None, help="Filter by category")
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
