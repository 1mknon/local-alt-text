"""Manifest: track last run of major operations."""
import json
from datetime import datetime
from .paths import MANIFEST_JSON


def load():
    try:
        with open(MANIFEST_JSON, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def update(section, **data):
    m = load()
    m[section] = {"ts": datetime.now().isoformat(timespec="seconds"), **data}
    MANIFEST_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_JSON, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2, ensure_ascii=False)
