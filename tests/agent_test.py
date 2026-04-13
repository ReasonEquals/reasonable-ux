import asyncio
import os
import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import anthropic
from anthropic import Anthropic
from dotenv import load_dotenv
import base64
import json
from datetime import datetime
from urllib.parse import urlparse
from PIL import Image

load_dotenv(override=True)
client = Anthropic()


class LLMAdapter:
    """Normalises API calls across providers (anthropic, openai, google)."""

    def __init__(self, provider: str):
        self._provider = provider
        if provider == "anthropic":
            self._client = anthropic.AsyncAnthropic()
        elif provider == "openai":
            import openai as _openai
            self._client = _openai.AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        elif provider == "google":
            import google.generativeai as genai
            genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
            self._genai = genai
        else:
            raise ValueError(f"Unknown provider: {provider!r}")

    async def complete(self, messages: list, model: str, max_tokens: int, tools: list = None) -> tuple:
        """Routes to the appropriate provider. Returns (response_text, input_tokens, output_tokens, raw_content).
        raw_content is the full content block list when advisor tools are active (needed for
        multi-turn conversation history), None otherwise."""
        if self._provider == "anthropic":
            return await self._complete_anthropic(messages, model, max_tokens, tools=tools)
        elif self._provider == "openai":
            text, in_tok, out_tok = await self._complete_openai(messages, model, max_tokens)
            return (text, in_tok, out_tok, None)
        elif self._provider == "google":
            text, in_tok, out_tok = await self._complete_google(messages, model, max_tokens)
            return (text, in_tok, out_tok, None)

    async def _complete_anthropic(self, messages, model, max_tokens, tools=None):
        kwargs = dict(model=model, max_tokens=max_tokens, messages=messages)
        if tools:
            kwargs["tools"] = tools
            kwargs["betas"] = ["advisor-tool-2026-03-01"]
            response = await self._client.beta.messages.create(**kwargs)
        else:
            response = await self._client.messages.create(**kwargs)
        text = next((b.text for b in reversed(response.content) if hasattr(b, "text")), "")
        raw_content = response.content if tools else None
        return (text, response.usage.input_tokens, response.usage.output_tokens, raw_content)

    @staticmethod
    def _anthropic_to_openai_messages(messages) -> list:
        """Translates Anthropic message format to OpenAI format."""
        oai_messages = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if isinstance(content, str):
                oai_messages.append({"role": role, "content": content})
            elif isinstance(content, list):
                oai_content = []
                for block in content:
                    if block.get("type") == "image":
                        src = block["source"]
                        oai_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}
                        })
                    else:
                        oai_content.append(block)
                oai_messages.append({"role": role, "content": oai_content})
            else:
                oai_messages.append({"role": role, "content": content})
        return oai_messages

    async def _complete_openai(self, messages, model, max_tokens):
        oai_messages = self._anthropic_to_openai_messages(messages)
        response = await self._client.chat.completions.create(
            model=model, max_tokens=max_tokens, messages=oai_messages
        )
        return (
            response.choices[0].message.content,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )

    @staticmethod
    def _anthropic_to_google_contents(messages) -> list:
        """Translates Anthropic message format to Google generativeai format."""
        contents = []
        for msg in messages:
            role = "model" if msg["role"] == "assistant" else "user"
            content = msg["content"]
            if isinstance(content, str):
                parts = [content]
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if block.get("type") == "image":
                        src = block["source"]
                        parts.append({
                            "mime_type": src["media_type"],
                            "data": base64.b64decode(src["data"]),
                        })
                    else:
                        parts.append(block.get("text", ""))
            else:
                parts = [str(content)]
            contents.append({"role": role, "parts": parts})
        return contents

    async def _complete_google(self, messages, model, max_tokens):
        contents = self._anthropic_to_google_contents(messages)
        gen_model = self._genai.GenerativeModel(model)
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: gen_model.generate_content(
                contents,
                generation_config={"max_output_tokens": max_tokens},
            )
        )
        in_tok = response.usage_metadata.prompt_token_count or 0
        out_tok = response.usage_metadata.candidates_token_count or 0
        return (response.text, in_tok, out_tok)


async def screenshot_as_base64(page):
    screenshot = await page.screenshot(type="jpeg", quality=40)
    return base64.b64encode(screenshot).decode("utf-8")

DEFAULT_goal = None


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


async def _click_nav_by_label(page, label: str) -> bool:
    """Find the first visible <a> whose text contains `label` (case-insensitive). Return True on success."""
    label = label.strip()
    try:
        locator = page.get_by_role("link", name=label, exact=False).first
        await locator.click(timeout=5000)
        return True
    except Exception:
        pass
    try:
        escaped = label.replace('"', '\\"')
        locator = page.locator(f'a:has-text("{escaped}")').first
        await locator.click(timeout=5000)
        return True
    except Exception:
        return False


def _infer_goal_from_url(url: str, mode: str) -> str:
    """Infer an appropriate test goal from the URL path segment and mode."""
    path = urlparse(url).path.rstrip("/")
    segment = path.split("/")[-1].lower() if path else ""

    goals_ux = {
        "pricing":  "Evaluate whether pricing information is clear, accessible, and compelling for the target buyer.",
        "faq":      "Evaluate clarity and findability of FAQ content and whether it addresses likely buyer concerns.",
        "features": "Evaluate whether the features page clearly communicates value to the target buyer and supports conversion.",
        "about":    "Evaluate whether the about page establishes credibility and trust for a professional audience.",
        "contact":  "Evaluate whether the contact page makes it easy to reach out and sets clear expectations.",
        "login":    "Evaluate the login page UX: clarity, ease of use, and friction in the sign-in flow.",
        "signup":   "Evaluate the signup flow for friction points, clarity of value, and ease of completion.",
        "register": "Evaluate the registration flow for friction points, clarity of value, and ease of completion.",
        "terms":    "Evaluate whether the terms page is readable and appropriately reassuring for a professional audience.",
        "privacy":  "Evaluate whether the privacy policy is readable and appropriately reassuring for a professional audience.",
        "blog":     "Evaluate the blog page for content quality, navigation clarity, and whether it builds trust with the target buyer.",
        "demo":     "Evaluate whether the demo page clearly communicates value and makes it easy to request or start a demo.",
    }
    goals_qa = {
        "pricing":  "Verify the pricing page renders correctly and all pricing tiers, CTAs, and interactive elements are functional.",
        "faq":      "Verify the FAQ page renders correctly and all expandable sections, links, and navigation elements function.",
        "features": "Verify the features page renders correctly and all interactive elements, images, and links function.",
        "about":    "Verify the about page renders correctly and all links, images, and media load without errors.",
        "contact":  "Verify the contact form renders correctly and all fields, validation, and submit controls function.",
        "login":    "Test the login form with valid and invalid credentials and verify error handling.",
        "signup":   "Verify the signup form renders correctly and all required fields, validation, and submit controls function.",
        "register": "Verify the registration form renders correctly and all required fields, validation, and submit controls function.",
        "terms":    "Verify the terms page renders correctly and all content loads without errors.",
        "privacy":  "Verify the privacy policy page renders correctly and all content loads without errors.",
        "blog":     "Verify the blog page renders correctly and all article links, pagination, and navigation function.",
        "demo":     "Verify the demo page renders correctly and all CTAs, form elements, and interactive components function.",
    }

    if mode == "ux":
        return goals_ux.get(segment) or "Evaluate this page for clarity, value proposition, CTA effectiveness, and friction in the user journey."
    else:
        return goals_qa.get(segment) or "Verify this page renders correctly and all key interactive elements are functional."


def _make_run_dir(url: str, run_type: str) -> str:
    """Construct and create runs/{domain}/{YYYY-MM-DD_HHMM}_{run_type}/."""
    hostname = urlparse(url).hostname or url
    if hostname.startswith("www."):
        hostname = hostname[4:]
    domain = hostname.replace(".", "_").replace("-", "_")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    run_dir = f"runs/{domain}/{timestamp}_{run_type}"
    os.makedirs(run_dir, exist_ok=True)
    return run_dir


def _build_prompt(goal, step, max_steps, email, password, mode, url=None, persona=None):
    creds_block = ""
    if email or password:
        creds_block = f"\nIf you encounter a login or signup form, use these credentials:\n"
        if email:
            creds_block += f"  Email/Username: {email}\n"
        if password:
            creds_block += f"  Password: {password}\n"
        creds_block += "  Login tip: after entering password, use selector 'button[type=\"submit\"]' to click Sign in (not the bare 'button' selector, which may hit a Change/Back button instead).\n"

    url_block = f"\nYou are evaluating {url}. Never navigate to a different domain — if you find yourself on a different domain, use navigate to return to {url}.\n" if url else ""

    if mode == "ux":
        if persona is None:
            persona_block = """Before evaluating, infer a plausible evaluator persona for this specific site based on the URL, page title, and above-the-fold content visible in the screenshot. The persona must represent a realistic buyer or user for this product — not a deliberately mismatched evaluator. State the persona in your observation AND include it as a top-level 'persona' field in your JSON response (e.g. 'mid-market SaaS buyer comparing project management tools'). Frame your friction_points and recommendations from that persona's perspective."""
            persona_schema_field = '\n    "persona": "one short sentence naming the persona you inferred for this site",'
        else:
            persona_block = f"You are evaluating this site as: {persona}. Your friction_points and recommendations must reflect that perspective."
            persona_schema_field = ""

        return f"""You are a UX evaluator. Your goal is: {goal}
{creds_block}{url_block}
{persona_block}

Current step: {step + 1}

Navigate the page and evaluate the user experience. Respond in JSON with exactly this shape:
{{
    "observation": "what you see on the page",
    "action": "click | type | navigate | done — navigate requires a full URL starting with http:// or https://; to follow a link use click with its CSS selector instead",
    "target": "simple CSS selector — prefer id over class over tag (e.g. '#username', 'button[type=submit]', 'input[name=password]') — avoid generic selectors like '.button' or bare 'a' — no :contains() — or URL or null. For clicks on main navigation links, use the format 'nav:<Visible Label>' instead of a CSS selector (e.g. 'nav:Pricing', 'nav:Features'). Use CSS selectors for everything else — form fields, buttons, CTAs, in-page elements.",
    "value": "text to type or null",{persona_schema_field}
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


def _build_below_fold_prompt(persona: str) -> str:
    return f"""You are evaluating this page as: {persona}. The agent that evaluated this page could only see above the fold. Look at the full page and identify anything below the fold that is relevant to the evaluation from this persona's perspective — additional value propositions, pricing signals, social proof, trust indicators, feature explanations, or UX issues. Return a JSON object with two fields: below_fold_findings (array of strings) and below_fold_score_adjustments (object where each key is a dimension name and each value is {{"adjusted_score": <integer 1-5>, "reason": "one sentence explanation"}}). Only include adjustments for these three dimensions if applicable: cta_clarity, copy_quality, flow_smoothness."""


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


async def _run_below_fold_analysis(page, run_dir, url, persona, advisor=False):
    print("\n🔍 Running below-the-fold analysis...")
    await page.goto(url, wait_until="networkidle")
    fp_path = f"{run_dir}/full_page.jpeg"
    await page.screenshot(path=fp_path, full_page=True, type="jpeg", quality=60)
    print(f"📸 Full-page screenshot saved: {fp_path}")

    MAX_HEIGHT = 7500
    with Image.open(fp_path) as img:
        if img.height > MAX_HEIGHT:
            cropped = img.crop((0, 0, img.width, MAX_HEIGHT))
            cropped.save(fp_path, "JPEG", quality=60)
            print(f"📐 Cropped full-page screenshot {img.width}x{img.height} → {img.width}x{MAX_HEIGHT}")

    with open(fp_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    kwargs = dict(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded}
                },
                {"type": "text", "text": _build_below_fold_prompt(persona)}
            ]
        }]
    )
    if advisor:
        kwargs["tools"] = [{"type": "advisor_20260301", "name": "advisor", "model": "claude-opus-4-6", "max_uses": 1}]
        kwargs["betas"] = ["advisor-tool-2026-03-01"]
        response = client.beta.messages.create(**kwargs)
    else:
        response = client.messages.create(**kwargs)

    raw = next((b.text for b in reversed(response.content) if hasattr(b, "text")), "")
    print(f"💰 Below-fold analysis tokens: {response.usage.input_tokens + response.usage.output_tokens:,}")
    try:
        clean = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except Exception as e:
        print(f"⚠️  Could not parse below-fold analysis: {e}")
        print(f"Raw response:\n{raw}")
        return None


async def scout_page(url: str, storage_state: str = None) -> dict:
    """Fetch page HTML, extract key text elements, ask claude-sonnet-4-6 to score interest 1-5.
    Returns {interest_score, reason, extracted_text, input_tokens, output_tokens}.
    """
    cookie_jar = requests.cookies.RequestsCookieJar()
    if storage_state:
        try:
            with open(storage_state, "r", encoding="utf-8") as f:
                state = json.load(f)
            for c in state.get("cookies", []):
                cookie_jar.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
        except Exception as e:
            print(f"⚠️  Could not load cookies from storage state: {e}")
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "reasonable-ux-scout/1.0"}, cookies=cookie_jar)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        return {
            "interest_score": 1,
            "reason": f"Page fetch failed: {e}",
            "extracted_text": "",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    meta_tag = soup.find("meta", attrs={"name": "description"})
    meta_desc = meta_tag.get("content", "").strip() if meta_tag else ""
    h1_tags = [h.get_text(strip=True) for h in soup.find_all("h1")]
    h1 = h1_tags[0] if h1_tags else ""

    nav_links = []
    nav = soup.find("nav") or soup.find("header")
    if nav:
        nav_links = [a.get_text(strip=True) for a in nav.find_all("a") if a.get_text(strip=True)][:10]

    cta_texts = []
    for el in soup.find_all(["button", "a"]):
        cls = " ".join(el.get("class", []))
        text = el.get_text(strip=True)
        if text and any(kw in cls.lower() for kw in ["cta", "btn", "button", "primary", "signup", "sign-up", "get-started", "start", "trial", "free"]):
            cta_texts.append(text)
    if not cta_texts:
        cta_texts = [b.get_text(strip=True) for b in soup.find_all("button") if b.get_text(strip=True)][:5]
    cta_texts = cta_texts[:5]

    extracted_text = (
        f"Title: {title}\n"
        f"Meta description: {meta_desc}\n"
        f"H1: {h1}\n"
        f"Nav links: {', '.join(nav_links)}\n"
        f"Primary CTA buttons: {', '.join(cta_texts)}"
    )

    scout_prompt = f"""You are evaluating whether a web page is worth a full UX analysis. Based on the extracted page elements below, rate the page's interest for UX evaluation on a scale of 1-5.

Page elements:
{extracted_text}

Interest score rubric:
- 5: Rich content page with clear UX elements worth evaluating (pricing, features, signup flow, product demo)
- 4: Substantial content with some evaluatable UX (about, contact, blog landing)
- 3: Moderate content, worth a look (generic landing page, thin but real content)
- 2: Minimal content or mostly boilerplate (simple terms, cookie policy, error page)
- 1: Empty, redirect, or no meaningful content

Respond with a JSON object with exactly these two fields:
{{
    "interest_score": <integer 1-5>,
    "reason": "<one sentence explaining the score>"
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        messages=[{"role": "user", "content": scout_prompt}]
    )

    raw = response.content[0].text
    try:
        clean = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
        return {
            "interest_score": max(1, min(5, int(result.get("interest_score", 3)))),
            "reason": result.get("reason", ""),
            "extracted_text": extracted_text,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
    except Exception as e:
        print(f"⚠️  Could not parse scout response: {e}\nRaw: {raw}")
        return {
            "interest_score": 3,
            "reason": "Could not parse scout response, defaulting to threshold",
            "extracted_text": extracted_text,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }


def _build_html_report(report, goal, run_id, run_label, mode, below_fold=None):
    final_status = report[-1].get("pass_fail", "unknown").upper() if report else "UNKNOWN"
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
            if entry.get("action") == "scout_skip":
                score = entry.get("interest_score", "?")
                rows += f"""
            <tr>
                <td>{entry['step']}</td>
                <td style="color:#888;font-size:11px">scout skip</td>
                <td style="white-space:pre-wrap;font-size:12px;color:#aaa">{entry['observation']}</td>
                <td style="color:#888">scout_skip</td>
                <td>—</td><td>—</td><td>—</td>
                <td>—</td>
                <td>—</td>
                <td>—</td>
                <td style="color:#888;font-weight:bold">SKIP</td>
                <td>{entry.get('verdict','')}</td>
            </tr>"""
                continue

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
        if entry.get("action") == "scout_skip":
            rows += f"""
        <tr>
            <td>{entry['step']}</td>
            <td style="color:#888;font-size:11px">scout skip</td>
            <td style="white-space:pre-wrap;font-size:12px;color:#aaa">{entry['observation']}</td>
            <td>scout_skip</td>
            <td style="color:#888">{entry.get('reasoning','')}</td>
            <td style="color:#888;font-weight:bold">SKIP</td>
            <td>{entry.get('verdict','')}</td>
        </tr>"""
            continue

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


async def run(url=None, goal=None, max_steps=8, suite_dir=None, token_budget=None, email=None, password=None, mode="qa", scout=False, scout_threshold=3, provider: str = "anthropic", model: str = "claude-opus-4-5", storage_state=None, advisor: bool = False):
    if not url:
        raise ValueError("run() requires a url — no default fallback.")
    if advisor and provider == "anthropic" and model == "claude-opus-4-5":
        model = "claude-sonnet-4-6"
        print("🧠 Advisor mode: switching executor to claude-sonnet-4-6 (Opus 4.5 does not support advisor tool)")
    # ── Scout phase (optional) ────────────────────────────────────────────────
    scout_input_tokens = 0
    scout_output_tokens = 0

    if scout:
        scout_result = await scout_page(url, storage_state=storage_state)
        score = scout_result["interest_score"]
        reason = scout_result["reason"]
        extracted = scout_result["extracted_text"]
        scout_input_tokens = scout_result["input_tokens"]
        scout_output_tokens = scout_result["output_tokens"]
        print(f"🔍 Scout: {url} scored {score}/5 — {reason}")

        if score < scout_threshold:
            if not goal:
                goal = _infer_goal_from_url(url, mode)
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_label = "_".join(goal.split()[0:3]).lower().strip(".,!?")
            if suite_dir:
                _path = urlparse(url).path.strip("/")
                page_segment = _path.replace("/", "_") if _path else "homepage"
                run_dir = f"{suite_dir}/{page_segment}"
            else:
                run_dir = _make_run_dir(url, "single_page")
            os.makedirs(run_dir, exist_ok=True)

            scout_entry = {
                "step": 1,
                "screenshot": "",
                "observation": extracted,
                "action": "scout_skip",
                "target": None,
                "pass_fail": "skip",
                "verdict": f"Scout score {score}/5 — {reason}. Below threshold, skipped full evaluation.",
                "interest_score": score,
                "input_tokens": scout_input_tokens,
                "output_tokens": scout_output_tokens,
            }
            if mode == "ux":
                scout_entry["cta_clarity"] = None
                scout_entry["copy_quality"] = None
                scout_entry["flow_smoothness"] = None
                scout_entry["first_impression"] = ""
                scout_entry["friction_points"] = []
                scout_entry["recommendations"] = []
                scout_entry["confidence"] = "low"
            else:
                scout_entry["reasoning"] = f"Scout score {score}/5 below threshold {scout_threshold}. No full evaluation performed."

            report = [scout_entry]
            report_path = f"{run_dir}/report.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            print(f"\n📄 JSON report saved: {report_path}")

            html = _build_html_report(report, goal, run_id, run_label, mode)
            html_path = f"{run_dir}/report.html"
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"🌐 HTML report saved: {html_path}")

            return {
                "input": scout_input_tokens,
                "output": scout_output_tokens,
                "total": scout_input_tokens + scout_output_tokens,
                "scout_skipped": True,
                "scout_input_tokens": scout_input_tokens,
                "scout_output_tokens": scout_output_tokens,
                "console_logs": [],
                "network_events": [],
            }

    # ── Full vision eval ──────────────────────────────────────────────────────
    adapter = LLMAdapter(provider)
    async with async_playwright() as p:
        headless = os.environ.get("CI", "false").lower() == "true"
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(storage_state=storage_state)
        page = await context.new_page()

        await page.goto(url, wait_until="domcontentloaded")

        console_logs = []
        page.on("console", lambda msg: console_logs.append({
            "type": msg.type,
            "text": msg.text
        }))

        network_events = []
        _pending_requests = {}

        def _on_request(request):
            _pending_requests[request.url] = asyncio.get_event_loop().time()

        def _on_response(response):
            start = _pending_requests.pop(response.url, None)
            duration_ms = round((asyncio.get_event_loop().time() - start) * 1000) if start else None
            if response.status >= 400 or (duration_ms is not None and duration_ms > 2000):
                network_events.append({
                    "url": response.url,
                    "status": response.status,
                    "duration_ms": duration_ms
                })

        page.on("request", _on_request)
        page.on("response", _on_response)

        if not goal:
            goal = _infer_goal_from_url(url, mode)

        conversation = []
        report = []
        persona = None
        tokens_used = 0
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_label = "_".join(goal.split()[0:3]).lower().strip(".,!?")
        if suite_dir:
            _path = urlparse(url).path.strip("/")
            page_segment = _path.replace("/", "_") if _path else "homepage"
            run_dir = f"{suite_dir}/{page_segment}"
        else:
            run_dir = _make_run_dir(url, "single_page")
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
                        "text": _build_prompt(goal, step, max_steps, email, password, mode, url=url, persona=persona)
                    }
                ]
            })

            advisor_tools = None
            if advisor and provider == "anthropic":
                advisor_tools = [{"type": "advisor_20260301", "name": "advisor", "model": "claude-opus-4-6", "max_uses": 1}]
            step_budget = 2048 if advisor else 1024
            raw, _in_tok, _out_tok, _raw_content = await adapter.complete(conversation, model, step_budget, tools=advisor_tools)
            tokens_used += _in_tok + _out_tok
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
                "content": _raw_content if _raw_content else raw
            })

            try:
                clean = raw.replace("```json", "").replace("```", "").strip()
                # Extract JSON object even if surrounded by preamble text (common with advisor)
                brace_start = clean.find("{")
                brace_end = clean.rfind("}")
                if brace_start != -1 and brace_end != -1:
                    clean = clean[brace_start:brace_end + 1]
                decision = json.loads(clean)

                if mode == "ux" and persona is None:
                    persona = decision.get("persona") or "a plausible buyer or user for this product"

                entry = {
                    "step": step + 1,
                    "screenshot": screenshot_path,
                    "observation": decision["observation"],
                    "action": decision["action"],
                    "target": decision.get("target"),
                    "pass_fail": decision.get("pass_fail", "in_progress"),
                    "verdict": decision.get("verdict", ""),
                    "input_tokens": _in_tok,
                    "output_tokens": _out_tok
                }

                if mode == "ux":
                    entry["cta_clarity"] = decision.get("cta_clarity")
                    entry["copy_quality"] = decision.get("copy_quality")
                    entry["flow_smoothness"] = decision.get("flow_smoothness")
                    entry["first_impression"] = decision.get("first_impression", "")
                    entry["friction_points"] = decision.get("friction_points", [])
                    entry["recommendations"] = decision.get("recommendations", [])
                    entry["confidence"] = decision.get("confidence", "")
                    if step == 0:
                        entry["persona"] = persona
                else:
                    entry["reasoning"] = decision.get("reasoning", "")

                report.append(entry)

                if decision["action"] == "done":
                    print(f"\n✅ Agent complete — {decision.get('pass_fail', '').upper()}: {decision.get('verdict', '')}")
                    break
                elif decision["action"] == "click":
                    target = decision["target"]
                    if isinstance(target, str) and target.startswith("nav:"):
                        label = target[len("nav:"):]
                        ok = await _click_nav_by_label(page, label)
                        if not ok:
                            print(f"⚠️  nav link not found for label: {label!r} — skipping and continuing")
                            conversation.append({
                                "role": "user",
                                "content": [{"type": "text", "text": f"No visible navigation link matching '{label}' was found on the page. Please try a different label or action."}]
                            })
                            continue
                    else:
                        try:
                            await asyncio.wait_for(page.click(_sanitize_selector(target)), timeout=10)
                        except asyncio.TimeoutError:
                            print(f"⚠️  click target not found: {target!r} — skipping and continuing")
                            conversation.append({
                                "role": "user",
                                "content": [{"type": "text", "text": f"The element '{target}' was not found on the page or did not respond within 10 seconds. Please try a different selector or action."}]
                            })
                            continue
                elif decision["action"] == "navigate":
                    target = decision["target"]
                    if isinstance(target, str) and target.startswith("nav:"):
                        label = target[len("nav:"):]
                        print(f"⚠️  navigate action received a nav label instead of a URL — converting to nav click: {label!r}")
                        ok = await _click_nav_by_label(page, label)
                        if not ok:
                            print(f"⚠️  nav link not found for label: {label!r} — skipping and continuing")
                            conversation.append({
                                "role": "user",
                                "content": [{"type": "text", "text": f"No visible navigation link matching '{label}' was found on the page. Please try a different label or action."}]
                            })
                            continue
                    elif target and not target.startswith("http://") and not target.startswith("https://"):
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
                        await asyncio.wait_for(page.goto(target, wait_until="domcontentloaded"), timeout=15)
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

            # After click/navigate give the page time to settle before next screenshot
            if decision["action"] in ("click", "navigate"):
                try:
                    await asyncio.wait_for(page.wait_for_load_state("domcontentloaded"), timeout=5.0)
                except Exception:
                    pass
            await asyncio.sleep(1.5)

        # Below-the-fold analysis (UX mode only)
        below_fold = None
        if mode == "ux":
            try:
                below_fold = await _run_below_fold_analysis(page, run_dir, url, persona or "a plausible buyer or user for this product", advisor=advisor)
                if below_fold:
                    bf_path = f"{run_dir}/below_fold.json"
                    with open(bf_path, "w", encoding="utf-8") as f:
                        json.dump(below_fold, f, indent=2)
                    print(f"📄 Below-fold analysis saved: {bf_path}")
            except Exception as e:
                print(f"⚠️  Below-fold analysis failed: {e}")

        # Save console and network logs
        with open(f"{run_dir}/console.json", "w") as f:
            json.dump(console_logs, f, indent=2)
        with open(f"{run_dir}/network.json", "w") as f:
            json.dump(network_events, f, indent=2)

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
        await asyncio.wait_for(context.close(), timeout=10)

        total_input = sum(r.get("input_tokens", 0) for r in report if "input_tokens" in r)
        total_output = sum(r.get("output_tokens", 0) for r in report if "output_tokens" in r)
        return {
            "input": total_input + scout_input_tokens,
            "output": total_output + scout_output_tokens,
            "total": total_input + total_output + scout_input_tokens + scout_output_tokens,
            "scout_skipped": False,
            "scout_input_tokens": scout_input_tokens,
            "scout_output_tokens": scout_output_tokens,
            "console_logs": console_logs,
            "network_events": network_events,
        }

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Dev entry point for agent_test.run(). For the full CLI use run.py.")
    parser.add_argument("--url", type=str, required=True, help="Target URL (required)")
    parser.add_argument("--goal", type=str, default=None)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--mode", type=str, default="qa", choices=["qa", "ux"])
    parser.add_argument("--email", type=str, default=None)
    parser.add_argument("--password", type=str, default=None)
    parser.add_argument("--token-budget", type=int, default=None)
    parser.add_argument("--provider", type=str, default="anthropic", choices=["anthropic", "openai", "google"])
    parser.add_argument("--model", type=str, default="claude-opus-4-5")
    parser.add_argument("--advisor", action="store_true", help="Enable Opus advisor tool for higher-quality judgment (Anthropic only)")
    args = parser.parse_args()
    asyncio.run(run(
        url=args.url,
        goal=args.goal,
        max_steps=args.steps,
        mode=args.mode,
        email=args.email,
        password=args.password,
        token_budget=args.token_budget,
        provider=args.provider,
        model=args.model,
        advisor=args.advisor,
    ))
