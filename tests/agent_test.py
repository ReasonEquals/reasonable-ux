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


def _sanitize_selector(selector):
    """Strip comma-separated selector parts that use :contains(), which Playwright does not support."""
    if selector is None:
        return selector
    parts = [p.strip() for p in selector.split(",")]
    clean = [p for p in parts if ":contains(" not in p]
    if not clean:
        raise ValueError(f"All selector parts were blocked (contained :contains()): {selector!r}")
    if len(clean) < len(parts):
        blocked = [p for p in parts if ":contains(" in p]
        print(f"⚠️  Stripped blocked selector parts: {blocked}")
    return ", ".join(clean)


def _build_prompt(goal, step, max_steps, email, password, mode, url=None):
    creds_block = ""
    if email or password:
        creds_block = f"\nIf you encounter a login or signup form, use these credentials:\n"
        if email:
            creds_block += f"  Email/Username: {email}\n"
        if password:
            creds_block += f"  Password: {password}\n"

    url_block = f"\nYou are evaluating {url}. Never navigate to a different domain — if you find yourself on a different domain, use navigate to return to {url}.\n" if url else ""

    if mode == "ux":
        return f"""You are a UX evaluator. Your goal is: {goal}
{creds_block}{url_block}
You are evaluating this site as a CPA or accountant at a mid-size firm. You have been asked by your firm's tech stack committee to research and vet software options. You care about:
- Whether the value proposition is immediately clear to someone with an accounting background
- Whether pricing and trial terms are easy to find and understand
- Whether the product feels trustworthy and professional enough to recommend to a partner at the firm
- Whether you could explain what the product does and why it's worth trying in two sentences or less

You are also evaluating this site as a B2B SaaS specialist who has evaluated hundreds of SaaS products and understands how early-stage B2B companies operate. You know that pricing is often intentionally withheld to drive trial signups, that social proof is sparse at the startup stage, and that what matters is whether the value proposition is immediately clear, the trial path is frictionless, and the product feels credible to its target buyer. You evaluate against B2B SaaS norms, not consumer web standards, and only flag things that are genuinely unusual or problematic for the category.

You are also evaluating this site as an experienced SaaS UX designer and web professional who evaluates landing pages and onboarding flows for a living. You care about:
- Visual hierarchy and whether the information architecture serves the target buyer
- Whether the above-the-fold content earns the scroll
- Cognitive friction in the signup flow
- Whether trust signals are placed effectively for the stage and category
- Whether the overall design communicates credibility and quality to a professional audience

All three perspectives inform your evaluation: the CPA assesses fit and trustworthiness for the target buyer, the B2B SaaS specialist evaluates against category norms, and the UX designer evaluates craft and execution.

Current step: {step + 1}

Navigate the page and evaluate the user experience. Respond in JSON with exactly this shape:
{{
    "observation": "what you see on the page",
    "action": "click | type | navigate | done — navigate requires a full URL starting with http:// or https://; to follow a link use click with its CSS selector instead",
    "target": "simple CSS selector — prefer id over class over tag (e.g. '#username', 'button[type=submit]', 'input[name=password]') — avoid generic selectors like '.button' or bare 'a' — no :contains() — or URL or null",
    "value": "text to type or null",
    "cta_clarity": {{"score": 1-5, "note": "Is the primary call-to-action obvious and well-labeled?"}},
    "copy_quality": {{"score": 1-5, "note": "Is the copy clear, concise, and free of confusion?"}},
    "flow_smoothness": {{"score": 1-5, "note": "Does the interaction feel smooth and logical?"}},
    "first_impression": "one sentence gut reaction to what you see",
    "friction_points": ["list any moments of confusion, hesitation, or extra effort required"],
    "recommendations": ["one specific, actionable fix per friction point — not generic advice, a concrete change. E.g. 'No pricing above the fold → add a line near the CTA that says Plans start at $X/month'"],
    "confidence": "high | medium | low — high if you navigated the page fully and evaluated real content; medium if you saw the page but could not interact with some elements; low if you were blocked, hit an error, or only saw partial content",
    "pass_fail": "pass | fail | in_progress",
    "verdict": "one sentence UX summary so far"
}}

Score rubric: 1=very poor, 2=poor, 3=acceptable, 4=good, 5=excellent.
pass_fail should reflect overall UX quality: pass if average score >= 3, fail if < 3, in_progress while still navigating.

If your goal is complete, use action: done and give final scores and verdict.
If you are on step {max_steps}, you MUST use action: done — do not continue."""

    # Default: qa mode
    return f"""You are a QA agent. Your goal is: {goal}
{creds_block}{url_block}
Current step: {step + 1}

Respond in JSON with exactly this shape:
{{
    "observation": "what you see on the page",
    "action": "click | type | navigate | done — navigate requires a full URL starting with http:// or https://; to follow a link use click with its CSS selector instead",
    "target": "simple CSS selector — prefer id over class over tag (e.g. '#username', 'button[type=submit]', 'input[name=password]') — avoid generic selectors like '.button' or bare 'a' — no :contains() — or URL or null",
    "value": "text to type or null",
    "reasoning": "why you chose this action",
    "pass_fail": "pass | fail | in_progress",
    "verdict": "one sentence summary of test status so far"
}}

If your goal is complete, use action: done and give a final pass_fail and verdict.
If you are on step {max_steps}, you MUST use action: done with a final pass_fail and verdict — do not continue."""


BELOW_FOLD_PROMPT = """You are evaluating this page as a CPA at a mid-size firm vetting software for their tech stack, a B2B SaaS specialist who understands early-stage startup norms, and an experienced SaaS UX designer. The agent that evaluated this page could only see above the fold. Look at the full page and identify anything below the fold that is relevant to the evaluation — additional value propositions, pricing signals, social proof, trust indicators, feature explanations, or UX issues. Return a JSON object with two fields: below_fold_findings (array of strings) and below_fold_score_adjustments (object where each key is a dimension name and each value is {"adjusted_score": <integer 1-5>, "reason": "one sentence explanation"}). Only include adjustments for these three dimensions if applicable: cta_clarity, copy_quality, flow_smoothness."""


def _build_below_fold_html(below_fold):
    if not below_fold:
        return ""
    findings = below_fold.get("below_fold_findings", [])
    adjustments = below_fold.get("below_fold_score_adjustments", {})
    findings_html = "".join(f'<li style="margin-bottom:6px">{f}</li>' for f in findings)
    adj_rows = ""
    dim_labels = {"cta_clarity": "CTA Clarity", "copy_quality": "Copy Quality", "flow_smoothness": "Flow Smoothness"}
    for dim, val in adjustments.items():
        label = dim_labels.get(dim, dim.replace("_", " ").title())
        score = (val.get("adjusted_score") or val.get("adjustment", "—")) if isinstance(val, dict) else val
        reason = val.get("reason", "") if isinstance(val, dict) else ""
        color = "#2ecc71" if isinstance(score, (int, float)) and score >= 4 else "#f39c12" if isinstance(score, (int, float)) and score >= 3 else "#e74c3c"
        adj_rows += f"""
        <tr>
            <td style="font-weight:bold">{label}</td>
            <td style="color:{color};font-weight:bold">{score}/5</td>
            <td>{reason}</td>
        </tr>"""
    adj_section = f"""
    <h3 style="color:#00d4ff;font-size:15px;margin:16px 0 8px">Score Adjustments</h3>
    <table style="width:100%;border-collapse:collapse;background:#16213e;border-radius:8px;overflow:hidden">
        <tr>
            <th style="background:#0f3460;color:#00d4ff;padding:10px 14px;text-align:left;font-size:12px">Dimension</th>
            <th style="background:#0f3460;color:#00d4ff;padding:10px 14px;text-align:left;font-size:12px">Adjusted Score</th>
            <th style="background:#0f3460;color:#00d4ff;padding:10px 14px;text-align:left;font-size:12px">Reason</th>
        </tr>
        {adj_rows}
    </table>""" if adj_rows else ""
    return f"""
    <div style="margin-top:32px;padding:20px;background:#16213e;border-left:4px solid #00d4ff;border-radius:4px">
        <h2 style="color:#00d4ff;font-size:18px;margin-bottom:12px">🔍 Below-the-Fold Analysis</h2>
        <h3 style="color:#00d4ff;font-size:15px;margin-bottom:8px">Findings</h3>
        <ul style="padding-left:20px;color:#eee;font-size:13px;line-height:1.6">{findings_html}</ul>
        {adj_section}
    </div>"""


async def _run_below_fold_analysis(page, run_dir, url):
    print("\n🔍 Running below-the-fold analysis...")
    await page.goto(url, wait_until="networkidle")
    fp_path = f"{run_dir}/full_page.jpeg"
    await page.screenshot(path=fp_path, full_page=True, type="jpeg", quality=60)
    print(f"📸 Full-page screenshot saved: {fp_path}")

    with open(fp_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded}
                },
                {"type": "text", "text": BELOW_FOLD_PROMPT}
            ]
        }]
    )

    raw = response.content[0].text
    print(f"💰 Below-fold analysis tokens: {response.usage.input_tokens + response.usage.output_tokens:,}")
    try:
        clean = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except Exception as e:
        print(f"⚠️  Could not parse below-fold analysis: {e}")
        print(f"Raw response:\n{raw}")
        return None


def _build_html_report(report, goal, run_id, run_label, mode, below_fold=None):
    final_status = report[-1].get("pass_fail", "unknown").upper()
    status_color = "#2ecc71" if final_status == "PASS" else "#e74c3c" if final_status == "FAIL" else "#f39c12"

    shared_style = """
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: Arial, sans-serif; margin: 40px; background: #1a1a2e; color: #eee; }
        h1 { font-size: 28px; margin-bottom: 8px; color: #00d4ff; }
        .status { font-size: 24px; font-weight: bold; margin: 16px 0; }
        .goal { background: #16213e; padding: 15px; border-left: 4px solid #00d4ff; margin: 20px 0; border-radius: 4px; }
        .meta { color: #888; font-size: 13px; margin: 8px 0; }
        table { width: 100%; border-collapse: collapse; background: #16213e; border-radius: 8px; overflow: hidden; margin-top: 20px; }
        th { background: #0f3460; color: #00d4ff; padding: 12px 16px; text-align: left; font-size: 13px; }
        td { padding: 12px 16px; border-bottom: 1px solid #0f3460; vertical-align: top; font-size: 13px; }
        tr:last-child td { border-bottom: none; }
        tr:hover td { background: #0f3460; }
        img { border-radius: 4px; border: 1px solid #0f3460; }
        .score { font-weight: bold; }
        .s5 { color: #2ecc71; } .s4 { color: #27ae60; } .s3 { color: #f39c12; }
        .s2 { color: #e67e22; } .s1 { color: #e74c3c; }
        .friction { color: #f39c12; font-size: 12px; }
    """

    if mode == "ux":
        rows = ""
        for entry in report:
            pf = entry.get("pass_fail", "").upper()
            pf_color = "#2ecc71" if pf == "PASS" else "#e74c3c" if pf == "FAIL" else "#f39c12"

            def score_cell(field):
                obj = entry.get(field)
                if not obj:
                    return "—"
                s = obj.get("score", 0)
                cls = f"s{min(max(int(s), 1), 5)}"
                return f'<span class="score {cls}">{s}/5</span><br><span style="color:#aaa;font-size:11px">{obj.get("note","")}</span>'

            friction = entry.get("friction_points", [])
            friction_html = "".join(f'<div class="friction">• {f}</div>' for f in friction) if friction else "—"
            recs = entry.get("recommendations", [])
            recs_html = "".join(f'<div style="color:#00d4ff;font-size:11px">→ {r}</div>' for r in recs) if recs else ""

            conf = entry.get("confidence", "")
            conf_color = "#2ecc71" if conf == "high" else "#f39c12" if conf == "medium" else "#e74c3c" if conf == "low" else "#888"

            rows += f"""
            <tr>
                <td>{entry['step']}</td>
                <td><img src="screenshots/step_{entry['step']}.png" width="200"/></td>
                <td>{entry['observation']}</td>
                <td>{entry['action']}</td>
                <td>{score_cell('cta_clarity')}</td>
                <td>{score_cell('copy_quality')}</td>
                <td>{score_cell('flow_smoothness')}</td>
                <td>{entry.get('first_impression', '—')}</td>
                <td>{friction_html}{recs_html}</td>
                <td style="color:{conf_color};font-weight:bold">{conf.upper() if conf else "—"}</td>
                <td style="color:{pf_color};font-weight:bold">{pf}</td>
                <td>{entry.get('verdict','')}</td>
            </tr>"""

        return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>UX Report — {run_label} — {run_id}</title>
    <style>{shared_style}</style>
</head>
<body>
    <h1>🎨 UX Evaluation Report</h1>
    <div class="goal"><strong>Goal:</strong> {goal}</div>
    <p class="meta"><strong>Run ID:</strong> {run_id}</p>
    <p class="status" style="color:{status_color}">Final Status: {final_status}</p>
    <table>
        <tr>
            <th>Step</th>
            <th>Screenshot</th>
            <th>Observation</th>
            <th>Action</th>
            <th>CTA Clarity</th>
            <th>Copy Quality</th>
            <th>Flow</th>
            <th>First Impression</th>
            <th>Friction / Fixes</th>
            <th>Confidence</th>
            <th>Pass/Fail</th>
            <th>Verdict</th>
        </tr>
        {rows}
    </table>
    {_build_below_fold_html(below_fold)}
</body>
</html>"""

    # Default: qa mode
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

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>QA Report — {run_label} — {run_id}</title>
    <style>{shared_style}</style>
</head>
<body>
    <h1>🧪 QA Agent Report</h1>
    <div class="goal"><strong>goal:</strong> {goal}</div>
    <p class="meta"><strong>Run ID:</strong> {run_id}</p>
    <p class="status" style="color:{status_color}">Final Status: {final_status}</p>
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


async def run(url="https://the-internet.herokuapp.com/login", goal=DEFAULT_goal, max_steps=8, suite_dir=None, token_budget=None, email=None, password=None, mode="qa"):
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
                        "text": _build_prompt(goal, step, max_steps, email, password, mode, url=url)
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

            if token_budget and tokens_used >= token_budget:
                print(f"💰 Token budget exceeded ({tokens_used:,}/{token_budget:,}) after API call, stopping test")
                report.append({
                    "step": step + 1,
                    "screenshot": screenshot_path,
                    "observation": "Token budget exceeded",
                    "action": "budget_stop",
                    "target": None,
                    "reasoning": f"Used {tokens_used:,} of {token_budget:,} token budget",
                    "pass_fail": "fail",
                    "verdict": f"Test stopped — token budget of {token_budget:,} exceeded"
                })
                break

            conversation.append({
                "role": "assistant",
                "content": raw
            })

            try:
                clean = raw.replace("```json", "").replace("```", "").strip()
                decision = json.loads(clean)

                entry = {
                    "step": step + 1,
                    "screenshot": screenshot_path,
                    "observation": decision["observation"],
                    "action": decision["action"],
                    "target": decision.get("target"),
                    "pass_fail": decision.get("pass_fail", "in_progress"),
                    "verdict": decision.get("verdict", ""),
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens
                }

                if mode == "ux":
                    entry["cta_clarity"] = decision.get("cta_clarity")
                    entry["copy_quality"] = decision.get("copy_quality")
                    entry["flow_smoothness"] = decision.get("flow_smoothness")
                    entry["first_impression"] = decision.get("first_impression", "")
                    entry["friction_points"] = decision.get("friction_points", [])
                    entry["recommendations"] = decision.get("recommendations", [])
                    entry["confidence"] = decision.get("confidence", "")
                else:
                    entry["reasoning"] = decision.get("reasoning", "")

                report.append(entry)

                if decision["action"] == "done":
                    print(f"\n✅ Agent complete — {decision.get('pass_fail', '').upper()}: {decision.get('verdict', '')}")
                    break
                elif decision["action"] == "click":
                    try:
                        await asyncio.wait_for(page.click(_sanitize_selector(decision["target"])), timeout=10)
                    except asyncio.TimeoutError:
                        target = decision["target"]
                        print(f"⚠️  click target not found: {target!r} — skipping and continuing")
                        conversation.append({
                            "role": "user",
                            "content": [{"type": "text", "text": f"The element '{target}' was not found on the page or did not respond within 10 seconds. Please try a different selector or action."}]
                        })
                        continue
                elif decision["action"] == "navigate":
                    target = decision["target"]
                    if target and not target.startswith("http://") and not target.startswith("https://"):
                        print(f"⚠️  navigate action received a selector instead of a URL — converting to click: {target!r}")
                        try:
                            await asyncio.wait_for(page.click(_sanitize_selector(target)), timeout=10)
                        except asyncio.TimeoutError:
                            print(f"⚠️  click target not found: {target!r} — skipping and continuing")
                            conversation.append({
                                "role": "user",
                                "content": [{"type": "text", "text": f"The element '{target}' was not found on the page or did not respond within 10 seconds. Please try a different selector or action."}]
                            })
                            continue
                    else:
                        await asyncio.wait_for(page.goto(target), timeout=15)
                elif decision["action"] == "type":
                    await asyncio.wait_for(page.fill(_sanitize_selector(decision["target"]), decision["value"]), timeout=10)

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

        # Below-the-fold analysis (UX mode only)
        below_fold = None
        if mode == "ux":
            try:
                below_fold = await _run_below_fold_analysis(page, run_dir, url)
                if below_fold:
                    bf_path = f"{run_dir}/below_fold.json"
                    with open(bf_path, "w", encoding="utf-8") as f:
                        json.dump(below_fold, f, indent=2)
                    print(f"📄 Below-fold analysis saved: {bf_path}")
            except Exception as e:
                print(f"⚠️  Below-fold analysis failed: {e}")

        # Save JSON report
        report_path = f"{run_dir}/report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\n📄 JSON report saved: {report_path}")

        # Build HTML report
        html = _build_html_report(report, goal, run_id, run_label, mode, below_fold=below_fold)
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
