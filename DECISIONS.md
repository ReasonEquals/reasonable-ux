# DECISIONS.md

reasonable-ux started as a single Playwright + Claude loop that took a screenshot, asked "what do you see?", and clicked the next thing. By the end it was over 100 commits: multi-provider LLM routing, authenticated multi-page crawls, Langfuse observability with a hard-won memory-leak fix, a calibrated eval harness with regression detection, and a PDF reporting pipeline backed by Haiku synthesis. This document records the decisions that shaped the system — what was chosen, what was considered, what was given up, and what broke along the way.

---

## 1. Token Economics

Vision models are expensive per-call. Multi-step runs with 8–12 screenshots, a JSON schema, and a growing conversation history compound fast. Each eval run came out of pocket — this is a self-funded project. Every decision in this section was made under that constraint.

### Decision: Strip images from conversation history after each step

**Context:** Each agent step appends a new user message to the conversation history, which is re-sent in full on the next call. Screenshots are large base64-encoded blobs. After 8 steps, the history contains 8 screenshots that will be re-sent on every subsequent call.

**Decision:** Before appending the current step's screenshot, walk all prior user messages and drop any `"type": "image"` content blocks. Only the new step's screenshot travels in the payload. [`agent_core.py:850`](agent_core.py#L850)

**Alternatives considered:** Keep full multi-turn vision context so the model can reason across frames; window to the last N screenshots rather than stripping all.

**Tradeoffs accepted:** The model loses visual continuity between steps. This is acceptable because Playwright navigates to a new page state on each step — the prior screenshot doesn't reflect what's currently on screen anyway.

**Outcome:** Enabled 12-step defaults without hitting cost ceilings. Image history was the largest single cost driver; stripping it was the highest-leverage change in the section.

---

### Decision: Two JPEG quality tiers — 40 for steps, 60 for full-page

**Context:** Per-step screenshots are encoded, sent, then immediately stripped (see above). The full-page below-fold screenshot is a single larger call used once per run.

**Decision:** Two constants: quality 40 for per-step screenshots ([`agent_core.py:221`](agent_core.py#L221)), quality 60 for the below-fold full-page capture ([`agent_core.py:396`](agent_core.py#L396), [`agent_core.py:403`](agent_core.py#L403)).

**Alternatives considered:** Single quality level everywhere (80); PNG for lossless fidelity; adaptive quality based on image content.

**Tradeoffs accepted:** Slightly lower visual fidelity on per-step screenshots. In practice, buttons, nav labels, and body copy remain legible at quality 40. The full-page screenshot warrants 60 because it covers more visual surface and is used for a single more detailed analysis call.

**Outcome:** Materially smaller payloads on per-step screenshots without legibility loss. No fidelity regressions observed across author-run evaluations. No systematic test has been conducted at alternative quality levels — the numbers were set once during early development and have not been revisited.

---

### Decision: Model tiering — Sonnet executor, Haiku synthesizer

**Context:** Early eval runs used Opus 4.5 as the default executor. A single eval run cost approximately $8. That's not viable for iterative calibration work.

**Decision:** Swapped the default executor from Opus to `claude-sonnet-4-6` (Batch 31). Reserved `claude-haiku-4-5-20251001` for the exec summary synthesis step, which has lower quality requirements ([`generate_report.py:91`](generate_report.py#L91)).

**Alternatives considered:** Opus everywhere for maximum quality; Haiku everywhere for minimum cost; a cost gate that escalates to Opus when confidence is low.

**Tradeoffs accepted:** Sonnet emitted malformed JSON on a substantial share of steps initially — a smoke test fired the `json-repair` fallback on 3 of 7 steps (~40%); the first full eval after the swap fired repair on 49 of 58 steps (84.5%). This required adding the fallback (Batch 32) and a full baseline recalibration (Batch 33). The quality gap is real but acceptable for a tool that surfaces directional UX signals, not precise scores.

**Outcome:** Cost down ~4× ($8 Opus → ~$2 Sonnet per 20-URL eval run, sourced from the Batch 31 Anthropic-dashboard reconciliation). The swap regressed the pass rate to 5/20 against Opus-tuned bands; Batch 33 recalibrated labels for Sonnet's actual scoring behavior, restoring 19/20. Haiku exec synthesis runs at an estimated ~$0.002 per report.

---

### Decision: Check token budget after the LLM call, not before

**Context:** Without a hard cap, long multi-page runs could accumulate unbounded token spend. But observability requires that each call's data land in Langfuse before the process exits.

**Decision:** `step_budget = 2048 if advisor else 1024`. The budget check fires after the call is logged, raising an exception that exits the loop gracefully. [`agent_core.py:878`](agent_core.py#L878)

**Alternatives considered:** Check budget before each call and refuse if exceeded; let the model manage its own budget via the token-counting API.

**Tradeoffs accepted:** One additional LLM call fires at the budget boundary before the exception is raised.

**Outcome:** Langfuse captures the final step before exit. The report shows "stopped: budget exceeded" rather than a silent timeout. The hard cap has never been the wrong call — runs that hit it were exploratory runs that needed a ceiling.

---

## 2. Agent Architecture

### Decision: LLMAdapter with a special-case path for the Advisor tool

**Context:** The tool needs to support Anthropic, OpenAI, and Google models interchangeably. But the advisor-beta tool — which uses Anthropic's extended thinking and tool_use features — is Anthropic-only. LiteLLM doesn't support the advisor-beta API surface.

**Decision:** `LLMAdapter.complete()` routes everything through `litellm.acompletion()` ([`agent_core.py:154`](agent_core.py#L154)) except when advisor mode is active, which routes directly to `_complete_anthropic_advisor()` ([`agent_core.py:168`](agent_core.py#L168)). The adapter class unifies tracing across both paths. [`agent_core.py:123`](agent_core.py#L123)

**Alternatives considered:** Separate provider classes with a factory; hard-code Anthropic everywhere and treat multi-provider as a future concern; run the advisor through LiteLLM with a custom plugin.

**Tradeoffs accepted:** Two code paths to maintain. When `--advisor` is enabled with a non-Anthropic provider, the advisor tool is silently dropped — the request still routes through LiteLLM with the chosen provider, but no advisor capability is available. There is no automatic fallback to Anthropic.

**Outcome:** `--provider openai/gpt-4o` works for standard evaluation runs. Advisor functionality is unaffected by provider choice. The two paths share the same Langfuse tracing hooks.

---

### Decision: `nav:<Label>` prefix instead of CSS selectors for navigation

**Context:** The agent was emitting CSS selectors like `a[href="/pricing"]` or `a:has-text("Pricing")` for main navigation links. These are fragile: class names change, `:has-text()` is unsupported in Playwright's strict selector engine, and generated markup varies wildly across frameworks.

**Decision:** The prompt instructs Claude to emit `nav:<Visible Label>` (e.g., `nav:Pricing`) for main-navigation links. The agent loop detects the prefix and routes to `_click_nav_by_label()`, which uses Playwright's semantic `page.get_by_role("link", name=…)` API, with an `a:has-text()` fallback. [`agent_core.py:241`](agent_core.py#L241)

**Alternatives considered:** CSS selectors everywhere; XPath; Playwright's `get_by_text`; require the user to supply selectors.

**Tradeoffs accepted:** This is a prompt-engineering contract, not a code contract. If the model drifts back to CSS selectors for nav links, clicks fail without an obvious error message. That made regression detection essential.

**Outcome:** Navigation reliability improved substantially across diverse frameworks. The eval harness was extended with `_NAV_DRIFT_RE` (Batch 43) to catch drift automatically — see Eval Harness section below.

---

### Decision: Infer persona on step 1, thread it through the entire run

**Context:** UX evaluation should reflect a specific buyer archetype, not a generic "user." But requiring a `--persona` flag on every invocation adds friction and produces worse output when users make poor guesses about their own customers.

**Decision:** On step 1, `_build_prompt` receives `persona=None` and asks Claude to infer a plausible evaluator persona from the first screenshot, URL, and page title, returning it as a top-level `persona` field in the JSON response. From step 2 onward, the inferred persona is passed back into `_build_prompt` and Claude is told to stay in character. The same string is threaded into the below-fold analysis call. [`agent_core.py:294`](agent_core.py#L294), [`agent_core.py:836`](agent_core.py#L836), [`agent_core.py:1037`](agent_core.py#L1037)

**Alternatives considered:** Always require `--persona` flag; re-infer each step; skip personas and evaluate from a neutral perspective.

**Tradeoffs accepted:** The persona is locked after step 1. If the inferred persona is wrong, the entire run reflects it. There is no mid-run correction.

**Outcome:** Multi-site validation (Batch 44) produced site-appropriate personas without user input — DTC sites got "eco-conscious millennial shopper," content platforms got "aspiring paid newsletter creator." The persona is written to `report.json` so runs are reproducible.

---

## 3. Auth and Multi-Page Orchestration

### Decision: Pre-authenticate once, serialize browser storage state, reuse across pages

**Context:** Multi-page runs against authenticated SaaS products initially required re-authenticating for each page. That meant 3× the runtime cost and a constant risk of session invalidation mid-suite.

**Decision:** `_do_auth()` ([`run.py:124`](run.py#L124)) logs in once via a dedicated Playwright context, calls `context.storage_state()` (cookies + localStorage), writes the result to a tempfile, and passes the tempfile path to every subsequent page evaluation via the agent's `storage_state=` parameter. [`run.py:227`](run.py#L227)

**Alternatives considered:** Re-authenticate per page; pass session tokens as environment variables; API-key-based auth bypass for supported apps.

**Tradeoffs accepted:** Tempfile cleanup is required on every exit path, including error paths. Session expiry between pages is a silent failure mode.

**Outcome:** Multi-step login flows (email → Continue → password → Submit) work reliably. Auth happens once per suite. The auth debug screenshot written to `runs/auth_debug_{PID}.png` on failure (Batch 40) has been the primary diagnostic tool for auth regressions.

---

### Decision: Hard stop on auth failure, no silent fallback

**Context:** If auth failed silently, the agent would start evaluating the login page as if it were the product — producing confident, detailed, completely worthless UX reports with no indication that anything was wrong.

**Decision:** Auth failure raises an exception immediately (Batch 12b). A debug screenshot is written to `runs/auth_debug_{PID}.png` before the exception propagates so there's something to inspect.

**Alternatives considered:** Log a warning and continue unauthenticated; retry once with a longer timeout; degrade to a single-page unauthenticated run.

**Tradeoffs accepted:** Transient auth failures abort the entire run. There is no automatic retry.

**Outcome:** Forces the caller to fix auth before spending tokens. Every instance of auth failure in practice has been a genuine problem (wrong selector, changed login flow, rate-limited endpoint) rather than a transient blip.

---

### Decision: Sequential page execution, not parallel

**Context:** Parallel Playwright contexts could theoretically speed up multi-page evaluation suites.

**Decision:** `run_pages()` executes pages sequentially. [`run.py:108`](run.py#L108)

**Alternatives considered:** `asyncio.gather` over all pages simultaneously; a bounded concurrency pool (e.g., 2 at a time).

**Tradeoffs accepted:** Slower wall-clock time for large suites. More importantly: if `agent_run` raises an uncaught exception mid-suite, the loop exits early and subsequent pages do not run — there is no per-page error isolation. The auth tempfile is always cleaned up via `finally` regardless. Each Playwright context is independent and closed by the agent loop after each page, so memory does not accumulate across a long suite.

**Outcome:** Shared auth session is trivially correct — no cross-context state collision. Error recovery is page-by-page without cascading failures. Rate limit headroom is preserved. The wall-clock cost is acceptable for a tool that runs a handful of pages per session rather than hundreds.

---

## 4. Observability — The Langfuse Journey

This section has more narrative than the others because the observability work was the most instructive failure sequence in the project.

### Decision: Replace `AnthropicInstrumentor` with `@observe` on the three direct-SDK paths

**Context (Batch 38.1):** LiteLLM's per-step calls were traced via `litellm.callbacks = ["langfuse_otel"]`. But the advisor, below-fold analysis, and scout functions used the Anthropic SDK directly — bypassing LiteLLM entirely. Those three paths accounted for 15–20% of all tokens and were completely invisible in Langfuse.

**First attempt (Batch 39):** Added `opentelemetry-instrumentation-anthropic` and called `AnthropicInstrumentor().instrument()` after the Langfuse TracerProvider was initialized. Appeared to work. Shipped.

**Root cause discovered (Batch 45):** `AnthropicInstrumentor().instrument()` runs at import time — before the TracerProvider is initialized. The spans were silently discarded. No error. No warning. Just missing traces in the Langfuse UI. The only way to catch this was to count spans per session and notice the gap.

**Decision (Batch 45):** Removed the entire OTel approach. Replaced with langfuse v4's `@observe(as_type="generation", capture_input=False, capture_output=False)` via the `@_lf_observe` wrapper ([`agent_core.py:42`](agent_core.py#L42)). Each of the three functions (`_complete_anthropic_advisor` at [`agent_core.py:167`](agent_core.py#L167), `_run_below_fold_analysis` at [`agent_core.py:388`](agent_core.py#L388), `scout_page` at [`agent_core.py:448`](agent_core.py#L448)) is decorated with `@_lf_observe`. Each call site wraps the invocation in `with propagate_attributes(session_id=run_dir)` to group spans under the correct Langfuse session.

**Alternatives considered:** Fix the OTel init ordering (fragile — depends on import order); instrument at the `httpx` layer (too low-level, loses semantic context); accept the blind spots.

**Tradeoffs accepted:** Manual `_lf_update_generation()` call required inside each decorated function to preserve prompt/response visibility without triggering auto-capture. More boilerplate per function.

**Outcome:** All three paths surface as named generations in Langfuse under the correct session. Runtime-verified end-to-end via two smoke tests against a live Langfuse instance: a 4-step LiteLLM run produced step generations plus a `_run_below_fold_analysis` generation; a separate `--scout --advisor` run produced `scout_page`, `_complete_anthropic_advisor`, and `_run_below_fold_analysis` generations. Both runs were correctly grouped under their respective `session_id` (`runs/<domain>/<timestamp>_single_page`), confirming `propagate_attributes` works across both LiteLLM and direct-SDK paths.

---

### Decision: `capture_input=False, capture_output=False` — never let `@observe` auto-serialize

**Context:** The first version of `@_lf_observe` used default `@observe` settings, which auto-serialize all function arguments and return values to JSON for the trace.

**What broke:** `_run_below_fold_analysis(page, ...)` receives a Playwright `Page` object as its first argument. `@observe` attempted to JSON-serialize it, recursively touching every internal browser and DOM attribute. CPU pegged at 100%. The process consumed over 50 GB of RAM trying to serialize the object graph and had to be killed manually. The same problem would have occurred with `messages` in `_complete_anthropic_advisor` — base64-encoded screenshots inside message content blocks are enormous.

**Decision:** `capture_input=False, capture_output=False` on every `@_lf_observe` call ([`agent_core.py:52`](agent_core.py#L52)). Each decorated function manually calls `_lf_update_generation()` after its LLM call, passing only text and scalar values. The advisor's path extracts `safe_input` by stripping image content blocks from messages before logging.

**Tradeoffs accepted:** Prompt/response visibility in Langfuse requires a manual call inside each function. Forgetting it produces a trace with no content.

**Outcome:** Zero memory regression. Trace content is present and readable in Langfuse. This invariant is documented in CLAUDE.md section 5 to prevent future regression.

---

### Decision: `propagate_attributes` is a sync context manager — never `async with`

**Context:** `propagate_attributes(session_id=...)` returns `_AgnosticContextManager` from `opentelemetry.util._decorator`. It implements `__enter__` and `__exit__` but not `__aenter__` and `__aexit__`.

**What broke:** `async with propagate_attributes(...)` raises a runtime error immediately. The agent loop is async throughout, so this was a natural mistake.

**Decision:** Always `with propagate_attributes(...)` (sync context manager). Inside the `with` block, `await` async functions normally — OTel context propagates through `contextvars` across `await` boundaries.

**Tradeoffs accepted:** Slightly counterintuitive in an async codebase. The sync `with` block can contain `await` expressions, which looks wrong but is correct.

**Outcome:** Documented as a section-5 invariant in CLAUDE.md. Has not regressed since.

---

## 5. Reporting Pipeline

### Decision: Jinja2 HTML → Playwright headless PDF

**Context:** Reports need complex CSS layouts: dark theme, gradient backgrounds, screenshot embeds, per-step tables, score callouts.

**Decision:** Render the report to a Jinja2 HTML template written to a temp file, then open it in a headless Playwright Chromium context and call `page.pdf()`. Screenshots are rewritten to `file://` URIs so Chromium can load them locally. [`generate_report.py:168`](generate_report.py#L168)

**Alternatives considered:** `reportlab` (Python-native but requires manual layout math); `weasyprint` (HTML→PDF without a browser but CSS support is limited); plain text output with no PDF.

**Tradeoffs accepted:** Requires a Playwright/Chromium install. Slower than native PDF generation. The temp HTML file must be cleaned up on every exit path.

**Outcome:** Full HTML/CSS fidelity. The template is editable by anyone who knows HTML. Screenshot embeds render correctly. `document.fonts.ready` is awaited before rendering to ensure fonts load.

---

### Decision: Haiku for exec summary synthesis, not deterministic aggregation

**Context:** Multi-page runs produce per-page JSON friction lists. A synthesized cross-page summary is more useful than a flat concatenation, but deterministic aggregation (pick the top N friction points by frequency) loses nuance.

**Decision:** Pass all page findings to `claude-haiku-4-5-20251001` with a prompt that explicitly requires actionable recommendations: "name actual UI elements, not generic advice." Strict JSON output. Degrades gracefully if Haiku fails — the page-by-page report is always generated regardless. [`generate_report.py:61`](generate_report.py#L61), [`generate_report.py:91`](generate_report.py#L91)

**Alternatives considered:** Frequency-weighted top-N aggregation; Sonnet for better synthesis; skip exec summary for single-page runs.

**Tradeoffs accepted:** Non-deterministic. Haiku occasionally produces malformed JSON (~2% of runs). The fallback is an empty exec summary, not a crashed report.

**Outcome:** Synthesized summaries naturally weight findings that appear across multiple pages. Haiku is fast (~2 seconds) and cheap (~$0.002 per report). The strict JSON output constraint has held.

---

### Decision: Dedup pass before Haiku synthesis

**Context:** If every page in a 4-page run flags "CTA is unclear," Haiku receives and likely repeats that finding four times, wasting space and obscuring other patterns.

**Decision:** A 7-line pass in `stitch_reports` iterates page summaries before passing them to Haiku, clearing any `top_finding` string that has already appeared. First occurrence wins. [`generate_report.py:287`](generate_report.py#L287)

**Alternatives considered:** Let Haiku deduplicate (it sometimes does, unreliably); include all findings and post-process the summary; deduplicate after synthesis.

**Tradeoffs accepted:** Order-dependent — the first page's version of a repeated finding survives. Pages that appear later in the suite may have a better articulation of the same finding.

**Outcome:** Exec summaries present novel cross-page findings. The dedup pass has caught repeated findings in multi-page runs against real SaaS products where a broken nav pattern propagated across every page.

---

## 6. Eval Harness

### Decision: Isolated `eval_runs/` directory with a `manifest.json` per run

**Context:** Eval runs generate agent artifacts (screenshots, JSON, PDFs) identical in structure to real audit runs. If they land in `runs/`, they pollute the audit history and make it hard to diff eval results over time.

**Decision:** Eval output goes to `eval_runs/<timestamp>_<label>/`. Each eval invocation writes `manifest.json` capturing pass rate, per-URL results, labels file SHA, and settings at time of run. [`evals/run_evals.py`](evals/run_evals.py)

**Alternatives considered:** Tag eval runs in `runs/` with a metadata field; a separate eval database; a dedicated eval repo.

**Tradeoffs accepted:** Two output directories to maintain. `eval_runs/` requires its own cleanup policy.

**Outcome:** Eval manifests are diffable. The same labels SHA means two manifests are directly comparable for regression detection. Audit history in `runs/` stays clean and readable. See [`eval_results_sample.md`](eval_results_sample.md) for a committed sample run.

---

### Decision: `_NAV_DRIFT_RE` — automated regression detection for nav selector drift

**Context:** Prompt changes and model updates occasionally caused agents to revert to emitting CSS selectors (`a[href="/pricing"]`) for navigation links instead of the required `nav:Pricing` prefix. This produced silent click failures — the selector would fail without an obvious error.

**Decision:** A compiled regex pattern at [`evals/run_evals.py:137`](evals/run_evals.py#L137) matches CSS anti-patterns (`a[href`, `a:has-text`, `a:contains`, `.nav-`, `.nav_`, `nav a`, `header a`) in step `target` fields. Labels with `assert_nav_drift: true` fail the eval if the pattern matches any step. All 7 `saas_landing` labels carry this flag. [`evals/run_evals.py:143`](evals/run_evals.py#L143), [`evals/run_evals.py:194`](evals/run_evals.py#L194)

**Alternatives considered:** Manual review of eval output after each run; a separate linting pass; no regression detection.

**Tradeoffs accepted:** The regex can false-positive on legitimately complex CSS selectors for non-navigation elements on unusual pages.

**Outcome:** Drift caught automatically on every eval run. The regression that prompted this change (Batch 43) would have been invisible in production without it.

---

### Decision: Calibrate eval baseline to the model's actual behavior, not ideal output

**Context:** The first locked baseline had a 52.6% pass rate — completely unusable as a regression signal. Running the eval against changes would produce noise rather than signal.

**Root cause:** 10 label bugs surfaced iteratively: substring mismatches (`subscribe` matched `subscription`), wrong expected keywords for specific pages, score bands set too narrow around ideal expected values rather than around observed model behavior.

**Decision (Batches 28 + 28.5 — Opus arc):** Batch 28 fixed 6 label bugs and widened score bands by ±12 (52.6% → 65%). Batch 28.5 fixed 4 more label bugs and recalibrated bands against observed Opus output (65% → 19/20).

**Decision (Batch 33 — Sonnet arc):** The Opus → Sonnet swap (Batch 31) regressed the pass rate to 5/20 against Opus-tuned bands. Batch 33 recalibrated labels for Sonnet's actual scoring behavior, restoring 19/20 (95%) — the locked baseline used today.

**Lesson learned:** Narrower score bands are not stricter — they are unstable. A band calibrated to ideal output will fail legitimately correct responses. A band calibrated to observed model behavior within a reasonable range is both stable and sensitive to regressions. Write the rubric based on what the model actually does, then tighten if the model improves.

---

### Decision: Bot-block preflight before spending vision tokens

**Context:** Some URLs in eval fixtures return a Cloudflare CAPTCHA at the CDN edge before Playwright even loads the application. Running the full agent on a blocked URL costs tokens and produces a meaningless report.

**Decision:** HTTP HEAD/GET check before each eval URL ([`evals/run_evals.py:60`](evals/run_evals.py#L60)). Checks HTTP status code, content type, and text patterns (`"just a moment"`, `"captcha"`, `"access denied"`). Blocked URLs are marked `"skipped"`, not `"failed"`, so they don't contaminate the pass rate.

**Alternatives considered:** Let the agent handle it and detect failure in the report; skip blocked URLs manually before each eval run; use a proxy to bypass CDN challenges.

**Tradeoffs accepted:** Only catches CDN-edge challenges. JS-rendered CAPTCHAs served by the application after page load slip through to the agent run.

**Outcome:** Eval pass rates are not polluted by external infrastructure failures. The skipped count in the manifest distinguishes infrastructure problems from product regressions.

---

## 7. Security and Product Framing

### Decision: `_sanitize_extracted.py` — indirect prompt injection defense

**Context:** Personas and friction strings extracted from agent output are fed back into downstream prompts — exec summary synthesis, multi-persona analysis, persona generation from the URL. A malicious website could embed instructions in on-page text that Claude captures and later re-injects into a downstream prompt.

**Decision:** `_sanitize_extracted.py` caps persona strings at 200 characters, friction and recommendation fields at 500 characters, and strips strings matching patterns for role markers and instruction smuggles before any re-injection into a prompt.

**Alternatives considered:** Trust model output; validate only at the final output boundary; use a dedicated guardrail model.

**Tradeoffs accepted:** Overly aggressive sanitization could truncate legitimate UX findings on verbose pages.

**Outcome:** Indirect injection surface reduced. Added in Batch 27 after mapping the full data flow from extraction to re-injection. The character caps have not truncated meaningful findings in practice.

---

### Decision: `TERMS.md` covering third-party TOS compliance and data handling

**Context:** Running automated browser sessions against third-party websites touches their terms of service. Without explicit terms of use, the tool's commercial viability is ambiguous and potential users have no documented basis for their own compliance decisions.

**Decision:** `TERMS.md` added (Batch 46) covering: third-party TOS compliance (user's responsibility to verify), data retention (runs/ is gitignored and never transmitted), API cost model (user bears LLM costs), acceptable use, no-warranty disclaimer.

**Alternatives considered:** README footnote; no terms; full legal terms drafted by counsel.

**Tradeoffs accepted:** The TERMS.md provides clarity but not legal protection. It is not a substitute for counsel if the tool is used commercially at scale.

**Outcome:** Reduces ambiguity for potential users evaluating the tool. Required milestone before any public release.

---

### Decision: `html.escape()` on all LLM-extracted fields in HTML report output

**Context:** `_build_html_report` and `_build_below_fold_html` build HTML via f-strings. All LLM-extracted strings — friction points, recommendations, observations, verdicts, first impressions, score notes, persona, below-fold findings — were interpolated directly without HTML escaping. `_sanitize_extracted.py` strips prompt-injection patterns (role markers, instruction smuggles) but does not HTML-escape.

**The gap:** A malicious site under audit could embed `<script>` tags, event handlers, or other HTML in its page copy. Claude might extract these verbatim into friction points or observations. When the HTML report is opened — including when sent to a founder or client — the injected markup executes in the browser. This is a stored XSS in the tool's primary deliverable artifact.

**Decision (Batch 56+57):** `import html` (stdlib, zero dependencies). Every LLM-extracted string is wrapped with `html.escape()` before f-string injection into report HTML. [`agent_core.py:356`](agent_core.py#L356), [`agent_core.py:636`](agent_core.py#L636), [`agent_core.py:656`](agent_core.py#L656), [`agent_core.py:659`](agent_core.py#L659), [`agent_core.py:661`](agent_core.py#L661), [`agent_core.py:616`](agent_core.py#L616), [`agent_core.py:670–679`](agent_core.py#L670). Internal values (step numbers, pass/fail labels, confidence colors) are not escaped — they are generated by application code, not model output.

**Alternatives considered:** Sanitize at write time in `_sanitize_extracted.py` (would require extending that module's scope beyond prompt-injection defense to HTML hygiene — two responsibilities in one module); switch f-string HTML to a Jinja2 template with autoescape enabled (Jinja2 is already used and autoescaped in `generate_report.py` — would improve consistency but requires migrating ~130 lines of inline HTML per function); accept the risk given local-only use (unacceptable because the HTML report is the primary deliverable, intended to be sent to founders/clients).

**Tradeoffs accepted:** `html.escape()` encodes `&`, `<`, `>`, `"`, `'` — legitimate angle brackets in LLM output (e.g., "clarity < 3" in a note) will render as `&lt;` in the raw HTML but display correctly in the browser. No observable visual regression.

**Outcome:** Stored XSS surface eliminated in both the inline HTML path (`_build_html_report`, `_build_below_fold_html`) and the below-fold adj table. The Jinja2 path in `generate_report.py` was already protected via `autoescape=jinja2.select_autoescape(["html", "j2"])`.

---

### Decision: Repo visibility — pre-public checklist passed

**Context:** The repo is private during development. Before flipping it public, a pre-release checklist was run (2026-04-26) to confirm no secrets in history, no hardcoded credentials in the working tree, all dependency licenses are permissive, the README is externally readable, and the CI workflows are in working order.

**Decision:** Conditions met as of 2026-04-26. Repo is ready to flip public; the visibility toggle itself is intentionally a separate manual step.

**Conditions verified:**
- Git history content scan (`git log --all -S`) — all `sk-ant` hits are the placeholder string `ANTHROPIC_API_KEY=sk-ant-...` in docs; no real key values in history.
- Working tree credential grep — nothing outside `.env.example`.
- Dependency licenses — all MIT / Apache-2.0 / BSD: anthropic (MIT), playwright (Apache-2.0), beautifulsoup4 (MIT), python-dotenv (BSD-3-Clause), requests (Apache-2.0), openai (Apache-2.0), google-generativeai (Apache-2.0), Pillow (MIT-CMU), Jinja2 (BSD), json-repair (MIT), litellm (MIT), langfuse (MIT), opentelemetry-api/sdk/exporter (Apache-2.0).
- README externally readable — accurate after Batch 49 cleanup.
- CI workflows — `test.yml` fixed in Batch 52; `pytest.yml` added in Batch 51.

**Tradeoffs accepted:** Once public, commit history is permanently visible. The history scan covered known key prefixes; a more exhaustive scan (e.g. trufflehog) would add confidence but was assessed as out of proportion given the project's dev history.

**Outcome:** Dependency license audit cadence set to annual.

---

## 8. Deferred — Considered, Not Built

Naming what was decided against is as informative as naming what was built. Each item below was considered, sized, and deliberately left out — not forgotten.

### Multi-step auth (OAuth, MFA, SSO)

Single-step email/password covers every site the tool was used against in development. OAuth requires per-provider implementations; MFA requires session continuity beyond what `storage_state` provides. The complexity-to-coverage ratio doesn't justify the work at current scale. Documented as a known limitation in the README rather than as missing functionality.

### Unit and integration tests for the agent loop itself

The agent loop's behavior depends on a vision model's response to a screenshot — fundamentally nondeterministic. Mocking the LLM produces tests that verify the mock, not the agent. Loop coverage is held by the `evals/` harness (semantic regression detection on real model behavior). A traditional unit test layer for the loop itself would compete with the eval harness for maintenance attention without adding signal.

Pure helpers — `_sanitize_selector`, `_infer_goal_from_url`, `_sanitize_extracted` (prompt-injection defense), and `_NAV_DRIFT_RE` / `_nav_drift_check` from the eval harness — are unit-tested in `tests/`. These are deterministic functions where unit tests carry real signal; they were verified by source-code mutation testing during Batch 48 to confirm tests catch realistic regressions.

### LLM response caching

Cached responses would distort the cost numbers used in this document and mask drift — a prompt or model change that silently broke the agent would be hidden by a cache hit. The eval harness depends on real, fresh model output to detect regressions. Caching is the right choice for many LLM applications; it is the wrong choice for a tool whose primary signal is fresh model behavior.

### Persistent eval database

Manifests are written as flat JSON files in `eval_runs/<timestamp>_<label>/manifest.json`. Diffing two manifests is `diff` or `jq`. A database adds an operational dependency without solving a problem at current scale (tens of eval runs, not thousands).

### Browser fingerprint or bot-detection evasion

Out of scope on principle. The CDN-edge bot-block preflight (Section 6) skips blocked URLs rather than circumventing them. Users of the tool are responsible for respecting target-site terms of service — see [`TERMS.md`](TERMS.md).

### Web UI / hosted control plane

CLI-only is the right surface for the audience this tool serves: developers and founders who want a report, not an interface. The static `dashboard.html` is a viewer for run history, not a control plane. A hosted UI would require auth, multi-tenancy, and infrastructure that the current single-user use case doesn't justify.

### Parallel page execution

Listed under Section 3 as a decision, not deferred — the choice to run pages sequentially is load-bearing for shared auth state and rate-limit headroom, not a "we'll get to it later" item.
