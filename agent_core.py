import asyncio
import base64
import html
import json
import os
import sys
from contextlib import nullcontext
from datetime import datetime
from urllib.parse import urlparse

import anthropic
import litellm
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from PIL import Image
from playwright.async_api import async_playwright

# Allow sibling-module imports when invoked as `python agent_core.py`
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from _sanitize_extracted import (  # noqa: E402
    sanitize_field,
    sanitize_persona,
    sanitize_string_list,
)

load_dotenv(override=True)

_LANGFUSE_TRACING_ENABLED = bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))
_langfuse_observe = None
_langfuse_propagate = None

if _LANGFUSE_TRACING_ENABLED:
    litellm.callbacks = ["langfuse_otel"]
    try:
        from langfuse import observe as _langfuse_observe
        from langfuse import propagate_attributes as _langfuse_propagate
    except ImportError:
        pass


def _lf_observe(f):
    """Wrap with langfuse @observe when tracing is on; identity otherwise.

    capture_input/output disabled — these functions receive Playwright Page
    objects and base64-encoded screenshots in messages; auto-serializing them
    burns 50+ GB and hangs. Functions manually call _lf_update_generation()
    after their LLM call to log only the safe text fields (prompt, response,
    model, tokens) — see invariants in CLAUDE.md section 5.
    """
    if _langfuse_observe is not None:
        return _langfuse_observe(as_type="generation", capture_input=False, capture_output=False)(f)
    return f


def _lf_update_generation(*, input=None, output=None, model=None, input_tokens=None, output_tokens=None, cost_usd=None):
    """Manually populate the current Langfuse generation observation with safe text fields.

    No-op when tracing is disabled or no observation is active. Use after an LLM call
    inside a function decorated with @_lf_observe — pass only text/scalar values, never
    Playwright objects or base64 image blobs.
    """
    if not _LANGFUSE_TRACING_ENABLED:
        return
    try:
        from langfuse import get_client
        usage = None
        if input_tokens is not None or output_tokens is not None:
            usage = {"input": input_tokens or 0, "output": output_tokens or 0}
        cost_details = {"total": cost_usd} if cost_usd is not None else None
        get_client().update_current_generation(
            input=input,
            output=output,
            model=model,
            usage_details=usage,
            cost_details=cost_details,
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Langfuse update_current_generation failed: {e}", file=sys.stderr)


def _calc_cost_usd(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    if input_tokens is None or output_tokens is None:
        return None
    try:
        inp, out = litellm.cost_per_token(
            model=model,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
        )
        return inp + out
    except Exception:  # noqa: BLE001
        return None


async def _flush_langfuse_spans():
    """Drain LiteLLM's logging queue and flush OTel spans while the event loop is still alive.

    Must run INSIDE the asyncio loop — LiteLLM's atexit path fails on Python 3.14 with
    'cannot schedule new futures after interpreter shutdown' when the OTel span creation
    is queued behind the main script's exit.
    """
    if not _LANGFUSE_TRACING_ENABLED:
        return
    try:
        from litellm.litellm_core_utils.logging_worker import GLOBAL_LOGGING_WORKER

        await GLOBAL_LOGGING_WORKER.flush()
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ LiteLLM logging flush failed: {e}", file=sys.stderr)
    try:
        from opentelemetry import trace as _otel_trace

        provider = _otel_trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=5000)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Langfuse OTel span flush failed: {e}", file=sys.stderr)
    try:
        from langfuse import get_client as _lf_get_client
        _lf_get_client().flush()
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Langfuse SDK flush failed: {e}", file=sys.stderr)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 reasonable-ux/0.1"
)


class TokenBudgetExceeded(Exception):
    def __init__(self, tokens_used: int, budget: int):
        self.tokens_used = tokens_used
        self.budget = budget
        super().__init__(f"Token budget exceeded: {tokens_used:,}/{budget:,}")


class LLMAdapter:
    """Normalises API calls across providers (anthropic, openai, google)."""

    def __init__(self, provider: str, token_budget: int = None):
        self._provider = provider
        self._token_budget = token_budget
        self._tokens_used = 0
        if provider == "anthropic":
            self._anthropic = anthropic.AsyncAnthropic()  # advisor-beta path only
        elif provider not in ("openai", "google"):
            raise ValueError(f"Unknown provider: {provider!r}")

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    def _litellm_model(self, model: str) -> str:
        if self._provider == "anthropic":
            return f"anthropic/{model}"
        elif self._provider == "openai":
            return model
        elif self._provider == "google":
            return f"gemini/{model}"

    async def complete(self, messages: list, model: str, max_tokens: int, tools: list = None, metadata: dict = None) -> tuple:
        """Routes to the appropriate provider via LiteLLM (or direct Anthropic for advisor-beta).
        Returns (response_text, input_tokens, output_tokens, raw_content)."""
        if self._provider == "anthropic" and tools:
            result = await self._complete_anthropic_advisor(messages, model, max_tokens, tools, metadata=metadata)
        else:
            oai_messages = self._anthropic_to_openai_messages(messages)
            response = await litellm.acompletion(
                model=self._litellm_model(model),
                messages=oai_messages,
                max_tokens=max_tokens,
                metadata=metadata,
            )
            text = response.choices[0].message.content or ""
            result = (text, response.usage.prompt_tokens, response.usage.completion_tokens, None)
        self._tokens_used += result[1] + result[2]
        if self._token_budget and self._tokens_used >= self._token_budget:
            raise TokenBudgetExceeded(self._tokens_used, self._token_budget)
        return result

    @_lf_observe
    async def _complete_anthropic_advisor(self, messages, model, max_tokens, tools, metadata=None):
        session_id = metadata.get("session_id") if metadata else None
        _ctx = _langfuse_propagate(session_id=session_id) if (_langfuse_propagate and session_id) else nullcontext()
        with _ctx:
            response = await self._anthropic.beta.messages.create(
            model=model, max_tokens=max_tokens, messages=messages,
            tools=tools, betas=["advisor-tool-2026-03-01"]
        )
        text = next((b.text for b in reversed(response.content) if hasattr(b, "text")), "")
        # Safe text-only summary of input messages (skip image blocks; they're already
        # captured upstream by per-step LiteLLM traces and would re-trigger the leak).
        safe_input = [
            {"role": m["role"],
             "text": " ".join(b.get("text", "") for b in m["content"] if isinstance(b, dict) and b.get("type") == "text")
             if isinstance(m["content"], list) else m["content"]}
            for m in messages
        ]
        _lf_update_generation(
            input=safe_input,
            output=text,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cost_usd=_calc_cost_usd(model, response.usage.input_tokens, response.usage.output_tokens),
        )
        return (text, response.usage.input_tokens, response.usage.output_tokens, response.content)

    @staticmethod
    def _anthropic_to_openai_messages(messages) -> list:
        """Normalises Anthropic-format messages to OpenAI format for LiteLLM."""
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
    except Exception:  # noqa: S110 — first locator attempt fails silently, falls through
        pass
    try:
        escaped = label.replace('"', '\\"')
        locator = page.locator(f'a:has-text("{escaped}")').first
        await locator.click(timeout=5000)
        return True
    except Exception:
        return False


def _infer_goal_from_url(url: str) -> str:
    """Infer an appropriate UX evaluation goal from the URL path segment."""
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

    return goals_ux.get(segment) or "Evaluate this page for clarity, value proposition, CTA effectiveness, and friction in the user journey."


def _make_run_dir(url: str, run_type: str) -> str:
    """Construct and create runs/{domain}/{YYYY-MM-DD_HHMM}_{run_type}/."""
    hostname = urlparse(url).hostname or url
    if hostname.startswith("www."):
        hostname = hostname[4:]
    domain = hostname.replace(".", "_").replace("-", "_")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = f"runs/{domain}/{timestamp}_{run_type}"
    os.makedirs(run_dir, exist_ok=True)
    return run_dir


def _build_prompt(goal, step, max_steps, email, password, url=None, persona=None):
    creds_block = ""
    if email or password:
        creds_block = "\nIf you encounter a login or signup form, use these credentials:\n"
        if email:
            creds_block += f"  Email/Username: {email}\n"
        if password:
            creds_block += f"  Password: {password}\n"
        creds_block += "  Login tip: after entering password, use selector 'button[type=\"submit\"]' to click Sign in (not the bare 'button' selector, which may hit a Change/Back button instead).\n"

    url_block = f"\nYou are evaluating {url}. Never navigate to a different domain — if you find yourself on a different domain, use navigate to return to {url}.\n" if url else ""

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

Friction is REQUIRED. Every real website has at least one friction point for any given persona — unclear CTAs, hidden pricing, cognitive load, competing messages, missing social proof, copy that assumes context. You MUST surface 1-3 concrete friction points on every step unless action="done" AND severity=0 (in which case verdict must justify the zero-friction claim). Empty friction_points on a non-`done` step is invalid. Keep every text field terse — one short sentence per note.

Navigate the page and evaluate the user experience. Respond in JSON with exactly this shape:
{{
    "observation": "one short sentence on what you see",
    "action": "click | type | navigate | done — navigate requires a full URL (http/https); to follow a link use click with its selector",
    "target": "simple CSS selector — prefer id > class > tag (e.g. '#username', 'button[type=submit]') — no :contains() — or URL or null. For main-nav links use 'nav:<Visible Label>' (e.g. 'nav:Pricing'). Use CSS selectors for everything else.",
    "value": "text to type or null",{persona_schema_field}
    "cta_clarity": {{"score": 1-5, "note": "one short sentence"}},
    "copy_quality": {{"score": 1-5, "note": "one short sentence"}},
    "flow_smoothness": {{"score": 1-5, "note": "one short sentence"}},
    "severity": "integer 0-4 — Nielsen severity for the worst issue. 0=no problem, 1=cosmetic, 2=minor, 3=major, 4=catastrophe. Be honest; don't pad.",
    "first_impression": "one short sentence",
    "friction_points": ["REQUIRED: 1-3 concrete, one-sentence friction points this persona would encounter on THIS page. Not generic UX maxims. Empty list is invalid unless action=done AND severity=0."],
    "recommendations": ["one concrete fix per friction point — one short sentence each. Same count as friction_points."],
    "confidence": "high | medium | low",
    "pass_fail": "pass | fail | in_progress",
    "verdict": "one short sentence"
}}

Score rubric: 1=very poor, 2=poor, 3=acceptable, 4=good, 5=excellent.
pass_fail should reflect overall UX quality: pass if average score >= 3, fail if < 3, in_progress while still navigating.

If your goal is complete, use action: done and give final scores and verdict.
If you are on step {max_steps}, you MUST use action: done — do not continue."""


def _build_below_fold_prompt(persona: str) -> str:
    return f"""You are evaluating this page as: {persona}. The agent that evaluated this page could only see above the fold. Look at the full page and identify anything below the fold that is relevant to the evaluation from this persona's perspective — additional value propositions, pricing signals, social proof, trust indicators, feature explanations, or UX issues. Return a JSON object with two fields: below_fold_findings (array of strings) and below_fold_score_adjustments (object where each key is a dimension name and each value is {{"adjusted_score": <integer 1-5>, "reason": "one sentence explanation"}}). Only include adjustments for these three dimensions if applicable: cta_clarity, copy_quality, flow_smoothness."""


def _build_below_fold_html(below_fold):
    if not below_fold:
        return ""
    findings = below_fold.get("below_fold_findings", [])
    adjustments = below_fold.get("below_fold_score_adjustments", {})
    findings_html = "".join(f'<li style="margin-bottom:6px">{html.escape(f)}</li>' for f in findings)
    adj_rows = ""
    dim_labels = {"cta_clarity": "CTA Clarity", "copy_quality": "Copy Quality", "flow_smoothness": "Flow Smoothness"}
    for dim, val in adjustments.items():
        label = dim_labels.get(dim, dim.replace("_", " ").title())
        score = (val.get("adjusted_score") or val.get("adjustment", "—")) if isinstance(val, dict) else val
        reason = val.get("reason", "") if isinstance(val, dict) else ""
        color = "#2ecc71" if isinstance(score, (int, float)) and score >= 4 else "#f39c12" if isinstance(score, (int, float)) and score >= 3 else "#e74c3c"
        adj_rows += f"""
        <tr>
            <td style="font-weight:bold">{html.escape(label)}</td>
            <td style="color:{color};font-weight:bold">{html.escape(str(score))}/5</td>
            <td>{html.escape(reason)}</td>
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


@_lf_observe
async def _run_below_fold_analysis(page, run_dir, url, persona, advisor=False, session_id=None):
    print("\n🔍 Running below-the-fold analysis...")
    try:
        await page.goto(url, wait_until="load", timeout=20000)
    except Exception:  # noqa: S110 — ad-heavy sites can stall past `load`; full_page screenshot will scroll and trigger lazy-load anyway
        pass
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

    tools = (
        [{"type": "advisor_20260301", "name": "advisor", "model": "claude-opus-4-6", "max_uses": 1}]
        if advisor
        else None
    )
    adapter = LLMAdapter("anthropic")
    raw, in_tok, out_tok, _ = await adapter.complete(
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded}
                },
                {"type": "text", "text": _build_below_fold_prompt(persona)}
            ]
        }],
        model="claude-sonnet-4-6",
        max_tokens=2048,
        tools=tools,
        metadata={"session_id": session_id or run_dir} if (session_id or run_dir) else None,
    )
    print(f"💰 Below-fold analysis tokens: {in_tok + out_tok:,}")
    _lf_update_generation(
        input=_build_below_fold_prompt(persona),
        output=raw,
        model="claude-sonnet-4-6",
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=_calc_cost_usd("claude-sonnet-4-6", in_tok, out_tok),
    )
    try:
        clean = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except Exception as e:
        print(f"⚠️  Could not parse below-fold analysis: {e}")
        print(f"Raw response:\n{raw}")
        return None


@_lf_observe
async def scout_page(url: str, storage_state: str = None, run_dir: str = None, session_id: str = None) -> dict:
    """Fetch page HTML, extract key text elements, ask claude-haiku-4-5-20251001 to score interest 1-5.
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

    extracted_text = sanitize_field(
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

    adapter = LLMAdapter("anthropic")
    raw, in_tok, out_tok, _ = await adapter.complete(
        messages=[{"role": "user", "content": scout_prompt}],
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        metadata={"session_id": session_id or run_dir} if (session_id or run_dir) else None,
    )

    _lf_update_generation(
        input=scout_prompt,
        output=raw,
        model="claude-haiku-4-5-20251001",
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=_calc_cost_usd("claude-haiku-4-5-20251001", in_tok, out_tok),
    )
    try:
        clean = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
        return {
            "interest_score": max(1, min(5, int(result.get("interest_score", 3)))),
            "reason": result.get("reason", ""),
            "extracted_text": extracted_text,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
        }
    except Exception as e:
        print(f"⚠️  Could not parse scout response: {e}\nRaw: {raw}")
        return {
            "interest_score": 3,
            "reason": "Could not parse scout response, defaulting to threshold",
            "extracted_text": extracted_text,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
        }


def _compute_summary(report):
    score_buckets = {"cta_clarity": [], "copy_quality": [], "flow_smoothness": []}
    friction_count = 0
    persona = None
    for entry in report:
        if entry.get("action") == "scout_skip":
            continue
        for field in score_buckets:
            obj = entry.get(field)
            if isinstance(obj, dict) and isinstance(obj.get("score"), (int, float)):
                score_buckets[field].append(obj["score"])
        friction_count += len(entry.get("friction_points", []))
        if not persona and entry.get("persona"):
            persona = entry["persona"]
    avgs = {k: round(sum(v) / len(v), 1) if v else None for k, v in score_buckets.items()}
    return persona, avgs, friction_count


def _build_html_report(report, goal, run_id, run_label, below_fold=None):
    final_status = report[-1].get("pass_fail", "unknown").upper() if report else "UNKNOWN"
    status_color = "#2ecc71" if final_status == "PASS" else "#e74c3c" if final_status == "FAIL" else "#f39c12"

    shared_style = """
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif; margin: 40px; background: #1a1a2e; color: #eee; }
        h1 { font-size: 28px; margin-bottom: 8px; color: #00d4ff; letter-spacing: -0.02em; }
        .status { font-size: 24px; font-weight: bold; margin: 16px 0; }
        .goal { background: #16213e; padding: 15px; border-left: 4px solid #00d4ff; margin: 20px 0; border-radius: 4px; }
        .meta { color: #888; font-size: 13px; margin: 8px 0; }
        table { width: 100%; border-collapse: collapse; background: #16213e; border-radius: 8px; overflow: hidden; margin-top: 20px; }
        th { background: #0f3460; color: #00d4ff; padding: 12px 16px; text-align: left; font-size: 13px; }
        td { padding: 12px 16px; border-bottom: 1px solid #0f3460; vertical-align: top; font-size: 13px; line-height: 1.5; }
        tr:last-child td { border-bottom: none; }
        tr:hover td { background: #0f3460; }
        img.step-shot { border-radius: 4px; border: 1px solid #0f3460; cursor: zoom-in; }
        .score { font-weight: bold; }
        .s5 { color: #2ecc71; } .s4 { color: #27ae60; } .s3 { color: #f39c12; }
        .s2 { color: #e67e22; } .s1 { color: #e74c3c; }
        .friction { color: #f39c12; font-size: 12px; }
        .summary { display: flex; gap: 16px; margin: 20px 0; }
        .scard { background: #16213e; padding: 16px 20px; border-radius: 8px; flex: 1; text-align: center; border: 1px solid #0f3460; }
        .scard .val { font-size: 28px; font-weight: 700; }
        .scard .lbl { font-size: 11px; color: #888; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.08em; }
        .persona-block { background: #16213e; border-left: 4px solid #00d4ff; padding: 14px 16px; margin: 0 0 20px; border-radius: 0 4px 4px 0; font-size: 13px; }
        .persona-label { font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 4px; }
    """

    persona, avgs, friction_count = _compute_summary(report)

    def _avg_card(label, v):
        if v is None:
            return f'<div class="scard"><div class="val" style="color:#888">—</div><div class="lbl">{label}</div></div>'
        cls = f"s{min(max(round(v), 1), 5)}"
        return f'<div class="scard"><div class="val {cls}">{v}/5</div><div class="lbl">{label}</div></div>'

    persona_html = (
        f'<div class="persona-block"><div class="persona-label">Inferred Persona</div><div>{html.escape(persona)}</div></div>'
        if persona else ""
    )
    summary_html = (
        f'<div class="summary">'
        f'{_avg_card("CTA Clarity", avgs["cta_clarity"])}'
        f'{_avg_card("Copy Quality", avgs["copy_quality"])}'
        f'{_avg_card("Flow", avgs["flow_smoothness"])}'
        f'<div class="scard"><div class="val" style="color:#f39c12">{friction_count}</div>'
        f'<div class="lbl">Friction Pts</div></div>'
        f'</div>'
    )

    rows = ""
    for entry in report:
        if entry.get("action") == "scout_skip":
            rows += f"""
        <tr>
            <td>{entry['step']}</td>
            <td style="color:#888;font-size:11px">scout skip</td>
            <td style="white-space:pre-wrap;font-size:12px;color:#aaa">{html.escape(entry['observation'])}</td>
            <td style="color:#888">scout_skip</td>
            <td>—</td><td>—</td><td>—</td>
            <td>—</td>
            <td>—</td>
            <td>—</td>
            <td style="color:#888;font-weight:bold">SKIP</td>
            <td>{html.escape(entry.get('verdict',''))}</td>
        </tr>"""
            continue

        pf = entry.get("pass_fail", "").upper()
        pf_color = "#2ecc71" if pf == "PASS" else "#e74c3c" if pf == "FAIL" else "#f39c12"

        def score_cell(field, _entry=entry):
            obj = _entry.get(field)
            if not obj:
                return "—"
            s = obj.get("score", 0)
            cls = f"s{min(max(int(s), 1), 5)}"
            return f'<span class="score {cls}">{s}/5</span><br><span style="color:#aaa;font-size:11px">{html.escape(obj.get("note",""))}</span>'

        friction = entry.get("friction_points", [])
        friction_html = "".join(f'<div class="friction">• {html.escape(f)}</div>' for f in friction) if friction else "—"
        recs = entry.get("recommendations", [])
        recs_html = "".join(f'<div style="color:#00d4ff;font-size:11px">→ {html.escape(r)}</div>' for r in recs) if recs else ""

        conf = entry.get("confidence", "")
        conf_color = "#2ecc71" if conf == "high" else "#f39c12" if conf == "medium" else "#e74c3c" if conf == "low" else "#888"

        rows += f"""
        <tr>
            <td>{entry['step']}</td>
            <td><img class="step-shot" src="screenshots/step_{entry['step']}.png" width="200"/></td>
            <td>{html.escape(entry['observation'])}</td>
            <td>{html.escape(entry['action'])}</td>
            <td>{score_cell('cta_clarity')}</td>
            <td>{score_cell('copy_quality')}</td>
            <td>{score_cell('flow_smoothness')}</td>
            <td>{html.escape(entry.get('first_impression', '—'))}</td>
            <td>{friction_html}{recs_html}</td>
            <td style="color:{conf_color};font-weight:bold">{conf.upper() if conf else "—"}</td>
            <td style="color:{pf_color};font-weight:bold">{pf}</td>
            <td>{html.escape(entry.get('verdict',''))}</td>
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
    <div class="goal"><strong>Goal:</strong> {html.escape(goal)}</div>
    <p class="meta"><strong>Run ID:</strong> {run_id}</p>
    <p class="status" style="color:{status_color}">Final Status: {final_status}</p>
    {persona_html}
    {summary_html}
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


async def run(url=None, goal=None, max_steps=8, suite_dir=None, token_budget=None, email=None, password=None, scout=False, scout_threshold=3, provider: str = "anthropic", model: str = "claude-sonnet-4-6", storage_state=None, advisor: bool = False, session_id: str = None):
    if not url:
        raise ValueError("run() requires a url — no default fallback.")
    if advisor and provider == "anthropic" and model == "claude-opus-4-5":
        model = "claude-sonnet-4-6"
        print("🧠 Advisor mode: switching executor to claude-sonnet-4-6 (Opus 4.5 does not support advisor tool)")

    # Compute run_dir up front so every LLM call (scout, per-step, below-fold, advisor)
    # shares one Langfuse session. session_id overrides run_dir when provided (e.g. suite runs
    # pass a shared suite session ID so all pages land under one Langfuse session).
    if not goal:
        goal = _infer_goal_from_url(url)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_label = "_".join(goal.split()[0:3]).lower().strip(".,!?")
    if suite_dir:
        _path = urlparse(url).path.strip("/")
        page_segment = _path.replace("/", "_") if _path else "homepage"
        run_dir = f"{suite_dir}/{page_segment}"
        os.makedirs(run_dir, exist_ok=True)
    else:
        run_dir = _make_run_dir(url, "single_page")
    lf_session_id = session_id if session_id is not None else run_dir

    # ── Scout phase (optional) ────────────────────────────────────────────────
    scout_input_tokens = 0
    scout_output_tokens = 0

    if scout:
        with (_langfuse_propagate(session_id=lf_session_id) if _langfuse_propagate and lf_session_id else nullcontext()):
            scout_result = await scout_page(url, storage_state=storage_state, run_dir=run_dir, session_id=lf_session_id)
        score = scout_result["interest_score"]
        reason = scout_result["reason"]
        extracted = scout_result["extracted_text"]
        scout_input_tokens = scout_result["input_tokens"]
        scout_output_tokens = scout_result["output_tokens"]
        print(f"🔍 Scout: {url} scored {score}/5 — {reason}")

        if score < scout_threshold:
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
                "cta_clarity": None,
                "copy_quality": None,
                "flow_smoothness": None,
                "first_impression": "",
                "friction_points": [],
                "recommendations": [],
                "confidence": "low",
            }

            report = [scout_entry]
            report_path = f"{run_dir}/report.json"
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            print(f"\n📄 JSON report saved: {report_path}")

            html = _build_html_report(report, goal, run_id, run_label)
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
    adapter = LLMAdapter(provider, token_budget=token_budget)
    async with async_playwright() as p:
        headless = os.environ.get("CI", "false").lower() == "true"
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(storage_state=storage_state, user_agent=USER_AGENT)
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=20000)
        except Exception:  # noqa: S110 — networkidle can time out on long-polling sites; DOM is already loaded enough to screenshot
            pass

        console_logs = []
        page.on("console", lambda msg: console_logs.append({
            "type": msg.type,
            "text": msg.text
        }))

        network_events = []
        _pending_requests = {}

        def _on_request(request):
            _pending_requests[request.url] = asyncio.get_running_loop().time()

        def _on_response(response):
            start = _pending_requests.pop(response.url, None)
            duration_ms = round((asyncio.get_running_loop().time() - start) * 1000) if start else None
            if response.status >= 400 or (duration_ms is not None and duration_ms > 2000):
                network_events.append({
                    "url": response.url,
                    "status": response.status,
                    "duration_ms": duration_ms
                })

        page.on("request", _on_request)
        page.on("response", _on_response)

        conversation = []
        report = []
        persona = None
        advisor_called_count = 0
        screenshots_dir = f"{run_dir}/screenshots"
        os.makedirs(screenshots_dir, exist_ok=True)

        for step in range(max_steps):
            print(f"\n--- Agent Step {step + 1} ---")

            screenshot_path = f"{screenshots_dir}/step_{step + 1}.png"
            try:
                await page.screenshot(path=screenshot_path)
                encoded = await screenshot_as_base64(page)
            except Exception as e:  # noqa: BLE001
                print(f"⚠️  Screenshot failed on step {step + 1}: {e} — stopping run")
                break
            step_url = page.url
            print(f"📸 Screenshot saved: {screenshot_path} ({step_url})")

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
                        "text": _build_prompt(goal, step, max_steps, email, password, url=url, persona=persona)
                    }
                ]
            })

            advisor_tools = None
            if advisor and provider == "anthropic":
                advisor_tools = [{"type": "advisor_20260301", "name": "advisor", "model": "claude-opus-4-6", "max_uses": 1}]
            step_budget = 2048 if advisor else 1024
            try:
                raw, _in_tok, _out_tok, _raw_content = await adapter.complete(
                    conversation, model, step_budget, tools=advisor_tools,
                    metadata={"session_id": lf_session_id, "step": step + 1}
                )
                if advisor and _raw_content and any(
                    getattr(b, "type", None) == "advisor_tool_result" for b in _raw_content
                ):
                    advisor_called_count += 1
            except TokenBudgetExceeded as e:
                print(f"💰 Token budget exceeded ({e.tokens_used:,}/{e.budget:,}), stopping test")
                report.append({
                    "step": step + 1,
                    "screenshot": screenshot_path,
                    "observation": "Token budget exceeded",
                    "action": "budget_stop",
                    "target": None,
                    "reasoning": f"Used {e.tokens_used:,} of {e.budget:,} token budget",
                    "pass_fail": "fail",
                    "verdict": f"Test stopped — token budget of {e.budget:,} exceeded"
                })
                break
            print(raw)
            if token_budget:
                print(f"💰 Tokens: {adapter.tokens_used:,}/{token_budget:,}")

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
                try:
                    decision = json.loads(clean)
                except json.JSONDecodeError:
                    # Sonnet occasionally emits malformed JSON (missing commas, trailing commas, etc).
                    # Repair in place rather than bailing the whole loop.
                    from json_repair import repair_json
                    decision = json.loads(repair_json(clean))
                    print(f"⚠️  JSON repair fired on step {step + 1}")

                if persona is None:
                    persona = sanitize_persona(decision.get("persona") or "a plausible buyer or user for this product")

                entry = {
                    "step": step + 1,
                    "screenshot": screenshot_path,
                    "url": step_url,
                    "observation": decision["observation"],
                    "action": decision["action"],
                    "target": decision.get("target"),
                    "pass_fail": decision.get("pass_fail", "in_progress"),
                    "verdict": decision.get("verdict", ""),
                    "input_tokens": _in_tok,
                    "output_tokens": _out_tok,
                    "cta_clarity": decision.get("cta_clarity"),
                    "copy_quality": decision.get("copy_quality"),
                    "flow_smoothness": decision.get("flow_smoothness"),
                    "severity": decision.get("severity"),
                    "first_impression": decision.get("first_impression", ""),
                    "friction_points": sanitize_string_list(decision.get("friction_points", [])),
                    "recommendations": sanitize_string_list(decision.get("recommendations", [])),
                    "confidence": decision.get("confidence", ""),
                }
                if step == 0:
                    entry["persona"] = persona

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
                        try:
                            await asyncio.wait_for(page.goto(target, wait_until="networkidle"), timeout=20)
                        except asyncio.TimeoutError:
                            # networkidle can linger on long-polling sites; fall back to whatever's loaded
                            pass
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
                    await asyncio.wait_for(page.wait_for_load_state("networkidle"), timeout=15.0)
                except Exception:  # noqa: S110 — networkidle can linger on long-polling sites, fall through
                    pass
            await asyncio.sleep(1.5)

        # Below-the-fold analysis
        below_fold = None
        try:
            with (_langfuse_propagate(session_id=lf_session_id) if _langfuse_propagate and lf_session_id else nullcontext()):
                below_fold = await _run_below_fold_analysis(page, run_dir, url, persona or "a plausible buyer or user for this product", advisor=advisor, session_id=lf_session_id)
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

        if persona:
            try:
                from persona_library import save_inferred
                await save_inferred(url, persona, run_dir, session_id=lf_session_id)
            except Exception as e:  # noqa: BLE001
                print(f"⚠️  Could not save inferred persona: {e}")

        # Build HTML report
        html = _build_html_report(report, goal, run_id, run_label, below_fold=below_fold)
        html_path = f"{run_dir}/report.html"
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"🌐 HTML report saved: {html_path}")

        await asyncio.sleep(0.5)
        try:
            await asyncio.wait_for(context.close(), timeout=10)
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  Browser context close failed: {e}", file=sys.stderr)
        await _flush_langfuse_spans()

        total_input = sum(r.get("input_tokens", 0) for r in report if "input_tokens" in r)
        total_output = sum(r.get("output_tokens", 0) for r in report if "output_tokens" in r)
        return {
            "input": total_input + scout_input_tokens,
            "output": total_output + scout_output_tokens,
            "total": total_input + total_output + scout_input_tokens + scout_output_tokens,
            "step_count": len(report),
            "scout_skipped": False,
            "scout_input_tokens": scout_input_tokens,
            "scout_output_tokens": scout_output_tokens,
            "console_logs": console_logs,
            "network_events": network_events,
            "advisor_called_count": advisor_called_count,
            "advisor_eligible_steps": len(report) if advisor else 0,
        }

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Dev entry point for agent_core.run(). For the full CLI use run.py.", allow_abbrev=False)
    parser.add_argument("--url", type=str, required=True, help="Target URL (required)")
    parser.add_argument("--goal", type=str, default=None)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--email", type=str, default=None)
    parser.add_argument("--password", type=str, default=None)
    parser.add_argument("--token-budget", type=int, default=None)
    parser.add_argument("--provider", type=str, default="anthropic", choices=["anthropic", "openai", "google"])
    parser.add_argument("--model", type=str, default="claude-sonnet-4-6")
    parser.add_argument("--advisor", action="store_true", help="Enable Opus advisor tool for higher-quality judgment (Anthropic only)")
    args = parser.parse_args()
    asyncio.run(run(
        url=args.url,
        goal=args.goal,
        max_steps=args.steps,
        email=args.email,
        password=args.password,
        token_budget=args.token_budget,
        provider=args.provider,
        model=args.model,
        advisor=args.advisor,
    ))
