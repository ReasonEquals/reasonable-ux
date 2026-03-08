import asyncio
import os
import json
import sys
import subprocess
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tests"))

async def run_suite(url: str, max_steps: int = 8):
    from planner import plan
    from agent_test import run

    suite_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    suite_dir = Path(f"runs/suite_{suite_id}")
    suite_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n🚀 Suite run started: {suite_id}")
    print(f"   URL: {url}\n")

    # Step 1 — Plan
    test_plan = await plan(url)
    test_cases = test_plan["suggested_test_cases"]

    CI_MODE = os.environ.get("CI", "false").lower() == "true"
    if CI_MODE:
        print("🚨 Running in CI mode - headless browser")
    else:
        print("🚨 Running in interactive mode - visible browser")

    print(f"\n▶ Running {len(test_cases)} test cases...\n")

    suite_results = []
    total_input = 0
    total_output = 0

    # Step 2 — Run each test case
    for i, tc in enumerate(test_cases):
        goal = tc["goal"]
        priority = tc["priority"]
        print(f"\n{'='*60}")
        print(f"Test {i+1}/{len(test_cases)} [{priority.upper()}]: {goal}")
        print(f"{'='*60}")

        if CI_MODE and priority in ["low", "medium"]:
            print(f"⏭️ Skipping [{priority.upper()}] in CI")
            suite_results.append({
                "test_number": i + 1,
                "goal": goal,
                "priority": priority,
                "final_status": "skipped",
                "verdict": "Skipped in CI — only HIGH priority tests run in CI",
                "report_path": ""
            })
            continue

        try:
            tokens = await asyncio.wait_for(
                run(url=url, goal=goal, max_steps=max_steps, suite_dir=str(suite_dir)),
                timeout=300
            )
            total_input += tokens.get("input", 0)
            total_output += tokens.get("output", 0)

            # Find the latest run folder inside suite_dir
            run_folders = sorted(suite_dir.iterdir())
            latest = run_folders[-1] if run_folders else None
            report_path = latest / "report.json" if latest else None

            final_status = "unknown"
            verdict = ""
            if report_path and report_path.exists():
                with open(report_path, "r", encoding="utf-8") as f:
                    report_data = json.load(f)
                if report_data:
                    final_status = report_data[-1].get("pass_fail", "unknown")
                    verdict = report_data[-1].get("verdict", "")

            suite_results.append({
                "test_number": i + 1,
                "goal": goal,
                "priority": priority,
                "final_status": final_status,
                "verdict": verdict,
                "report_path": str(latest / "report.html").replace("\\", "/") if latest else ""
            })

            status_icon = "✅" if final_status == "pass" else "❌" if final_status == "fail" else "⚠️"
            print(f"{status_icon} {final_status.upper()}: {verdict}")

        except asyncio.TimeoutError:
            print(f"⏱️ Test {i+1} exceeded max runtime, marking as timeout")
            suite_results.append({
                "test_number": i + 1,
                "goal": goal,
                "priority": priority,
                "final_status": "timeout",
                "verdict": "Test exceeded 300 second time limit",
                "report_path": ""
            })

        except Exception as e:
            print(f"❌ Test {i+1} failed with error: {e}")
            suite_results.append({
                "test_number": i + 1,
                "goal": goal,
                "priority": priority,
                "final_status": "error",
                "verdict": str(e),
                "report_path": ""
            })

    # Step 3 — Build suite report
    passed = sum(1 for r in suite_results if r["final_status"] == "pass")
    failed = sum(1 for r in suite_results if r["final_status"] == "fail")
    errors = sum(1 for r in suite_results if r["final_status"] == "error")
    timeouts = sum(1 for r in suite_results if r["final_status"] == "timeout")
    skipped = sum(1 for r in suite_results if r["final_status"] == "skipped")
    total = len(suite_results)
    suite_status = "PASS" if failed == 0 and errors == 0 and timeouts == 0 else "FAIL"
    suite_color = "#2ecc71" if suite_status == "PASS" else "#e74c3c"

    rows = ""
    for r in suite_results:
        pf = r["final_status"].upper()
        pf_color = "#2ecc71" if pf == "PASS" else "#e74c3c" if pf == "FAIL" else "#888" if pf == "SKIPPED" else "#f39c12"
        priority_color = "#e74c3c" if r["priority"] == "high" else "#f39c12" if r["priority"] == "medium" else "#2ecc71"
        link = f'<a href="{r["report_path"]}" style="color:#00d4ff">View →</a>' if r["report_path"] else "—"
        rows += f"""
        <tr>
            <td>{r['test_number']}</td>
            <td style="max-width:400px">{r['goal']}</td>
            <td style="color:{priority_color};font-weight:bold">{r['priority'].upper()}</td>
            <td style="color:{pf_color};font-weight:bold">{pf}</td>
            <td>{r['verdict']}</td>
            <td>{link}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Suite Report — {suite_id}</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: Arial, sans-serif; margin: 40px; background: #1a1a2e; color: #eee; }}
        h1 {{ font-size: 28px; margin-bottom: 8px; color: #00d4ff; }}
        .subtitle {{ color: #888; margin-bottom: 30px; font-size: 14px; }}
        .status {{ font-size: 24px; font-weight: bold; color: {suite_color}; margin: 16px 0; }}
        .meta {{ color: #888; font-size: 13px; margin: 6px 0; }}
        .stats {{ display: flex; gap: 20px; margin: 24px 0; }}
        .stat {{ background: #16213e; padding: 16px 24px; border-radius: 8px; text-align: center; flex: 1; }}
        .stat .number {{ font-size: 32px; font-weight: bold; }}
        .stat .label {{ font-size: 11px; color: #888; margin-top: 4px; }}
        .pass {{ color: #2ecc71; }}
        .fail {{ color: #e74c3c; }}
        table {{ width: 100%; border-collapse: collapse; background: #16213e; border-radius: 8px; overflow: hidden; }}
        th {{ background: #0f3460; color: #00d4ff; padding: 12px 16px; text-align: left; font-size: 13px; }}
        td {{ padding: 12px 16px; border-bottom: 1px solid #0f3460; vertical-align: top; font-size: 13px; }}
        tr:last-child td {{ border-bottom: none; }}
        tr:hover td {{ background: #0f3460; }}
    </style>
</head>
<body>
    <h1>🧪 Suite Report</h1>
    <p class="subtitle">reasons-qagent</p>
    <p class="meta"><strong>Suite ID:</strong> {suite_id}</p>
    <p class="meta"><strong>URL:</strong> {url}</p>
    <p class="status">Suite Status: {suite_status}</p>
    <div class="stats">
        <div class="stat"><div class="number">{total}</div><div class="label">TOTAL</div></div>
        <div class="stat"><div class="number pass">{passed}</div><div class="label">PASSED</div></div>
        <div class="stat"><div class="number fail">{failed}</div><div class="label">FAILED</div></div>
        <div class="stat"><div class="number" style="color:#f39c12">{errors}</div><div class="label">ERRORS</div></div>
        <div class="stat"><div class="number" style="color:#f39c12">{timeouts}</div><div class="label">TIMEOUTS</div></div>
        <div class="stat"><div class="number" style="color:#888">{skipped}</div><div class="label">SKIPPED</div></div>
        <div class="stat"><div class="number" style="color:#00d4ff">{total_input + total_output:,}</div><div class="label">TOKENS</div></div>
    </div>
    <table>
        <tr>
            <th>#</th>
            <th>Goal</th>
            <th>Priority</th>
            <th>Status</th>
            <th>Verdict</th>
            <th>Report</th>
        </tr>
        {rows}
    </table>
</body>
</html>"""

    suite_report_path = suite_dir / "suite_report.html"
    with open(suite_report_path, "w", encoding="utf-8") as f:
        f.write(html)
    subprocess.run(["python", "build_index.py"])
    print("📊 Dashboard index updated.")

    print(f"\n{'='*60}")
    print(f"📊 Suite complete: {passed}/{total} passed")
    print(f"   Input:  {total_input:,} tokens")
    print(f"   Output: {total_output:,} tokens")
    print(f"   Total:  {total_input + total_output:,} tokens")
    print(f"🌐 Suite report: {suite_report_path}")
    return suite_status == "PASS"

if __name__ == "__main__":
    import argparse
    import sys
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", type=str, default="https://the-internet.herokuapp.com/login")
    parser.add_argument("--steps", type=int, default=8)
    args = parser.parse_args()
    result = asyncio.run(run_suite(url=args.url, max_steps=args.steps))
    sys.exit(0 if result else 1)