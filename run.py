import asyncio
import argparse
import json
import requests
import subprocess
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tests"))

def parse_args():
    parser = argparse.ArgumentParser(description="reasons-qagent orchestrator")
    parser.add_argument("--url", type=str, help="Target URL to test")
    parser.add_argument("--goal", type=str, help="Test goal for the agent")
    parser.add_argument("--steps", type=int, default=10, help="Max steps (default: 10)")
    parser.add_argument("--token-budget", type=int, default=None, help="Max tokens per test (default: unlimited)")
    parser.add_argument("--plan", action="store_true", help="Run planner first to generate test cases")
    parser.add_argument("--email", type=str, default=None, help="Email/username for login or signup forms")
    parser.add_argument("--password", type=str, default=None, help="Password for login or signup forms")
    parser.add_argument("--mode", type=str, default="qa", choices=["qa", "ux"], help="Test mode: qa (functional pass/fail) or ux (UX quality evaluation)")
    parser.add_argument("--pages", nargs="+", type=str, default=None,
                        help="URL paths to test sequentially (e.g. / /pricing /about). Appended to --url. Requires --mode ux.")
    parser.add_argument("--discover", action="store_true",
                        help="Crawl --url to discover internal pages, then run agent on each (UX mode). Overrides --pages if both passed.")
    return parser.parse_args()

async def run_with_plan(url, steps, token_budget, email, password, mode):
    from planner import plan
    from agent_test import run

    test_plan = await plan(url)

    high_priority = [tc for tc in test_plan["suggested_test_cases"] if tc["priority"] == "high"]
    candidates = high_priority or test_plan["suggested_test_cases"]
    chosen = candidates[0]["goal"]

    print(f"\n🎯 Selected goal: {chosen}\n")

    total_tokens = await run(url=url, goal=chosen, max_steps=steps, token_budget=token_budget, email=email, password=password, mode=mode)
    return total_tokens

async def run_without_plan(url, goal, steps, token_budget, email, password, mode):
    from agent_test import run
    total_tokens = await run(url=url, goal=goal, max_steps=steps, token_budget=token_budget, email=email, password=password, mode=mode)
    return total_tokens

def _existing_run_names(runs_dir="runs"):
    runs_path = Path(runs_dir)
    if not runs_path.exists():
        return set()
    return {f.name for f in runs_path.iterdir() if f.is_dir()}


def _newest_run_folder(before_names, runs_dir="runs"):
    """Return the run folder created after the before_names snapshot was taken."""
    runs_path = Path(runs_dir)
    if not runs_path.exists():
        return None
    current = {f.name: f for f in runs_path.iterdir() if f.is_dir()}
    new_names = set(current.keys()) - before_names
    if new_names:
        return current[sorted(new_names, reverse=True)[0]]
    # Fallback: most recently modified
    folders = sorted(current.values(), key=lambda f: f.stat().st_mtime, reverse=True)
    return folders[0] if folders else None


async def run_pages(base_url, goal, steps, token_budget, email, password, mode, pages):
    """Run the agent once per page sequentially and return collected page_results."""
    from agent_test import run as agent_run

    page_results = []
    total_tokens_all = {"input": 0, "output": 0, "total": 0}

    for path in pages:
        path = path if path.startswith("/") else "/" + path
        full_url = base_url.rstrip("/") + path

        print(f"\n{'='*60}")
        print(f"🌐 Page: {path}  →  {full_url}")
        print(f"{'='*60}")

        # HEAD check — skip 4xx paths before spending agent tokens
        try:
            head = requests.head(
                full_url, timeout=8, allow_redirects=True,
                headers={"User-Agent": "reasonable-ux/1.0"},
            )
            if 400 <= head.status_code < 500:
                print(f"⚠️  Skipping {path} — HEAD returned {head.status_code}")
                continue
        except requests.RequestException as e:
            print(f"⚠️  HEAD request failed for {path}: {e} — skipping")
            continue

        before = _existing_run_names()
        tokens = await agent_run(
            url=full_url, goal=goal, max_steps=steps,
            token_budget=token_budget, email=email, password=password, mode=mode,
        )
        run_folder = _newest_run_folder(before)

        if tokens:
            total_tokens_all["input"]  += tokens["input"]
            total_tokens_all["output"] += tokens["output"]
            total_tokens_all["total"]  += tokens["total"]

        if run_folder:
            rp = run_folder / "report.json"
            if rp.exists():
                report = json.loads(rp.read_text(encoding="utf-8"))
                page_results.append({
                    "path":       path,
                    "url":        full_url,
                    "run_folder": run_folder,
                    "report":     report,
                })
                print(f"   📂 Collected results from {run_folder.name}")
            else:
                print(f"⚠️  No report.json found in {run_folder}")
        else:
            print(f"⚠️  Could not locate run folder for {path}")

    return page_results, total_tokens_all


def build_index():
    subprocess.run(["python", "build_index.py"])
    print("📊 Dashboard index updated.")

if __name__ == "__main__":
    args = parse_args()

    url  = args.url  or "https://the-internet.herokuapp.com/login"
    goal = args.goal or "Test the login form with valid and invalid credentials."

    print(f"\n🚀 Starting test run")
    print(f"   URL:   {url}")
    print(f"   Steps: {args.steps}")
    print(f"   Mode:  {args.mode.upper()}")
    if args.token_budget:
        print(f"   Budget: {args.token_budget:,} tokens")
    if args.email:
        print(f"   Email: {args.email}")
    if args.discover:
        print(f"   Discover: enabled" + (" (--pages ignored)" if args.pages else ""))
    elif args.pages:
        print(f"   Pages: {' '.join(args.pages)}")
    if args.plan:
        print(f"   Plan:  Planner → Agent\n")
    else:
        print(f"   Goal:  {goal}\n")

    # ── Resolve pages list ────────────────────────────────────────────────────
    if args.discover:
        if args.pages:
            print("ℹ️  --discover takes precedence; ignoring --pages.")
        from site_crawler import crawl
        print(f"\n🔍 Crawling {url} for internal links...")
        pages = crawl(url)
        if pages:
            print(f"   Found {len(pages)} path(s): {' '.join(pages)}\n")
        else:
            print("⚠️  No pages discovered. Exiting.")
            sys.exit(1)
    elif args.pages:
        pages = args.pages
    else:
        pages = None

    if pages:
        if args.mode != "ux":
            print("ℹ️  --pages/--discover requires UX mode; switching to --mode ux.")
            args.mode = "ux"

        page_results, total_tokens = asyncio.run(
            run_pages(url, goal, args.steps, args.token_budget,
                      args.email, args.password, args.mode, pages)
        )

        build_index()

        if page_results:
            from generate_report import stitch_reports
            from datetime import datetime
            from urllib.parse import urlparse

            hostname = urlparse(url).hostname or url.replace("https://", "").replace("http://", "")
            safe_domain = hostname.replace(".", "_")
            iso_date = datetime.now().strftime("%Y-%m-%d")
            Path("runs").mkdir(exist_ok=True)
            output_path = Path("runs") / f"{safe_domain}_{iso_date}_multi_page.pdf"

            print(f"\n📄 Stitching multi-page report → {output_path}")
            stitch_reports(page_results, url, output_path)

        if total_tokens and total_tokens["total"]:
            print(f"\n📊 Total tokens used this run:")
            print(f"   Input:  {total_tokens['input']:,}")
            print(f"   Output: {total_tokens['output']:,}")
            print(f"   Total:  {total_tokens['total']:,}")

    else:
        if args.plan:
            total_tokens = asyncio.run(run_with_plan(url, args.steps, args.token_budget, args.email, args.password, args.mode))
        else:
            total_tokens = asyncio.run(run_without_plan(url, goal, args.steps, args.token_budget, args.email, args.password, args.mode))

        build_index()

        if total_tokens:
            print(f"\n📊 Total tokens used this run:")
            print(f"   Input:  {total_tokens['input']:,}")
            print(f"   Output: {total_tokens['output']:,}")
            print(f"   Total:  {total_tokens['total']:,}")

    print("\n✅ Run complete. Open dashboard to view results.")
