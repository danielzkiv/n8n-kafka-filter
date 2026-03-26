"""
Persists filter configuration to filter_config.json.
When GCS_BUCKET env var is set, uses Google Cloud Storage so config
survives redeployments. Falls back to a local file otherwise (dev/local use).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_PATH = Path("filter_config.json")
_lock = threading.Lock()
_GCS_BUCKET = os.environ.get("GCS_BUCKET")
_GCS_OBJECT = os.environ.get("GCS_FILTER_CONFIG_OBJECT", "filter_config.json")


def _gcs_load() -> dict:
    from google.cloud import storage
    from google.cloud.exceptions import NotFound
    client = storage.Client()
    blob = client.bucket(_GCS_BUCKET).blob(_GCS_OBJECT)
    try:
        return json.loads(blob.download_as_text())
    except NotFound:
        return {}


def _gcs_save(data: dict) -> None:
    from google.cloud import storage
    client = storage.Client()
    blob = client.bucket(_GCS_BUCKET).blob(_GCS_OBJECT)
    blob.upload_from_string(json.dumps(data, indent=2), content_type="application/json")


def load() -> dict:
    with _lock:
        if _GCS_BUCKET:
            try:
                return _gcs_load()
            except Exception:
                return {}
        if not _PATH.exists():
            return {}
        try:
            return json.loads(_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}


def save(data: dict) -> None:
    with _lock:
        if _GCS_BUCKET:
            _gcs_save(data)
        else:
            _PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_pipeline(name: str) -> dict | None:
    return load().get(name)


def set_pipeline(name: str, filters: dict) -> None:
    data = load()
    data[name] = filters
    save(data)
