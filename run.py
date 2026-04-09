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
    parser.add_argument("--page-steps", type=int, default=None,
                        help="Max steps per page for --pages runs (default: 12). Overrides --steps for --pages mode.")
    parser.add_argument("--discover", action="store_true",
                        help="Crawl --url to discover internal pages, then run agent on each (UX mode). Overrides --pages if both passed.")
    parser.add_argument("--personas", action="store_true",
                        help="Run multi-persona analysis after audit and include in PDF.")
    parser.add_argument("--static-personas", action="store_true",
                        help="Use built-in static personas instead of generating contextual ones (faster, no extra API call).")
    parser.add_argument("--scout", action="store_true",
                        help="Enable scout mode: cheap text-only pre-screen before full vision eval")
    parser.add_argument("--scout-threshold", type=int, default=3,
                        help="Scout interest score threshold (1-5, default 3); pages scoring below this are skipped")
    parser.add_argument("--provider", choices=["anthropic", "openai", "google"], default="anthropic",
                        help="LLM provider for the agent (default: anthropic)")
    parser.add_argument("--model", default="claude-opus-4-5",
                        help="Model name to use (default: claude-opus-4-5)")
    return parser.parse_args()

async def run_with_plan(url, steps, token_budget, email, password, mode, scout=False, scout_threshold=3, provider: str = "anthropic", model: str = "claude-opus-4-5"):
    from planner import plan
    from agent_test import run

    test_plan = await plan(url)

    high_priority = [tc for tc in test_plan["suggested_test_cases"] if tc["priority"] == "high"]
    candidates = high_priority or test_plan["suggested_test_cases"]
    chosen = candidates[0]["goal"]

    print(f"\n🎯 Selected goal: {chosen}\n")

    total_tokens = await run(url=url, goal=chosen, max_steps=steps, token_budget=token_budget, email=email, password=password, mode=mode, scout=scout, scout_threshold=scout_threshold, provider=provider, model=model)
    return total_tokens

async def run_without_plan(url, goal, steps, token_budget, email, password, mode, scout=False, scout_threshold=3, provider: str = "anthropic", model: str = "claude-opus-4-5"):
    from agent_test import run
    total_tokens = await run(url=url, goal=goal, max_steps=steps, token_budget=token_budget, email=email, password=password, mode=mode, scout=scout, scout_threshold=scout_threshold, provider=provider, model=model)
    return total_tokens

def _existing_run_names(runs_dir="runs"):
    runs_path = Path(runs_dir)
    if not runs_path.exists():
        return set()
    names = set()
    for item in runs_path.iterdir():
        if item.is_dir():
            names.add(item.name)
            # Also capture names of children (domain subfolder pattern)
            for child in item.iterdir():
                if child.is_dir():
                    names.add(child.name)
    return names


def _newest_run_folder(before_names, runs_dir="runs"):
    """Return the run folder created after the before_names snapshot was taken.
    Searches both runs/ directly and one level of domain subfolders."""
    runs_path = Path(runs_dir)
    if not runs_path.exists():
        return None

    def all_run_folders():
        folders = {}
        for item in runs_path.iterdir():
            if item.is_dir() and not item.name.startswith("suite_"):
                # Check if this is a domain subfolder (contains timestamped run dirs)
                children = [c for c in item.iterdir() if c.is_dir()]
                if children and any("_" in c.name for c in children):
                    for child in children:
                        folders[str(child)] = child
                else:
                    folders[str(item)] = item
        return folders

    current = all_run_folders()
    new_folders = {k: v for k, v in current.items()
                   if v.name not in before_names and str(v.parent) != str(runs_path) or v.name not in before_names}

    # Find folders whose names weren't in before_names
    new_by_name = {k: v for k, v in current.items() if v.name not in before_names}
    if new_by_name:
        return sorted(new_by_name.values(), key=lambda f: f.stat().st_mtime, reverse=True)[0]

    # Fallback: most recently modified across all run folders
    all_folders = list(current.values())
    return sorted(all_folders, key=lambda f: f.stat().st_mtime, reverse=True)[0] if all_folders else None


async def run_pages(base_url, goal, steps, token_budget, email, password, mode, pages, scout=False, scout_threshold=3, provider: str = "anthropic", model: str = "claude-opus-4-5", page_steps: int = None):
    """Run the agent once per page sequentially and return collected page_results."""
    from agent_test import run as agent_run

    effective_steps = page_steps if page_steps is not None else 12

    page_results = []
    total_tokens_all = {"input": 0, "output": 0, "total": 0}

    import tempfile
    auth_state_path = None
    if email and password:
        print(f"\n🔐 Pre-authenticating session for {base_url}...")
        async def _do_auth():
            from playwright.async_api import async_playwright as _async_playwright
            from urllib.parse import urlparse as _urlparse
            _origin = _urlparse(base_url).scheme + "://" + _urlparse(base_url).netloc
            _headless = os.environ.get("CI", "false").lower() == "true"
            async with _async_playwright() as _p:
                _browser = await _p.chromium.launch(headless=_headless)
                _context = await _browser.new_context()
                _page = await _context.new_page()
                try:
                    await _page.goto(_origin.rstrip("/") + "/auth/login")
                    await _page.wait_for_load_state("networkidle")
                    print(f"   🌐 Auth page loaded: {_page.url}")

                    # Step 1: fill email and click Continue
                    await _page.wait_for_selector('input[type="email"]', timeout=15000)
                    await _page.click('input[type="email"]')
                    await _page.fill('input[type="email"]', email)
                    await _page.wait_for_timeout(300)
                    try:
                        await _page.click('button:has-text("Continue")', timeout=3000)
                    except Exception:
                        await _page.press('input[type="email"]', 'Enter')

                    # Step 2: wait for password field to appear, fill it and submit
                    _pw_sel = None
                    for _sel in ('input[type="password"]', 'input[name="password"]', 'input[placeholder*="assword"]'):
                        try:
                            await _page.wait_for_selector(_sel, timeout=10000)
                            _pw_sel = _sel
                            break
                        except Exception:
                            continue
                    if _pw_sel is None:
                        raise Exception("Password field not found after clicking Continue")
                    await _page.click(_pw_sel)
                    await _page.fill(_pw_sel, password)
                    try:
                        await _page.click('button[type="submit"]', timeout=5000)
                    except Exception:
                        _login_kw = {"login", "auth", "signin"}
                        if any(k in _page.url.lower() for k in _login_kw):
                            await _page.press(_pw_sel, 'Enter')

                    await _page.wait_for_load_state("networkidle")
                    print(f"   ✅ Auth complete — current URL: {_page.url}")
                except Exception as e:
                    try:
                        await _page.screenshot(path="/tmp/auth_debug.png")
                        print(f"   📸 Debug screenshot: /tmp/auth_debug.png")
                    except Exception:
                        pass
                    await _browser.close()
                    raise RuntimeError(f"Auth failed: {e}")

                _tf = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
                _auth_path = _tf.name
                _tf.close()
                await _context.storage_state(path=_auth_path)
                await _browser.close()
                return _auth_path
        try:
            auth_state_path = await _do_auth()
        except RuntimeError as e:
            print(f"\n❌ {e}")
            print("   Debug screenshot saved to /tmp/auth_debug.png")
            sys.exit(1)
        if auth_state_path:
            print(f"   💾 Session saved to {auth_state_path}")

    for path in pages:
        if path.startswith(("http://", "https://")):
            full_url = path
        else:
            path = path if path.startswith("/") else "/" + path
            full_url = base_url.rstrip("/") + path

        print(f"\n{'='*60}")
        print(f"🌐 Page: {full_url}")
        print(f"{'='*60}")

        # HEAD check — skip 4xx paths before spending agent tokens
        try:
            head = requests.head(
                full_url, timeout=8, allow_redirects=True,
                headers={"User-Agent": "reasonable-ux/1.0"},
            )
            if 400 <= head.status_code < 500 and head.status_code != 405:
                print(f"⚠️  Skipping {path} — HEAD returned {head.status_code}")
                continue
        except requests.RequestException as e:
            print(f"⚠️  HEAD request failed for {path}: {e} — skipping")
            continue

        from agent_test import _infer_goal_from_url
        page_goal = _infer_goal_from_url(full_url, mode)
        before = _existing_run_names()
        tokens = await agent_run(
            url=full_url, goal=page_goal, max_steps=effective_steps,
            token_budget=token_budget, email=email, password=password, mode=mode,
            scout=scout, scout_threshold=scout_threshold, provider=provider, model=model,
            storage_state=auth_state_path,
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

    if auth_state_path:
        try:
            os.unlink(auth_state_path)
        except Exception:
            pass

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
    if args.personas or args.static_personas:
        persona_label = "static" if args.static_personas else "contextual"
        print(f"   Personas: {persona_label}")
    if args.scout:
        print(f"   Scout: enabled (threshold {args.scout_threshold}/5)")
    print(f"   Provider: {args.provider}  Model: {args.model}")
    if args.plan:
        print(f"   Plan:  Planner → Agent\n")
    else:
        print(f"   Goal:  {goal}\n")

    # ── Resolve pages list ────────────────────────────────────────────────────
    if args.discover:
        if args.pages:
            print("ℹ️  --discover takes precedence; ignoring --pages.")
        from urllib.parse import urlparse as _up, urljoin as _urljoin
        _parsed = _up(url)
        base_url = f"{_parsed.scheme}://{_parsed.netloc}"
        print(f"\n🔍 Discovering internal pages on {base_url} (authenticated)...")

        async def _auth_crawl():
            from playwright.async_api import async_playwright as _ap
            from bs4 import BeautifulSoup
            _headless = os.environ.get("CI", "false").lower() == "true"
            async with _ap() as _p:
                _browser = await _p.chromium.launch(headless=_headless)
                _context = await _browser.new_context()
                _page = await _context.new_page()
                if args.email and args.password:
                    try:
                        await _page.goto(url)
                        await _page.wait_for_load_state("networkidle")
                        # Step 1 — fill email and click Continue
                        _email_sel = 'input[type="email"], input[name="email"]'
                        await _page.wait_for_selector(_email_sel, timeout=15000)
                        await _page.locator(_email_sel).first.click()
                        await _page.locator(_email_sel).first.fill(args.email)
                        await _page.wait_for_timeout(300)
                        _continue_sel = (
                            'button:has-text("Continue"), '
                            'button:has-text("Next"), '
                            'button:has-text("Sign in"), '
                            'button[type="submit"]'
                        )
                        try:
                            await _page.locator(_continue_sel).first.click(timeout=3000)
                        except Exception:
                            await _page.locator(_email_sel).first.press('Enter')
                        # Step 2 — fill password if the field appeared
                        _pw_visible = False
                        _pw_sel = 'input[type="password"]'
                        for _sel in ('input[type="password"]', 'input[name="password"]', 'input[placeholder*="assword"]'):
                            try:
                                await _page.wait_for_selector(_sel, timeout=8000)
                                _pw_sel = _sel
                                _pw_visible = True
                                break
                            except Exception:
                                continue
                        if _pw_visible:
                            await _page.click(_pw_sel)
                            await _page.fill(_pw_sel, args.password)
                            _submit_sel = 'button[type="submit"], button:has-text("Sign in")'
                            try:
                                await _page.locator(_submit_sel).first.click(timeout=5000)
                            except Exception:
                                _login_kw = {"login", "auth", "signin"}
                                if any(k in _page.url.lower() for k in _login_kw):
                                    await _page.press(_pw_sel, 'Enter')
                            await _page.wait_for_load_state("networkidle")
                        # Step 3 — confirm auth succeeded
                        import asyncio as _asyncio
                        _login_keywords = {"login", "auth", "signin", "sign-in"}
                        _authed = False
                        for _ in range(20):
                            _cur = _page.url
                            if _cur != url and not any(k in _cur.lower() for k in _login_keywords):
                                _authed = True
                                break
                            await _asyncio.sleep(0.5)
                        if _authed:
                            print(f"   ✅ Auth complete — {_page.url}")
                        else:
                            print(f"   ⚠️  Auth may not have succeeded — {_page.url}")
                    except Exception as _e:
                        print(f"   ⚠️  Auth failed: {_e} — crawling unauthenticated")
                await _page.goto(base_url)
                await _page.wait_for_load_state("networkidle")
                html = await _page.content()
                await _browser.close()

            _exclude = {"auth", "login", "register", "logout", "signin", "sign-in", "sign-out"}
            soup = BeautifulSoup(html, "html.parser")
            paths = {"/"}
            for tag in soup.find_all("a", href=True):
                href = tag["href"].strip()
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                _p2 = _up(_urljoin(base_url, href))
                if _p2.netloc != _parsed.netloc:
                    continue
                path = _p2.path or "/"
                parts = path.lower().strip("/").split("/")
                if any(part in _exclude for part in parts):
                    continue
                paths.add(path)
            return sorted(p for p in paths if not p.startswith("/cdn-cgi/"))

        pages = asyncio.run(_auth_crawl())
        url = base_url
        if pages:
            print(f"   Found {len(pages)} path(s): {' '.join(pages)}\n")
        else:
            print("⚠️  No pages discovered. Exiting.")
            sys.exit(1)
    elif args.pages:
        pages = args.pages
        if not args.url and pages[0].startswith(("http://", "https://")):
            from urllib.parse import urlparse as _up
            _p = _up(pages[0])
            url = f"{_p.scheme}://{_p.netloc}"
    else:
        pages = None

    if pages:
        if args.mode != "ux":
            print("ℹ️  --pages/--discover requires UX mode; switching to --mode ux.")
            args.mode = "ux"

        page_results, total_tokens = asyncio.run(
            run_pages(url, goal, args.steps, args.token_budget,
                      args.email, args.password, args.mode, pages,
                      scout=args.scout, scout_threshold=args.scout_threshold,
                      provider=args.provider, model=args.model,
                      page_steps=args.page_steps)
        )

        build_index()

        if page_results:
            from generate_report import stitch_reports
            from datetime import datetime
            from urllib.parse import urlparse

            hostname = urlparse(url).hostname or url.replace("https://", "").replace("http://", "")
            safe_domain = hostname.replace(".", "_")
            now = datetime.now()
            iso_date = now.strftime("%Y-%m-%d")
            iso_time = now.strftime("%H-%M-%S")
            suite_folder = Path("runs") / f"suite_{now.strftime('%Y%m%d_%H%M%S')}"
            suite_folder.mkdir(parents=True, exist_ok=True)
            output_path = suite_folder / f"{safe_domain}_{iso_date}_{iso_time}_multi_page.pdf"

            persona_results = None
            if args.personas or args.static_personas:
                from persona_orchestrator import orchestrate
                combined_report = [e for pr in page_results for e in pr["report"]]
                print(f"\n🧠 Running persona analysis ({persona_label})...")
                persona_results = asyncio.run(
                    orchestrate(url, combined_report, use_static=args.static_personas)
                )

            print(f"\n📄 Stitching multi-page report → {output_path}")
            stitch_reports(page_results, url, output_path, persona_results=persona_results)

        if total_tokens and total_tokens["total"]:
            print(f"\n📊 Total tokens used this run:")
            print(f"   Input:  {total_tokens['input']:,}")
            print(f"   Output: {total_tokens['output']:,}")
            print(f"   Total:  {total_tokens['total']:,}")

    else:
        before = _existing_run_names()

        if args.plan:
            total_tokens = asyncio.run(run_with_plan(url, args.steps, args.token_budget, args.email, args.password, args.mode, scout=args.scout, scout_threshold=args.scout_threshold, provider=args.provider, model=args.model))
        else:
            total_tokens = asyncio.run(run_without_plan(url, goal, args.steps, args.token_budget, args.email, args.password, args.mode, scout=args.scout, scout_threshold=args.scout_threshold, provider=args.provider, model=args.model))

        build_index()

        run_dir_for_pdf = _newest_run_folder(before)
        if run_dir_for_pdf:
            rp = run_dir_for_pdf / "report.json"
            if rp.exists():
                from generate_report import build_pdf
                single_report = json.loads(rp.read_text(encoding="utf-8"))
                pdf_path = run_dir_for_pdf / "report.pdf"
                build_pdf(run_dir_for_pdf, single_report, url, pdf_path)
                print(f"📄 PDF report saved: {pdf_path}")

        if (args.personas or args.static_personas) and before is not None:
            run_folder = _newest_run_folder(before)
            if run_folder and args.mode == "ux":
                rp = run_folder / "report.json"
                if rp.exists():
                    single_report = json.loads(rp.read_text(encoding="utf-8"))
                    from persona_orchestrator import orchestrate
                    from generate_report import build_pdf
                    from datetime import datetime as _dt
                    from urllib.parse import urlparse as _up
                    print(f"\n🧠 Running persona analysis ({persona_label})...")
                    persona_results = asyncio.run(
                        orchestrate(url, single_report, use_static=args.static_personas)
                    )
                    safe_domain = (_up(url).hostname or url).replace(".", "_")
                    _now = _dt.now()
                    iso_date = _now.strftime("%Y-%m-%d")
                    iso_time = _now.strftime("%H-%M-%S")
                    output_path = run_folder / f"{safe_domain}_{iso_date}_{iso_time}_persona.pdf"
                    print(f"\n📄 Generating persona report → {output_path}")
                    build_pdf(run_folder, single_report, url, output_path,
                              persona_results=persona_results)
            elif run_folder:
                print("⚠️  --personas requires --mode ux")

        if total_tokens:
            print(f"\n📊 Total tokens used this run:")
            print(f"   Input:  {total_tokens['input']:,}")
            print(f"   Output: {total_tokens['output']:,}")
            print(f"   Total:  {total_tokens['total']:,}")

    print("\n✅ Run complete. Open dashboard to view results.")
