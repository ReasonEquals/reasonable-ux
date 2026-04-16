import json
import os
from datetime import datetime
from urllib.parse import urlparse

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


def save_inferred(url: str, persona: str, run_dir: str) -> None:
    """Append a step-1 inferred persona string from a UX run."""
    records = _load()
    records.append({
        "type": "inferred",
        "url": url,
        "domain": _domain(url),
        "persona": persona,
        "run_dir": run_dir,
        "timestamp": datetime.now().isoformat(),
    })
    _save(records)


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
