import json
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()
client = Anthropic()


async def evaluate(persona: dict, report: list, url: str) -> dict:
    """
    Re-evaluate the UX through a specific persona's lens.
    Returns { persona_name, persona_description, score, key_findings, recommendations }.
    On parse failure returns a minimal error dict rather than crashing.
    """
    prompt = f"""You are a UX expert evaluating a website through the lens of a specific user persona.

URL: {url}

Persona:
Name: {persona["name"]}
Description: {persona["description"]}
Goals:
{json.dumps(persona["goals"], indent=2)}
Concerns:
{json.dumps(persona["concerns"], indent=2)}

UX Evaluation Report (from an automated agent):
{json.dumps(report, indent=2)}

Re-evaluate this UX report through the specific lens of this persona. Consider how their goals, background, and concerns would shape their experience based on what the report observed.

Return ONLY a JSON object with exactly these fields:
- persona_name: string
- persona_description: string
- score: float between 1.0 and 5.0 (how well the site serves this persona)
- key_findings: array of 3-5 strings (most important observations for this persona)
- recommendations: array of 3-5 strings (specific, actionable improvements for this persona)

Return nothing but the JSON object. No markdown, no explanation."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        clean = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
        result.setdefault("persona_name", persona["name"])
        result.setdefault("persona_description", persona["description"])
        result.setdefault("score", 0.0)
        result.setdefault("key_findings", [])
        result.setdefault("recommendations", [])
        return result
    except Exception as e:
        return {
            "persona_name": persona["name"],
            "persona_description": persona["description"],
            "score": 0.0,
            "key_findings": [f"Evaluation failed: {e}"],
            "recommendations": [],
        }
