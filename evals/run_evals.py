"""Phase 1 eval harness for reasonable-ux.

Loops evals/labels.jsonl, runs the existing single-page agent loop at 4 steps
per URL, and asserts persona / score-band / friction-keyword regressions.

Each invocation creates a fresh dir under `eval_runs/<timestamp>[_<label>]/`.
Per-URL agent artifacts (report.json, screenshots/, etc.) are moved out of
`runs/{domain}/` into `eval_runs/<ts>/<domain>/` so audit runs stay pristine.
A `manifest.json` at the root captures pass-rate, per-URL results, labels
file SHA, and settings — diffable across historical eval runs.

Usage:
    python evals/run_evals.py [--limit N] [--category saas_landing] [--label baseline]
"""
import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from agent_test import USER_AGENT  # noqa: E402
from agent_test import run as agent_run  # noqa: E402

LABELS_PATH = REPO_ROOT / "evals" / "labels.jsonl"
RUNS_DIR = REPO_ROOT / "runs"
EVAL_RUNS_DIR = REPO_ROOT / "eval_runs"
WALL_CLOCK_WARN_SECONDS = 90.0
PREFLIGHT_TIMEOUT_S = 10.0
SUBSCORE_FIELDS = ("cta_clarity", "copy_quality", "flow_smoothness")
BOT_BLOCK_SIGNALS = (
    "just a moment",
    "cf-browser-verification",
    "cf-challenge",
    "attention required | cloudflare",
    "verify you are human",
    "access denied",
    "captcha",
    "ddos protection by cloudflare",
    "enable javascript and cookies to continue",
)


def _domain_slug(url: str) -> str:
    host = urlparse(url).hostname or url
    if host.startswith("www."):
        host = host[4:]
    return host.replace(".", "_").replace("-", "_")


def _preflight(url: str) -> tuple[bool, str]:
    """Cheap HTTP check before spending agent tokens. Returns (ok, reason).

    Catches: 4xx/5xx, non-HTML responses, network errors, and Cloudflare/captcha
    challenge pages served at the edge (body-text signatures). Does NOT catch
    JS-rendered challenges that only fire against Playwright — those surface
    during the agent run itself.
    """
    try:
        r = requests.get(
            url,
            timeout=PREFLIGHT_TIMEOUT_S,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        return False, f"network: {type(exc).__name__}: {exc}"
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code}"
    ctype = r.headers.get("content-type", "").lower()
    if "html" not in ctype:
        return False, f"non-HTML content-type: {ctype}"
    body_lc = r.text[:8000].lower()
    for sig in BOT_BLOCK_SIGNALS:
        if sig in body_lc:
            return False, f"bot-block signature: {sig!r}"
    return True, "ok"


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


def _assert_label(label: dict, report: list[dict], wall_clock_s: float) -> tuple[bool, list[str], list[str]]:
    """Run assertions against a parsed report. Returns (passed, failures, warnings)."""
    failures: list[str] = []
    warnings: list[str] = []

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


def _labels_file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_eval_run_dir(label_suffix: str | None) -> Path:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    name = f"{ts}_{label_suffix}" if label_suffix else ts
    eval_dir = EVAL_RUNS_DIR / name
    eval_dir.mkdir(parents=True, exist_ok=False)
    return eval_dir


def _move_run_into_eval_dir(produced: Path, eval_run_dir: Path, domain: str) -> Path:
    """Move runs/{domain}/{ts}_single_page/ → eval_runs/{eval_ts}/{domain}/. Returns new path."""
    dest = eval_run_dir / domain
    if dest.exists():
        # Collision — rare (same domain twice in one eval), append a suffix
        dest = eval_run_dir / f"{domain}_{int(time.time())}"
    shutil.move(str(produced), str(dest))
    return dest


async def _evaluate_one(label: dict, eval_run_dir: Path) -> dict:
    url = label["url"]
    domain = _domain_slug(url)

    preflight_ok, preflight_reason = _preflight(url)
    if not preflight_ok:
        return {
            "url": url,
            "category": label.get("category"),
            "domain": domain,
            "status": "skipped",
            "passed": False,
            "failures": [],
            "warnings": [],
            "wall_clock_s": 0.0,
            "run_dir": None,
            "score": None,
            "persona": None,
            "skip_reason": preflight_reason,
        }

    since = time.time()
    t0 = time.monotonic()
    error: str | None = None
    try:
        await agent_run(url=url, max_steps=4)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    wall_clock = time.monotonic() - t0

    produced = _find_latest_single_page_run(url, since_ts=since)
    if produced is None:
        return {
            "url": url,
            "category": label.get("category"),
            "domain": domain,
            "status": "failed",
            "passed": False,
            "failures": [f"agent_run raised and produced no report: {error}"] if error else ["no single_page run dir produced after agent_run"],
            "warnings": [],
            "wall_clock_s": wall_clock,
            "run_dir": None,
            "score": None,
            "persona": None,
        }

    moved = _move_run_into_eval_dir(produced, eval_run_dir, domain)
    report_path = moved / "report.json"

    try:
        with open(report_path) as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "url": url,
            "category": label.get("category"),
            "domain": domain,
            "status": "failed",
            "passed": False,
            "failures": [f"report.json did not parse: {exc}"],
            "warnings": [],
            "wall_clock_s": wall_clock,
            "run_dir": moved.name,
            "score": None,
            "persona": None,
        }

    passed, failures, warnings = _assert_label(label, report, wall_clock)
    if error:
        failures.insert(0, f"agent_run raised (report produced anyway): {error}")
        passed = False

    return {
        "url": url,
        "category": label.get("category"),
        "domain": domain,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "failures": failures,
        "warnings": warnings,
        "wall_clock_s": wall_clock,
        "run_dir": moved.name,
        "score": _aggregate_score(report),
        "persona": _persona_string(report),
    }


def _build_manifest(
    *,
    eval_run_dir: Path,
    started_at: datetime,
    finished_at: datetime,
    labels_subset: list[dict],
    results: list[dict],
    settings: dict,
) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] == "failed")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    scored = passed + failed
    by_cat: dict[str, dict[str, int]] = {}
    for r in results:
        cat = r.get("category") or "uncategorized"
        bucket = by_cat.setdefault(cat, {"passed": 0, "failed": 0, "skipped": 0})
        bucket[r["status"]] += 1
    total_wall_clock = sum(r["wall_clock_s"] for r in results)
    return {
        "eval_run_id": eval_run_dir.name,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "total_wall_clock_s": round(total_wall_clock, 1),
        "labels_file": str(LABELS_PATH.relative_to(REPO_ROOT)),
        "labels_file_sha256": _labels_file_sha(LABELS_PATH),
        "labels_count_total": len(labels_subset),
        "settings": settings,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": total,
        "pass_rate": round(passed / scored, 3) if scored else 0.0,
        "per_category": by_cat,
        "results": results,
    }


def _print_summary(manifest: dict, eval_run_dir: Path) -> int:
    passed = manifest["passed"]
    failed = manifest["failed"]
    skipped = manifest["skipped"]
    scored = passed + failed
    print()
    print("=" * 60)
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"RESULT: {passed}/{scored} passed{tail}")
    print(f"Eval run dir: {eval_run_dir.relative_to(REPO_ROOT)}")
    print("=" * 60)

    print("\nPer-category:")
    for cat, bucket in sorted(manifest["per_category"].items()):
        cat_scored = bucket["passed"] + bucket["failed"]
        cat_tail = f" ({bucket['skipped']} skipped)" if bucket["skipped"] else ""
        print(f"  {cat}: {bucket['passed']}/{cat_scored}{cat_tail}")

    failing = [r for r in manifest["results"] if r["status"] == "failed"]
    if failing:
        print("\nFailures:")
        for r in failing:
            print(f"  {r['url']} ({r.get('category', '—')})")
            for msg in r["failures"]:
                print(f"    - {msg}")

    skipped_results = [r for r in manifest["results"] if r["status"] == "skipped"]
    if skipped_results:
        print("\nSkipped (pre-flight):")
        for r in skipped_results:
            print(f"  {r['url']} ({r.get('category', '—')}) — {r['skip_reason']}")

    warnings = [(r, w) for r in manifest["results"] for w in r["warnings"]]
    if warnings:
        print("\nWarnings:")
        for r, w in warnings:
            print(f"  {r['url']}: {w}")

    return 0 if failed == 0 else 1


async def _main_async(args) -> int:
    if not LABELS_PATH.is_file():
        raise SystemExit(f"labels file not found: {LABELS_PATH}")
    labels = _load_labels(LABELS_PATH)
    subset = labels
    if args.category:
        subset = [lbl for lbl in subset if lbl.get("category") == args.category]
    if args.limit is not None:
        subset = subset[: args.limit]
    if not subset:
        raise SystemExit("no labels selected")

    eval_run_dir = _make_eval_run_dir(args.label)
    started_at = datetime.now(timezone.utc)
    print(f"Running {len(subset)} eval(s) → {eval_run_dir.relative_to(REPO_ROOT)}")

    results = []
    for i, label in enumerate(subset, 1):
        print(f"\n[{i}/{len(subset)}] {label['url']} ({label.get('category', '—')})")
        result = await _evaluate_one(label, eval_run_dir)
        status_map = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}
        status = status_map[result["status"]]
        print(f"  → {status} ({result['wall_clock_s']:.1f}s)")
        if result["status"] == "skipped":
            print(f"    skip: {result['skip_reason']}")
        for msg in result["failures"]:
            print(f"    fail: {msg}")
        for msg in result["warnings"]:
            print(f"    warn: {msg}")
        results.append(result)

    finished_at = datetime.now(timezone.utc)
    manifest = _build_manifest(
        eval_run_dir=eval_run_dir,
        started_at=started_at,
        finished_at=finished_at,
        labels_subset=subset,
        results=results,
        settings={
            "max_steps": 4,
            "limit": args.limit,
            "category_filter": args.category,
            "label": args.label,
        },
    )
    (eval_run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    return _print_summary(manifest, eval_run_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="reasonable-ux Phase 1 eval harness")
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N labels")
    parser.add_argument("--category", type=str, default=None, help="Filter by category")
    parser.add_argument("--label", type=str, default=None, help="Suffix for the eval_runs/ dir (e.g. 'baseline', 'post-phase2')")
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
