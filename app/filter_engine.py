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
    mode: Literal["any", "all"]
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
    Routes each incoming event by event_type:
    - event_type not in event body              → skip
    - event_type not configured                 → skip
    - event_type configured, rules match        → forward
    - event_type configured, rules don't match  → skip
    """

    def __init__(
        self,
        event_type_field: str = "event_type",
        event_filters: dict[str, _FilterSet] | None = None,
    ) -> None:
        self._event_type_field = event_type_field
        self._event_filters: dict[str, _FilterSet] = event_filters or {}

    @classmethod
    def from_pipeline(cls, pipeline: "PipelineConfig") -> "FilterEngine":
        event_filters = {
            event_type: _FilterSet(
                mode=ef.filter_mode,
                rules=_parse_rules(ef.filter_rules),
            )
            for event_type, ef in pipeline.event_filters.items()
        }
        return cls(
            event_type_field=pipeline.event_type_field,
            event_filters=event_filters,
        )

    def should_forward(self, event: dict) -> tuple[bool, str]:
        raw_type = self._resolve_field(event, self._event_type_field)

        if raw_type is _MISSING:
            return False, f"no_field:{self._event_type_field}"

        event_type = str(raw_type)
        filter_set = self._event_filters.get(event_type)

        if filter_set is None:
            return False, f"unknown_event_type:{event_type}"

        if not filter_set.rules:
            return False, f"{event_type}:no_rules"

        result, reason = self._apply_filter_set(filter_set, event)
        return result, f"{event_type}:{reason}"

    def _apply_filter_set(self, fs: _FilterSet, event: dict) -> tuple[bool, str]:
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
