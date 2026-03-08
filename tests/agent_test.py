import asyncio
import os
from playwright.async_api import async_playwright
from anthropic import Anthropic
from dotenv import load_dotenv
import base64
import json
from datetime import datetime

load_dotenv()
client = Anthropic()



async def screenshot_as_base64(page):
    screenshot = await page.screenshot(type="jpeg", quality=40)
    return base64.b64encode(screenshot).decode("utf-8")

DEFAULT_goal = "Test the login form. Try logging in with valid credentials (username: tomsmith, password: SuperSecretPassword!), then try with invalid credentials and observe the error handling."

async def run(url="https://the-internet.herokuapp.com/login", goal=DEFAULT_goal, max_steps=8, suite_dir=None, token_budget=None):
    async with async_playwright() as p:
        headless = os.environ.get("CI", "false").lower() == "true"
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page()

        await page.goto(url)

        conversation = []
        report = []
        tokens_used = 0
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_label = "_".join(goal.split()[0:3]).lower().strip(".,!?")
        if suite_dir:
            run_dir = f"{suite_dir}/{run_id}_{run_label}"
        else:
            run_dir = f"runs/{run_id}_{run_label}"
        screenshots_dir = f"{run_dir}/screenshots"
        os.makedirs(screenshots_dir, exist_ok=True)

        for step in range(max_steps):
            # Check token budget before next API call
            if token_budget and tokens_used >= token_budget:
                print(f"💰 Token budget exceeded ({tokens_used:,}/{token_budget:,}), stopping test")
                report.append({
                    "step": step + 1,
                    "screenshot": "",
                    "observation": "Token budget exceeded",
                    "action": "budget_stop",
                    "target": None,
                    "reasoning": f"Used {tokens_used:,} of {token_budget:,} token budget",
                    "pass_fail": "fail",
                    "verdict": f"Test stopped — token budget of {token_budget:,} exceeded"
                })
                break

            print(f"\n--- Agent Step {step + 1} ---")

            screenshot_path = f"{screenshots_dir}/step_{step + 1}.png"
            await page.screenshot(path=screenshot_path)
            encoded = await screenshot_as_base64(page)
            print(f"📸 Screenshot saved: {screenshot_path}")

# Strip images from previous messages to reduce token cost
            for msg in conversation:
                if msg["role"] == "user" and isinstance(msg["content"], list):
                    msg["content"] = [
                        block for block in msg["content"] 
                        if block.get("type") != "image"
                    ]

            conversation.append({
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": encoded
                        }
                    },
                    {
                        "type": "text",
                        "text": f"""You are a QA agent. Your goal is: {goal}

Current step: {step + 1}

Respond in JSON with exactly this shape:
{{
    "observation": "what you see on the page",
    "action": "click | type | navigate | done",
    "target": "simple CSS selector — prefer id over class over tag (e.g. '#username', 'button[type=submit]', 'input[name=password]') — avoid generic selectors like '.button' or bare 'a' — no :contains() — or URL or null",
    "value": "text to type or null",
    "reasoning": "why you chose this action",
    "pass_fail": "pass | fail | in_progress",
    "verdict": "one sentence summary of test status so far"
}}

If your goal is complete, use action: done and give a final pass_fail and verdict.
If you are on step {max_steps}, you MUST use action: done with a final pass_fail and verdict — do not continue."""
                    }
                ]
            })

            response = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1024,
                messages=conversation
            )

            raw = response.content[0].text
            tokens_used += response.usage.input_tokens + response.usage.output_tokens
            print(raw)
            if token_budget:
                print(f"💰 Tokens: {tokens_used:,}/{token_budget:,}")

            conversation.append({
                "role": "assistant",
                "content": raw
            })

            try:
                clean = raw.replace("```json", "").replace("```", "").strip()
                decision = json.loads(clean)

                report.append({
                    "step": step + 1,
                    "screenshot": screenshot_path,
                    "observation": decision["observation"],
                    "action": decision["action"],
                    "target": decision.get("target"),
                    "reasoning": decision["reasoning"],
                    "pass_fail": decision.get("pass_fail", "in_progress"),
                    "verdict": decision.get("verdict", ""),
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens
                })

                if decision["action"] == "done":
                    print(f"\n✅ Agent complete — {decision.get('pass_fail', '').upper()}: {decision.get('verdict', '')}")
                    break
                elif decision["action"] == "click":
                    await asyncio.wait_for(page.click(decision["target"]), timeout=10)
                elif decision["action"] == "navigate":
                    await asyncio.wait_for(page.goto(decision["target"]), timeout=15)
                elif decision["action"] == "type":
                    await asyncio.wait_for(page.fill(decision["target"], decision["value"]), timeout=10)

            except asyncio.TimeoutError:
                print(f"⏱️ Step {step + 1} timed out")
                report.append({
                    "step": step + 1,
                    "screenshot": screenshot_path,
                    "observation": "Step timed out",
                    "action": "timeout",
                    "target": decision.get("target"),
                    "reasoning": "Action exceeded time limit",
                    "pass_fail": "fail",
                    "verdict": f"Step {step + 1} timed out waiting for action to complete"
                })
                break
            except Exception as e:
                print(f"Could not parse action: {e}")
                break

            await asyncio.sleep(1)

        # Save JSON report
        report_path = f"{run_dir}/report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\n📄 JSON report saved: {report_path}")

        # Build HTML report
        final_status = report[-1].get("pass_fail", "unknown").upper()
        status_color = "#2ecc71" if final_status == "PASS" else "#e74c3c" if final_status == "FAIL" else "#f39c12"

        rows = ""
        for entry in report:
            pf = entry.get("pass_fail", "").upper()
            pf_color = "#2ecc71" if pf == "PASS" else "#e74c3c" if pf == "FAIL" else "#f39c12"
            rows += f"""
            <tr>
                <td>{entry['step']}</td>
                <td><img src="screenshots/step_{entry['step']}.png" width="200"/></td>
                <td>{entry['observation']}</td>
                <td>{entry['action']}</td>
                <td>{entry['reasoning']}</td>
                <td style="color:{pf_color};font-weight:bold">{pf}</td>
                <td>{entry.get('verdict','')}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>QA Report — {run_label} — {run_id}</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: Arial, sans-serif; margin: 40px; background: #1a1a2e; color: #eee; }}
        h1 {{ font-size: 28px; margin-bottom: 8px; color: #00d4ff; }}
        .status {{ font-size: 24px; font-weight: bold; color: {status_color}; margin: 16px 0; }}
        .goal {{ background: #16213e; padding: 15px; border-left: 4px solid #00d4ff; margin: 20px 0; border-radius: 4px; }}
        .meta {{ color: #888; font-size: 13px; margin: 8px 0; }}
        table {{ width: 100%; border-collapse: collapse; background: #16213e; border-radius: 8px; overflow: hidden; margin-top: 20px; }}
        th {{ background: #0f3460; color: #00d4ff; padding: 12px 16px; text-align: left; font-size: 13px; }}
        td {{ padding: 12px 16px; border-bottom: 1px solid #0f3460; vertical-align: top; font-size: 13px; }}
        tr:last-child td {{ border-bottom: none; }}
        tr:hover td {{ background: #0f3460; }}
        img {{ border-radius: 4px; border: 1px solid #0f3460; }}
    </style>
</head>
<body>
    <h1>🧪 QA Agent Report</h1>
    <div class="goal"><strong>goal:</strong> {goal}</div>
    <p class="meta"><strong>Run ID:</strong> {run_id}</p>
    <p class="status">Final Status: {final_status}</p>
    <table>
        <tr>
            <th>Step</th>
            <th>Screenshot</th>
            <th>Observation</th>
            <th>Action</th>
            <th>Reasoning</th>
            <th>Pass/Fail</th>
            <th>Verdict</th>
</tr>
        {rows}
    </table>
</body>
</html>"""

        html_path = f"{run_dir}/report.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"🌐 HTML report saved: {html_path}")

        await asyncio.sleep(0.5)
        await asyncio.wait_for(browser.close(), timeout=10)

        total_input = sum(r.get("input_tokens", 0) for r in report if "input_tokens" in r)
        total_output = sum(r.get("output_tokens", 0) for r in report if "output_tokens" in r)
        return {"input": total_input, "output": total_output, "total": total_input + total_output}

if __name__ == "__main__":
    asyncio.run(run())