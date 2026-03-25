from __future__ import annotations

import logging
from typing import TYPE_CHECKING

try:
    import jsonschema
    _JSONSCHEMA_AVAILABLE = True
except ImportError:
    _JSONSCHEMA_AVAILABLE = False

if TYPE_CHECKING:
    from app.config import PipelineConfig

logger = logging.getLogger(__name__)


class SchemaValidator:
    """
    Validates incoming events against JSON Schema definitions.

    Each event type can have its own schema. Events with unknown types
    (no schema defined) are always considered valid — define a schema
    only for event types you want to enforce structure on.

    Schemas are defined per-pipeline in PIPELINES_JSON under "event_schemas":

        "event_schemas": {
          "order.created": {
            "type": "object",
            "required": ["event_id", "event_type", "payload"],
            "properties": {
              "event_id": {"type": "string"},
              "event_type": {"type": "string"},
              "payload": {
                "type": "object",
                "required": ["order_id", "amount"],
                "properties": {
                  "order_id": {"type": "string"},
                  "amount": {"type": "number", "minimum": 0}
                }
              }
            }
          }
        }
    """

    def __init__(self, schemas: dict[str, dict], event_type_field: str = "event_type") -> None:
        self._schemas = schemas
        self._event_type_field = event_type_field
        self._valid = 0
        self._invalid = 0

        if schemas and not _JSONSCHEMA_AVAILABLE:
            raise RuntimeError(
                "jsonschema package is required for schema validation. "
                "Add 'jsonschema' to requirements.txt."
            )

    @classmethod
    def from_pipeline(cls, pipeline: "PipelineConfig") -> "SchemaValidator":
        return cls(
            schemas=pipeline.event_schemas,
            event_type_field=pipeline.event_type_field,
        )

    def validate(self, event: dict) -> tuple[bool, str]:
        """
        Returns (is_valid, error_message).
        Events with no defined schema are always valid.
        """
        if not self._schemas:
            return True, ""

        event_type = self._get_event_type(event)
        schema = self._schemas.get(event_type) if event_type else None

        if schema is None:
            return True, ""  # No schema defined for this type — pass through

        try:
            jsonschema.validate(instance=event, schema=schema)
            self._valid += 1
            return True, ""
        except jsonschema.ValidationError as e:
            self._invalid += 1
            # e.message is the specific field error; e.path shows where in the doc
            path = ".".join(str(p) for p in e.path) or "root"
            error = f"{path}: {e.message}"
            logger.warning(
                "Event failed schema validation",
                extra={
                    "event_type": event_type,
                    "error": error,
                    "schema_path": path,
                },
            )
            return False, error

    def _get_event_type(self, event: dict) -> str | None:
        parts = self._event_type_field.split(".")
        current = event
        for part in parts:
            if not isinstance(current, dict):
                return None
            current = current.get(part)  # type: ignore[assignment]
            if current is None:
                return None
        return str(current) if current is not None else None

    @property
    def stats(self) -> dict:
        return {"schema_valid": self._valid, "schema_invalid": self._invalid}
