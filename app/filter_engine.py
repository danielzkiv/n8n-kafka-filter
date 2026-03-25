from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

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


class FilterEngine:
    def __init__(
        self,
        rules: list[FilterRule],
        mode: Literal["any", "all", "none"],
    ) -> None:
        self._rules = rules
        self._mode = mode

    @classmethod
    def from_config(cls, rules_dicts: list[dict], mode: Literal["any", "all", "none"]) -> "FilterEngine":
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
        return cls(rules, mode)

    def should_forward(self, event: dict) -> tuple[bool, str]:
        """Return (should_forward, reason) for the given event."""
        if self._mode == "none" or not self._rules:
            return True, "no_filter"

        results: list[tuple[bool, str]] = []
        for rule in self._rules:
            matched, detail = self._evaluate(rule, event)
            results.append((matched, f"rule:{rule.rule_id}:{detail}"))

        if self._mode == "any":
            for matched, reason in results:
                if matched:
                    return True, reason
            return False, f"no_rules_matched ({len(results)} rules checked)"

        # mode == "all"
        for matched, reason in results:
            if not matched:
                return False, reason
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
        """Traverse nested dict using dot notation. Returns _MISSING if not found."""
        parts = dotted_path.split(".")
        current: Any = event
        for part in parts:
            if not isinstance(current, dict):
                return _MISSING
            current = current.get(part, _MISSING)
            if current is _MISSING:
                return _MISSING
        return current
