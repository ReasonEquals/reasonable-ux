import json
import os
from datetime import datetime
from urllib.parse import urlparse

from dotenv import load_dotenv

from _sanitize_extracted import sanitize_field
from agent_core import LLMAdapter

LIBRARY_PATH = os.path.join(os.path.dirname(__file__), "personas_library.json")


def _domain(url: str) -> str:
    return urlparse(url).netloc.replace(".", "_")


def _load() -> list:
    if not os.path.exists(LIBRARY_PATH):
        return []
    with open(LIBRARY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(records: list) -> None:
    with open(LIBRARY_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)


async def _enrich(url: str, persona: str, run_dir: str | None = None) -> dict | None:
    """Expand a one-liner inferred persona into a structured object via Haiku."""
    load_dotenv()
    safe_url = sanitize_field(url)
    safe_persona = sanitize_field(persona)
    try:
        adapter = LLMAdapter("anthropic")
        raw, _, _, _ = await adapter.complete(
            messages=[
                {"role": "system", "content": "You are a UX research specialist. Return only valid JSON. No markdown, no explanation."},
                {"role": "user", "content": f"""Expand this persona description into a structured persona object.

Persona: "{safe_persona}"
Product URL: {safe_url}

Return a JSON object with exactly these fields:
- name: string (short label, 2-4 words)
- description: string (1-2 sentences)
- goals: array of 3-5 strings (what they want to accomplish on this site)
- concerns: array of 3-5 strings (friction, doubts, or risks they perceive)

Return only the JSON object, nothing else."""},
            ],
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            metadata={"session_id": run_dir} if run_dir else None,
        )
        clean = raw.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except Exception as e:  # noqa: S110
        print(f"⚠️  Persona enrichment failed: {e}")
        return None


async def save_inferred(url: str, persona: str, run_dir: str) -> None:
    """Append a step-1 inferred persona string from a UX run, enriched to structured form."""
    enriched = await _enrich(url, persona, run_dir)
    records = _load()
    records.append({
        "type": "inferred",
        "url": url,
        "domain": _domain(url),
        "persona": persona,
        "enriched": enriched,
        "run_dir": run_dir,
        "timestamp": datetime.now().isoformat(),
    })
    _save(records)
    if enriched:
        print(f"🧬 Persona enriched: {enriched.get('name', persona)}")


def save_generated(url: str, personas: list) -> None:
    """Append a set of structured personas from generate_personas()."""
    records = _load()
    records.append({
        "type": "generated",
        "url": url,
        "domain": _domain(url),
        "personas": personas,
        "timestamp": datetime.now().isoformat(),
    })
    _save(records)


def load_for_domain(url: str) -> list:
    """Return all saved persona records for the given URL's domain."""
    domain = _domain(url)
    return [r for r in _load() if r["domain"] == domain]


def load_all() -> list:
    """Return all saved persona records."""
    return _load()
