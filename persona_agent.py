import json

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()
client = Anthropic()


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
    frustrations = persona.get("frustrations") or []
    success = persona.get("success") or []

    prompt = f"""You are a UX expert evaluating a website through the lens of a specific user persona.

URL: {url}

Persona:
Name: {persona.get("name", "")}
Role: {persona.get("role", "")}
Company / context: {persona.get("company", "")}
Archetype: {persona.get("archetype", "")}
Goal: {persona.get("goal", "")}
Jobs-to-be-done: {persona.get("jtbd", "")}
Frustrations:
{json.dumps(frustrations, indent=2)}
Success looks like:
{json.dumps(success, indent=2)}
Context: {persona.get("context", "")}

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
        kwargs = dict(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        if advisor:
            kwargs["tools"] = [{"type": "advisor_20260301", "name": "advisor", "model": "claude-opus-4-6", "max_uses": 1}]
            kwargs["betas"] = ["advisor-tool-2026-03-01"]
            response = client.beta.messages.create(**kwargs)
        else:
            response = client.messages.create(**kwargs)
        raw = next((b.text for b in reversed(response.content) if hasattr(b, "text")), "").strip()
        clean = raw.replace("```json", "").replace("```", "").strip()
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
