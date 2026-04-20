import json

from anthropic import Anthropic
from dotenv import load_dotenv

from report_data import DEFAULT_PERSONAS

_ACCENT_COLORS = ["#7b2cbf", "#2c7bbf", "#bf2c7b"]
_ARCHETYPES = ["Evaluator", "Practitioner", "Outsider"]
_REQUIRED_FIELDS = (
    "id", "role", "company", "archetype", "goal", "jtbd",
    "frustrations", "success", "techSavvy", "context", "quote", "color",
)


def _pad_with_defaults(personas: list, want: int = 3) -> list:
    """Ensure we return at least `want` personas by padding from DEFAULT_PERSONAS
    while avoiding id collisions with whatever Haiku produced."""
    existing_ids = {p.get("id") for p in personas if isinstance(p, dict)}
    padded = list(personas)
    for fallback in DEFAULT_PERSONAS:
        if len(padded) >= want:
            break
        if fallback["id"] in existing_ids:
            continue
        padded.append(fallback)
        existing_ids.add(fallback["id"])
    return padded[:want]


def _validate_persona(p) -> bool:
    if not isinstance(p, dict):
        return False
    for field in _REQUIRED_FIELDS:
        if field not in p:
            return False
    if not isinstance(p["frustrations"], list) or len(p["frustrations"]) < 1:
        return False
    if not isinstance(p["success"], list) or len(p["success"]) < 1:
        return False
    return True


def _normalize_personas(raw: list) -> list:
    """Coerce the accent color palette + fill obvious gaps so every persona
    validates. Keeps whatever Haiku produced, overrides color with our palette
    to avoid clash with the Editorial theme's plum accent."""
    normalized = []
    for i, p in enumerate(raw):
        if not isinstance(p, dict):
            continue
        p = dict(p)
        p["color"] = _ACCENT_COLORS[i % len(_ACCENT_COLORS)]
        if not p.get("archetype"):
            p["archetype"] = _ARCHETYPES[i % len(_ARCHETYPES)]
        try:
            p["techSavvy"] = max(1, min(5, int(p.get("techSavvy", 3))))
        except (TypeError, ValueError):
            p["techSavvy"] = 3
        for list_field in ("frustrations", "success"):
            v = p.get(list_field)
            if not isinstance(v, list):
                p[list_field] = []
        if _validate_persona(p):
            normalized.append(p)
    return normalized


async def _call_haiku(url: str, summary: str) -> list:
    """Single Haiku call that returns a JSON array of 3 structured personas."""
    load_dotenv()
    client = Anthropic()

    system_prompt = (
        "You generate realistic structured user personas for UX research. "
        "Return only a valid JSON array. No markdown, no prose, no commentary."
    )

    user_prompt = f"""URL: {url}

UX report summary:
{summary}

Infer the product type and its likely audience from the URL and summary above.
Then return a JSON array of EXACTLY 3 DISTINCT personas that represent different
archetypes evaluating this specific product. The three archetypes must be:
  1. "Evaluator"    — a decision-maker comparing the product for a team/org
  2. "Practitioner" — the IC who would actually use it day-to-day
  3. "Outsider"     — someone newer to this category or coming from an adjacent context

Each persona MUST be a JSON object with EXACTLY these fields (NO personal names — use role as identifier):
- id: string (kebab-case slug, e.g. "team-lead")
- role: string (job title — this is the persona's identifier)
- company: string (stage/size + context, e.g. "Series B fintech · 40-person team")
- archetype: one of "Evaluator", "Practitioner", "Outsider"
- goal: string (one sentence — what they want from this product)
- jtbd: string — formatted "When I ___, I want to ___ so I can ___"
- frustrations: array of exactly 3 strings (concrete pains they bring to the eval)
- success: array of exactly 2 strings (what "this worked" looks like for them)
- techSavvy: integer 1-5
- context: string (device, mindset, time-of-day — one sentence)
- quote: string (in-voice pull quote, 1 sentence, <140 chars)
- color: string (hex — will be overridden by the renderer, any hex is fine)

Anchor specificity to the URL and summary. A fintech tool gets finance-adjacent
roles; a dev tool gets engineer personas; a design tool gets design roles; etc.

Return ONLY the JSON array of 3 objects. Nothing else."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = next((b.text for b in reversed(response.content) if hasattr(b, "text")), "").strip()
    clean = raw.replace("```json", "").replace("```", "").strip()
    parsed = json.loads(clean)
    if not isinstance(parsed, list):
        raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")
    return parsed


async def generate_personas(url, summary, advisor=False):
    """Generate N=3 structured personas for the run. Retries once on parse
    failure, then falls back to DEFAULT_PERSONAS padding. Never raises — the
    PDF must always render."""
    last_err = None
    for attempt in (1, 2):
        try:
            raw = await _call_haiku(url, summary)
            normalized = _normalize_personas(raw)
            if len(normalized) >= 3:
                try:
                    from persona_library import save_generated
                    save_generated(url, normalized[:3])
                except Exception as e:  # noqa: BLE001
                    print(f"   ⚠️  persona library save failed: {e}")
                return normalized[:3]
            last_err = f"only {len(normalized)}/3 valid personas on attempt {attempt}"
            print(f"   ⚠️  persona generation: {last_err} — retrying" if attempt == 1 else f"   ⚠️  persona generation: {last_err}")
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            print(f"   ⚠️  persona generation attempt {attempt} failed: {e}")
    padded = _pad_with_defaults([], want=3)
    print(f"[persona] fallback — padding with DEFAULT_PERSONAS (got 0/3): {last_err}")
    return padded
