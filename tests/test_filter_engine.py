import pytest
from app.filter_engine import FilterEngine, FilterRule, _FilterSet


def make_engine(rules_dicts: list[dict], mode: str = "any") -> FilterEngine:
    return FilterEngine.from_config(rules_dicts, mode)


# --- Basic operator tests ---

def test_eq_match():
    engine = make_engine([{"rule_id": "r1", "field": "event_type", "operator": "eq", "value": "order.created"}])
    ok, _ = engine.should_forward({"event_type": "order.created"})
    assert ok


def test_eq_no_match():
    engine = make_engine([{"rule_id": "r1", "field": "event_type", "operator": "eq", "value": "order.created"}])
    ok, _ = engine.should_forward({"event_type": "other"})
    assert not ok


def test_neq():
    engine = make_engine([{"rule_id": "r1", "field": "event_type", "operator": "neq", "value": "test.ping"}])
    ok, _ = engine.should_forward({"event_type": "order.created"})
    assert ok
    ok2, _ = engine.should_forward({"event_type": "test.ping"})
    assert not ok2


def test_contains_string():
    engine = make_engine([{"rule_id": "r1", "field": "msg", "operator": "contains", "value": "hello"}])
    assert engine.should_forward({"msg": "say hello world"})[0]
    assert not engine.should_forward({"msg": "goodbye"})[0]


def test_contains_list():
    engine = make_engine([{"rule_id": "r1", "field": "tags", "operator": "contains", "value": "vip"}])
    assert engine.should_forward({"tags": ["vip", "premium"]})[0]
    assert not engine.should_forward({"tags": ["basic"]})[0]


def test_in_operator():
    engine = make_engine([
        {"rule_id": "r1", "field": "status", "operator": "in", "value": ["active", "pending"]}
    ])
    assert engine.should_forward({"status": "active"})[0]
    assert not engine.should_forward({"status": "closed"})[0]


def test_exists_present():
    engine = make_engine([{"rule_id": "r1", "field": "customer_id", "operator": "exists", "value": None}])
    assert engine.should_forward({"customer_id": "123"})[0]
    assert not engine.should_forward({"other": "field"})[0]
    assert not engine.should_forward({"customer_id": None})[0]


def test_regex():
    engine = make_engine([{"rule_id": "r1", "field": "id", "operator": "regex", "value": r"^EVT-\d+$"}])
    assert engine.should_forward({"id": "EVT-12345"})[0]
    assert not engine.should_forward({"id": "ID-12345"})[0]


def test_numeric_gt():
    engine = make_engine([{"rule_id": "r1", "field": "amount", "operator": "gt", "value": 100}])
    assert engine.should_forward({"amount": 150})[0]
    assert not engine.should_forward({"amount": 100})[0]
    assert not engine.should_forward({"amount": 50})[0]


def test_numeric_gte():
    engine = make_engine([{"rule_id": "r1", "field": "amount", "operator": "gte", "value": 100}])
    assert engine.should_forward({"amount": 100})[0]
    assert engine.should_forward({"amount": 101})[0]
    assert not engine.should_forward({"amount": 99})[0]


def test_numeric_lt_lte():
    engine_lt = make_engine([{"rule_id": "r1", "field": "score", "operator": "lt", "value": 5}])
    engine_lte = make_engine([{"rule_id": "r1", "field": "score", "operator": "lte", "value": 5}])
    assert engine_lt.should_forward({"score": 4})[0]
    assert not engine_lt.should_forward({"score": 5})[0]
    assert engine_lte.should_forward({"score": 5})[0]


# --- Dot notation ---

def test_nested_field():
    engine = make_engine([{"rule_id": "r1", "field": "payload.customer.tier", "operator": "eq", "value": "gold"}])
    assert engine.should_forward({"payload": {"customer": {"tier": "gold"}}})[0]
    assert not engine.should_forward({"payload": {"customer": {"tier": "silver"}}})[0]
    assert not engine.should_forward({"payload": {}})[0]


# --- Mode tests ---

def test_mode_none_forwards_all():
    engine = FilterEngine.from_config([], "none")
    assert engine.should_forward({})[0]
    assert engine.should_forward({"anything": "here"})[0]


def test_mode_any():
    rules = [
        {"rule_id": "r1", "field": "type", "operator": "eq", "value": "A"},
        {"rule_id": "r2", "field": "type", "operator": "eq", "value": "B"},
    ]
    engine = make_engine(rules, mode="any")
    assert engine.should_forward({"type": "A"})[0]
    assert engine.should_forward({"type": "B"})[0]
    assert not engine.should_forward({"type": "C"})[0]


def test_mode_all():
    rules = [
        {"rule_id": "r1", "field": "type", "operator": "eq", "value": "order"},
        {"rule_id": "r2", "field": "amount", "operator": "gte", "value": 100},
    ]
    engine = make_engine(rules, mode="all")
    assert engine.should_forward({"type": "order", "amount": 200})[0]
    assert not engine.should_forward({"type": "order", "amount": 50})[0]
    assert not engine.should_forward({"type": "payment", "amount": 200})[0]


# --- Edge cases ---

def test_missing_field_returns_false():
    engine = make_engine([{"rule_id": "r1", "field": "missing_field", "operator": "eq", "value": "x"}])
    ok, reason = engine.should_forward({"other": "value"})
    assert not ok
    assert "field_not_found" in reason or "no_rules_matched" in reason


def test_invalid_operator_raises():
    with pytest.raises(ValueError, match="unsupported operator"):
        FilterRule(rule_id="r1", field="f", operator="unknown_op", value="x")


def test_reason_string_is_informative():
    engine = make_engine([{"rule_id": "high-value", "field": "amount", "operator": "gte", "value": 500}])
    ok, reason = engine.should_forward({"amount": 1000})
    assert ok
    assert "high-value" in reason


# --- Per-event-type routing ---

def make_routing_engine(event_filters: dict, default: str = "none", event_type_field: str = "event_type") -> FilterEngine:
    """Build a FilterEngine with per-event-type routing."""
    parsed = {
        et: _FilterSet(
            mode=cfg.get("filter_mode", "none"),
            rules=[FilterRule(**r) for r in cfg.get("filter_rules", [])],
        )
        for et, cfg in event_filters.items()
    }
    return FilterEngine(
        global_filter=_FilterSet(mode="none"),
        event_type_field=event_type_field,
        event_filters=parsed,
        default_filter_mode=default,
    )


def test_routing_forwards_matching_event_type():
    engine = make_routing_engine({
        "order.created": {"filter_mode": "none", "filter_rules": []},
    })
    ok, _ = engine.should_forward({"event_type": "order.created", "amount": 50})
    assert ok


def test_routing_drops_excluded_event_type():
    engine = make_routing_engine({
        "test.ping": {"filter_mode": "drop", "filter_rules": []},
    }, default="none")
    ok, reason = engine.should_forward({"event_type": "test.ping"})
    assert not ok
    assert "drop" in reason


def test_routing_applies_rules_per_event_type():
    engine = make_routing_engine({
        "order.created": {
            "filter_mode": "all",
            "filter_rules": [
                {"rule_id": "min-amount", "field": "amount", "operator": "gte", "value": 100},
            ],
        },
        "user.signup": {"filter_mode": "none", "filter_rules": []},
    })
    # order.created passes only when amount >= 100
    assert engine.should_forward({"event_type": "order.created", "amount": 200})[0]
    assert not engine.should_forward({"event_type": "order.created", "amount": 10})[0]
    # user.signup always passes
    assert engine.should_forward({"event_type": "user.signup"})[0]


def test_routing_unknown_type_default_forward():
    engine = make_routing_engine(
        {"order.created": {"filter_mode": "none"}},
        default="none",
    )
    ok, reason = engine.should_forward({"event_type": "unknown.event"})
    assert ok
    assert "default_forward" in reason


def test_routing_unknown_type_default_drop():
    engine = make_routing_engine(
        {"order.created": {"filter_mode": "none"}},
        default="drop",
    )
    ok, reason = engine.should_forward({"event_type": "unknown.event"})
    assert not ok
    assert "default_drop" in reason


def test_routing_missing_event_type_field():
    engine = make_routing_engine(
        {"order.created": {"filter_mode": "none"}},
        default="drop",
    )
    # Event has no event_type field at all
    ok, reason = engine.should_forward({"amount": 50})
    assert not ok
    assert "field_missing" in reason


def test_routing_custom_event_type_field():
    engine = make_routing_engine(
        {"ORDER": {"filter_mode": "none"}},
        event_type_field="type",
    )
    assert engine.should_forward({"type": "ORDER", "id": 1})[0]
    assert not engine.should_forward({"event_type": "ORDER"})[0]  # wrong field


def test_routing_nested_event_type_field():
    engine = make_routing_engine(
        {"order.created": {"filter_mode": "none"}},
        event_type_field="metadata.event_type",
    )
    assert engine.should_forward({"metadata": {"event_type": "order.created"}})[0]


def test_routing_reason_includes_event_type():
    engine = make_routing_engine({
        "payment.done": {"filter_mode": "none"},
    })
    ok, reason = engine.should_forward({"event_type": "payment.done"})
    assert ok
    assert "payment.done" in reason


def test_multiple_event_types_independent_rules():
    """Each event type has its own completely different rules."""
    engine = make_routing_engine({
        "order.created": {
            "filter_mode": "all",
            "filter_rules": [{"rule_id": "r1", "field": "amount", "operator": "gte", "value": 100}],
        },
        "user.signup": {
            "filter_mode": "any",
            "filter_rules": [{"rule_id": "r2", "field": "country", "operator": "eq", "value": "US"}],
        },
        "test.ping": {"filter_mode": "drop"},
    }, default="drop")

    assert engine.should_forward({"event_type": "order.created", "amount": 500})[0]
    assert not engine.should_forward({"event_type": "order.created", "amount": 5})[0]
    assert engine.should_forward({"event_type": "user.signup", "country": "US"})[0]
    assert not engine.should_forward({"event_type": "user.signup", "country": "DE"})[0]
    assert not engine.should_forward({"event_type": "test.ping"})[0]
    assert not engine.should_forward({"event_type": "anything.else"})[0]  # default drop
