import asyncio
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

async def run(url="https://the-internet.herokuapp.com/login", goal=DEFAULT_goal, max_steps=8):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        await page.goto(url)

        conversation = []
        report = []
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_label = goal.split()[0:3]
        run_label = "_".join(run_label).lower().strip(".,!?") + "_test"

        for step in range(8):
            print(f"\n--- Agent Step {step + 1} ---")

            screenshot_path = f"screenshots/{run_label}_{run_id}_step_{step + 1}.png"
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
    "target": "simple CSS selector using id or class preferred over bare tag names (e.g. use '.button' not 'a', '#username' not 'input') — no :contains() — or URL or null",
    "value": "text to type or null",
    "reasoning": "why you chose this action",
    "pass_fail": "pass | fail | in_progress",
    "verdict": "one sentence summary of test status so far"
}}

If your goal is complete, use action: done and give a final pass_fail and verdict.
If you are on step 8, you MUST use action: done with a final pass_fail and verdict — do not continue."""
                    }
                ]
            })

            response = client.messages.create(
                model="claude-opus-4-5",
                max_tokens=1024,
                messages=conversation
            )

            raw = response.content[0].text
            print(raw)

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
                    await page.click(decision["target"])
                elif decision["action"] == "navigate":
                    await page.goto(decision["target"])
                elif decision["action"] == "type":
                    await page.fill(decision["target"], decision["value"])

            except Exception as e:
                print(f"Could not parse action: {e}")
                break

            await asyncio.sleep(1)

        # Save JSON report
        report_path = f"reports/{run_label}_{run_id}.json"
        with open(report_path, "w") as f:
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
                <td><img src="../{entry['screenshot']}" width="200"/></td>
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
        body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
        h1 {{ color: #333; }}
        .status {{ font-size: 24px; font-weight: bold; color: {status_color}; }}
        .goal {{ background: #fff; padding: 15px; border-left: 4px solid #3498db; margin: 20px 0; }}
        table {{ width: 100%; border-collapse: collapse; background: #fff; }}
        th {{ background: #2c3e50; color: white; padding: 10px; text-align: left; }}
        td {{ padding: 10px; border-bottom: 1px solid #ddd; vertical-align: top; }}
        tr:hover {{ background: #f9f9f9; }}
    </style>
</head>
<body>
    <h1>🧪 QA Agent Report</h1>
    <div class="goal"><strong>goal:</strong> {goal}</div>
    <p><strong>Run ID:</strong> {run_id}</p>
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

        html_path = f"reports/{run_label}_{run_id}.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"🌐 HTML report saved: {html_path}")

        await browser.close()

        total_input = sum(r.get("input_tokens", 0) for r in report if "input_tokens" in r)
        total_output = sum(r.get("output_tokens", 0) for r in report if "output_tokens" in r)
        return {"input": total_input, "output": total_output, "total": total_input + total_output}

if __name__ == "__main__":
    asyncio.run(run())