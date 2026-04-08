import asyncio
from personas import DEFAULT_PERSONAS, generate_personas
from persona_agent import evaluate


async def orchestrate(url: str, report: list, use_static: bool = False) -> list:
    """
    Generate (or load) personas, then run all evaluate() calls in parallel.
    Returns list of persona result dicts.
    """
    if use_static:
        personas = DEFAULT_PERSONAS
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
        personas = await generate_personas(url, summary)
        print(f"   Generated {len(personas)} personas")

    print(f"   Running {len(personas)} persona evaluations in parallel...")
    results = await asyncio.gather(*[evaluate(p, report, url) for p in personas])
    return list(results)
