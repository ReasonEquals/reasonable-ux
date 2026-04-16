import json
import os
from datetime import datetime
from urllib.parse import urlparse

from anthropic import Anthropic
from dotenv import load_dotenv

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


def _enrich(url: str, persona: str) -> dict | None:
    """Expand a one-liner inferred persona into a structured object via Haiku."""
    load_dotenv()
    try:
        client = Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system="You are a UX research specialist. Return only valid JSON. No markdown, no explanation.",
            messages=[{"role": "user", "content": f"""Expand this persona description into a structured persona object.

Persona: "{persona}"
Product URL: {url}

Return a JSON object with exactly these fields:
- name: string (short label, 2-4 words)
- description: string (1-2 sentences)
- goals: array of 3-5 strings (what they want to accomplish on this site)
- concerns: array of 3-5 strings (friction, doubts, or risks they perceive)

Return only the JSON object, nothing else."""}],
        )
        raw = response.content[0].text.strip()
        clean = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except Exception as e:  # noqa: S110
        print(f"⚠️  Persona enrichment failed: {e}")
        return None


def save_inferred(url: str, persona: str, run_dir: str) -> None:
    """Append a step-1 inferred persona string from a UX run, enriched to structured form."""
    enriched = _enrich(url, persona)
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
