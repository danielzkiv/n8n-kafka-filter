"""
Persists filter configuration to filter_config.json.
This lets the UI update filters at runtime without restarting or editing PIPELINES_JSON.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

_PATH = Path("filter_config.json")
_lock = threading.Lock()


def load() -> dict:
    with _lock:
        if not _PATH.exists():
            return {}
        try:
            return json.loads(_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}


def save(data: dict) -> None:
    with _lock:
        _PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_pipeline(name: str) -> dict | None:
    return load().get(name)


def set_pipeline(name: str, filters: dict) -> None:
    data = load()
    data[name] = filters
    save(data)
