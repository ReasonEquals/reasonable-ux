import asyncio

from persona_agent import evaluate
from personas import DEFAULT_PERSONAS, generate_personas


def _pad_to_three(personas: list) -> list:
    """Pad to 3 personas from DEFAULT_PERSONAS without id collisions.
    Defensive net around generate_personas, which already pads on its own
    failure paths — this catches partial success / caller-side shrinkage."""
    existing_ids = {p.get("id") for p in personas if isinstance(p, dict)}
    padded = list(personas)
    for fallback in DEFAULT_PERSONAS:
        if len(padded) >= 3:
            break
        if fallback["id"] in existing_ids:
            continue
        padded.append(fallback)
        existing_ids.add(fallback["id"])
    if len(padded) < 3:
        print(f"[persona] fallback — padding with DEFAULT_PERSONAS (got {len(personas)}/3)")
    return padded[:3]


async def orchestrate(url: str, report: list, use_static: bool = False, advisor: bool = False) -> list:
    """
    Generate (or load) 3 structured personas, then run evaluate() calls in
    batches of 2 with a 2-second pause between batches to avoid rate limits.
    Returns list of persona result dicts — each item is the full structured
    persona plus {score, key_findings, recommendations}, in original persona
    order. Downstream `_resolve_personas` keys off `id` + `color` to pick
    these up.
    """
    if use_static:
        personas = list(DEFAULT_PERSONAS)
        print(f"   Using {len(personas)} static personas")
    else:
        # Build a brief report summary for persona generation context
        first_impressions = [e.get("first_impression", "") for e in report if e.get("first_impression")]
        friction = []
        for entry in report:
            friction.extend(entry.get("friction_points", []))
        parts = []
        if first_impressions:
            parts.append(f"First impression: {first_impressions[-1]}")
        if friction:
            parts.append(f"Key friction points: {'; '.join(friction[:5])}")
        summary = " | ".join(parts) if parts else "No summary available."

        print("   Generating contextual personas...")
        personas = await generate_personas(url, summary, advisor=advisor)
        print(f"   Generated {len(personas)} personas")

    personas = _pad_to_three(personas)

    print(f"   Running {len(personas)} persona evaluations in batches of 2...")
    results = []
    for i in range(0, len(personas), 2):
        batch = personas[i:i + 2]
        batch_results = await asyncio.gather(*[evaluate(p, report, url, advisor=advisor) for p in batch])
        results.extend(batch_results)
        if i + 2 < len(personas):
            await asyncio.sleep(2)
    return results
