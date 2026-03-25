from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import PipelineConfig

logger = logging.getLogger(__name__)

_MISSING = object()

SUPPORTED_OPERATORS = {"eq", "neq", "contains", "in", "exists", "regex", "gt", "gte", "lt", "lte"}


@dataclass
class FilterRule:
    rule_id: str
    field: str
    operator: str
    value: Any

    def __post_init__(self) -> None:
        if self.operator not in SUPPORTED_OPERATORS:
            raise ValueError(
                f"Rule '{self.rule_id}': unsupported operator '{self.operator}'. "
                f"Must be one of: {sorted(SUPPORTED_OPERATORS)}"
            )


@dataclass
class _FilterSet:
    """A self-contained filter: a mode + its rules."""
    mode: Literal["none", "any", "all", "drop"]
    rules: list[FilterRule] = field(default_factory=list)


def _parse_rules(rules_dicts: list[dict]) -> list[FilterRule]:
    rules = []
    for raw in rules_dicts:
        try:
            rules.append(FilterRule(
                rule_id=raw["rule_id"],
                field=raw["field"],
                operator=raw["operator"],
                value=raw.get("value"),
            ))
        except KeyError as e:
            raise ValueError(f"Filter rule missing required key: {e}") from e
    return rules


class FilterEngine:
    """
    Routes each incoming event to the right filter set based on event type.

    Two modes of operation:
    1. Global (simple): one filter_mode + filter_rules applied to every event.
    2. Per-event-type (routing): reads `event_type_field` from the event,
       looks up the matching EventFilterConfig, and applies its rules.
       Unknown event types fall back to `default_filter_mode`.
    """

    def __init__(
        self,
        global_filter: _FilterSet,
        event_type_field: str = "event_type",
        event_filters: dict[str, _FilterSet] | None = None,
        default_filter_mode: Literal["none", "drop"] = "none",
    ) -> None:
        self._global = global_filter
        self._event_type_field = event_type_field
        self._event_filters: dict[str, _FilterSet] = event_filters or {}
        self._default_filter_mode = default_filter_mode

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_pipeline(cls, pipeline: "PipelineConfig") -> "FilterEngine":
        """Primary factory — builds from a full PipelineConfig."""
        global_filter = _FilterSet(
            mode=pipeline.filter_mode,
            rules=_parse_rules(pipeline.filter_rules),
        )
        event_filters = {
            event_type: _FilterSet(
                mode=ef.filter_mode,
                rules=_parse_rules(ef.filter_rules),
            )
            for event_type, ef in pipeline.event_filters.items()
        }
        return cls(
            global_filter=global_filter,
            event_type_field=pipeline.event_type_field,
            event_filters=event_filters,
            default_filter_mode=pipeline.default_filter_mode,
        )

    @classmethod
    def from_config(cls, rules_dicts: list[dict], mode: Literal["any", "all", "none"]) -> "FilterEngine":
        """Backwards-compatible factory for tests and simple use cases."""
        return cls(global_filter=_FilterSet(mode=mode, rules=_parse_rules(rules_dicts)))

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def should_forward(self, event: dict) -> tuple[bool, str]:
        """Return (should_forward, reason_string) for the given event."""
        if self._event_filters:
            return self._route(event)
        return self._apply_filter_set(self._global, event)

    # ------------------------------------------------------------------
    # Routing logic
    # ------------------------------------------------------------------

    def _route(self, event: dict) -> tuple[bool, str]:
        raw_type = self._resolve_field(event, self._event_type_field)

        if raw_type is _MISSING:
            return self._apply_default(f"field_missing:{self._event_type_field}")

        event_type = str(raw_type)
        filter_set = self._event_filters.get(event_type)

        if filter_set is None:
            return self._apply_default(f"unknown_event_type:{event_type}")

        result, reason = self._apply_filter_set(filter_set, event)
        return result, f"event_type:{event_type}:{reason}"

    def _apply_default(self, context: str) -> tuple[bool, str]:
        if self._default_filter_mode == "drop":
            return False, f"{context}:default_drop"
        return True, f"{context}:default_forward"

    # ------------------------------------------------------------------
    # Filter set evaluation
    # ------------------------------------------------------------------

    def _apply_filter_set(self, fs: _FilterSet, event: dict) -> tuple[bool, str]:
        if fs.mode == "drop":
            return False, "drop"

        if fs.mode == "none" or not fs.rules:
            return True, "no_filter"

        results = [(self._evaluate(rule, event), f"rule:{rule.rule_id}") for rule in fs.rules]

        if fs.mode == "any":
            for (matched, detail), label in results:
                if matched:
                    return True, f"{label}:{detail}"
            return False, f"no_rules_matched ({len(results)} checked)"

        # mode == "all"
        for (matched, detail), label in results:
            if not matched:
                return False, f"{label}:{detail}"
        return True, "all_rules_matched"

    def _evaluate(self, rule: FilterRule, event: dict) -> tuple[bool, str]:
        field_value = self._resolve_field(event, rule.field)

        if rule.operator == "exists":
            matched = field_value is not _MISSING and field_value is not None
            return matched, "exists" if matched else "field_missing_or_null"

        if field_value is _MISSING:
            return False, f"field_not_found:{rule.field}"

        op = rule.operator
        try:
            if op == "eq":
                return field_value == rule.value, f"{field_value!r}=={rule.value!r}"
            if op == "neq":
                return field_value != rule.value, f"{field_value!r}!={rule.value!r}"
            if op == "contains":
                if isinstance(field_value, str):
                    matched = str(rule.value) in field_value
                elif isinstance(field_value, (list, tuple, set)):
                    matched = rule.value in field_value
                else:
                    matched = False
                return matched, f"contains:{rule.value!r}"
            if op == "in":
                if not isinstance(rule.value, list):
                    raise ValueError(f"Rule '{rule.rule_id}': 'in' operator requires a list value")
                return field_value in rule.value, f"{field_value!r} in {rule.value!r}"
            if op == "regex":
                matched = bool(re.search(str(rule.value), str(field_value)))
                return matched, f"regex:{rule.value!r}"
            if op == "gt":
                return float(field_value) > float(rule.value), f"{field_value}>{rule.value}"
            if op == "gte":
                return float(field_value) >= float(rule.value), f"{field_value}>={rule.value}"
            if op == "lt":
                return float(field_value) < float(rule.value), f"{field_value}<{rule.value}"
            if op == "lte":
                return float(field_value) <= float(rule.value), f"{field_value}<={rule.value}"
        except (TypeError, ValueError) as e:
            logger.warning("Filter evaluation error", extra={"rule_id": rule.rule_id, "error": str(e)})
            return False, f"evaluation_error:{e}"

        return False, "unknown_operator"

    @staticmethod
    def _resolve_field(event: dict, dotted_path: str) -> Any:
        parts = dotted_path.split(".")
        current: Any = event
        for part in parts:
            if not isinstance(current, dict):
                return _MISSING
            current = current.get(part, _MISSING)
            if current is _MISSING:
                return _MISSING
        return current
