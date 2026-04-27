import json

from dotenv import load_dotenv

from _sanitize_extracted import sanitize_field, sanitize_string_list
from agent_core import LLMAdapter

load_dotenv()


async def evaluate(persona: dict, report: list, url: str, advisor: bool = False) -> dict:
    """
    Re-evaluate the UX through a specific structured persona's lens.
    Accepts the Phase D persona schema (id, name, role, company, archetype,
    goal, jtbd, frustrations, success, techSavvy, context, quote, color).
    Returns the full persona dict merged with {score, key_findings,
    recommendations} so downstream _resolve_personas can consume it directly.
    On parse failure returns the persona with a minimal error payload rather
    than crashing the run.
    """
    safe_url = sanitize_field(url)
    safe_name = sanitize_field(persona.get("name", ""))
    safe_role = sanitize_field(persona.get("role", ""))
    safe_company = sanitize_field(persona.get("company", ""))
    safe_archetype = sanitize_field(persona.get("archetype", ""))
    safe_goal = sanitize_field(persona.get("goal", ""))
    safe_jtbd = sanitize_field(persona.get("jtbd", ""))
    safe_context = sanitize_field(persona.get("context", ""))
    frustrations = sanitize_string_list(persona.get("frustrations") or [])
    success = sanitize_string_list(persona.get("success") or [])

    prompt = f"""You are a UX expert evaluating a website through the lens of a specific user persona.

URL: {safe_url}

Persona:
Name: {safe_name}
Role: {safe_role}
Company / context: {safe_company}
Archetype: {safe_archetype}
Goal: {safe_goal}
Jobs-to-be-done: {safe_jtbd}
Frustrations:
{json.dumps(frustrations, indent=2)}
Success looks like:
{json.dumps(success, indent=2)}
Context: {safe_context}

UX Evaluation Report (from an automated agent):
{json.dumps(report, indent=2)}

Re-evaluate this UX report through the specific lens of this persona. Consider how their
goal, JTBD, frustrations, and success criteria would shape their experience based on what
the report observed.

Return ONLY a JSON object with exactly these fields:
- score: float between 1.0 and 5.0 (how well the site serves this persona)
- key_findings: array of 3-5 strings (most important observations for this persona)
- recommendations: array of 3-5 strings (specific, actionable improvements for this persona)

Return nothing but the JSON object. No markdown, no explanation."""

    try:
        tools = (
            [{"type": "advisor_20260301", "name": "advisor", "model": "claude-opus-4-6", "max_uses": 1}]
            if advisor
            else None
        )
        adapter = LLMAdapter("anthropic")
        raw, _, _, _ = await adapter.complete(
            messages=[{"role": "user", "content": prompt}],
            model="claude-sonnet-4-6",
            max_tokens=1500,
            tools=tools,
        )
        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        parsed = json.loads(clean)
        return {
            **persona,
            "score": float(parsed.get("score", 0.0)),
            "key_findings": parsed.get("key_findings", []) or [],
            "recommendations": parsed.get("recommendations", []) or [],
        }
    except Exception as e:
        return {
            **persona,
            "score": 0.0,
            "key_findings": [f"Evaluation failed: {e}"],
            "recommendations": [],
        }
