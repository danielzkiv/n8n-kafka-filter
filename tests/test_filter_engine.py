import pytest
from app.filter_engine import FilterEngine, FilterRule, _FilterSet


def make_engine(event_type: str, rules_dicts: list[dict], mode: str = "any") -> FilterEngine:
    """Build a FilterEngine with one event type configured."""
    return FilterEngine(
        event_type_field="event_type",
        event_filters={
            event_type: _FilterSet(
                mode=mode,
                rules=[FilterRule(**r) for r in rules_dicts],
            )
        },
    )


# --- Unknown / missing event type ---

def test_unknown_event_type_skipped():
    engine = make_engine("order.created", [{"rule_id": "r1", "field": "amount", "operator": "gt", "value": 0}])
    ok, reason = engine.should_forward({"event_type": "other.event", "amount": 100})
    assert not ok
    assert "unknown_event_type" in reason


def test_missing_event_type_field_skipped():
    engine = make_engine("order.created", [{"rule_id": "r1", "field": "amount", "operator": "gt", "value": 0}])
    ok, reason = engine.should_forward({"amount": 100})
    assert not ok
    assert "no_field" in reason


def test_no_rules_configured_skipped():
    engine = make_engine("order.created", [])
    ok, reason = engine.should_forward({"event_type": "order.created"})
    assert not ok
    assert "no_rules" in reason


# --- Basic operators ---

def test_eq_match():
    engine = make_engine("order.created", [{"rule_id": "r1", "field": "status", "operator": "eq", "value": "paid"}])
    ok, _ = engine.should_forward({"event_type": "order.created", "status": "paid"})
    assert ok


def test_eq_no_match():
    engine = make_engine("order.created", [{"rule_id": "r1", "field": "status", "operator": "eq", "value": "paid"}])
    ok, _ = engine.should_forward({"event_type": "order.created", "status": "pending"})
    assert not ok


def test_neq():
    engine = make_engine("order.created", [{"rule_id": "r1", "field": "env", "operator": "neq", "value": "test"}])
    assert engine.should_forward({"event_type": "order.created", "env": "prod"})[0]
    assert not engine.should_forward({"event_type": "order.created", "env": "test"})[0]


def test_contains_string():
    engine = make_engine("msg.received", [{"rule_id": "r1", "field": "msg", "operator": "contains", "value": "hello"}])
    assert engine.should_forward({"event_type": "msg.received", "msg": "say hello world"})[0]
    assert not engine.should_forward({"event_type": "msg.received", "msg": "goodbye"})[0]


def test_contains_list():
    engine = make_engine("user.action", [{"rule_id": "r1", "field": "tags", "operator": "contains", "value": "vip"}])
    assert engine.should_forward({"event_type": "user.action", "tags": ["vip", "premium"]})[0]
    assert not engine.should_forward({"event_type": "user.action", "tags": ["basic"]})[0]


def test_in_operator():
    engine = make_engine("order.created", [
        {"rule_id": "r1", "field": "status", "operator": "in", "value": ["active", "pending"]}
    ])
    assert engine.should_forward({"event_type": "order.created", "status": "active"})[0]
    assert not engine.should_forward({"event_type": "order.created", "status": "closed"})[0]


def test_exists_present():
    engine = make_engine("order.created", [{"rule_id": "r1", "field": "customer_id", "operator": "exists", "value": None}])
    assert engine.should_forward({"event_type": "order.created", "customer_id": "123"})[0]
    assert not engine.should_forward({"event_type": "order.created", "other": "field"})[0]
    assert not engine.should_forward({"event_type": "order.created", "customer_id": None})[0]


def test_regex():
    engine = make_engine("order.created", [{"rule_id": "r1", "field": "id", "operator": "regex", "value": r"^EVT-\d+$"}])
    assert engine.should_forward({"event_type": "order.created", "id": "EVT-12345"})[0]
    assert not engine.should_forward({"event_type": "order.created", "id": "ID-12345"})[0]


def test_numeric_gt():
    engine = make_engine("order.created", [{"rule_id": "r1", "field": "amount", "operator": "gt", "value": 100}])
    assert engine.should_forward({"event_type": "order.created", "amount": 150})[0]
    assert not engine.should_forward({"event_type": "order.created", "amount": 100})[0]
    assert not engine.should_forward({"event_type": "order.created", "amount": 50})[0]


def test_numeric_gte():
    engine = make_engine("order.created", [{"rule_id": "r1", "field": "amount", "operator": "gte", "value": 100}])
    assert engine.should_forward({"event_type": "order.created", "amount": 100})[0]
    assert engine.should_forward({"event_type": "order.created", "amount": 101})[0]
    assert not engine.should_forward({"event_type": "order.created", "amount": 99})[0]


def test_numeric_lt_lte():
    engine_lt  = make_engine("score.updated", [{"rule_id": "r1", "field": "score", "operator": "lt",  "value": 5}])
    engine_lte = make_engine("score.updated", [{"rule_id": "r1", "field": "score", "operator": "lte", "value": 5}])
    assert engine_lt.should_forward({"event_type": "score.updated", "score": 4})[0]
    assert not engine_lt.should_forward({"event_type": "score.updated", "score": 5})[0]
    assert engine_lte.should_forward({"event_type": "score.updated", "score": 5})[0]


# --- Dot notation ---

def test_nested_field():
    engine = make_engine("order.created", [{"rule_id": "r1", "field": "payload.customer.tier", "operator": "eq", "value": "gold"}])
    assert engine.should_forward({"event_type": "order.created", "payload": {"customer": {"tier": "gold"}}})[0]
    assert not engine.should_forward({"event_type": "order.created", "payload": {"customer": {"tier": "silver"}}})[0]
    assert not engine.should_forward({"event_type": "order.created", "payload": {}})[0]


# --- Match modes ---

def test_mode_any():
    engine = make_engine("order.created", [
        {"rule_id": "r1", "field": "status", "operator": "eq", "value": "paid"},
        {"rule_id": "r2", "field": "status", "operator": "eq", "value": "refunded"},
    ], mode="any")
    assert engine.should_forward({"event_type": "order.created", "status": "paid"})[0]
    assert engine.should_forward({"event_type": "order.created", "status": "refunded"})[0]
    assert not engine.should_forward({"event_type": "order.created", "status": "pending"})[0]


def test_mode_all():
    engine = make_engine("order.created", [
        {"rule_id": "r1", "field": "status", "operator": "eq", "value": "paid"},
        {"rule_id": "r2", "field": "amount", "operator": "gte", "value": 100},
    ], mode="all")
    assert engine.should_forward({"event_type": "order.created", "status": "paid", "amount": 200})[0]
    assert not engine.should_forward({"event_type": "order.created", "status": "paid", "amount": 50})[0]
    assert not engine.should_forward({"event_type": "order.created", "status": "pending", "amount": 200})[0]


# --- Multiple event types ---

def test_multiple_event_types_independent_rules():
    engine = FilterEngine(
        event_type_field="event_type",
        event_filters={
            "order.created": _FilterSet(mode="all", rules=[
                FilterRule(rule_id="r1", field="amount", operator="gte", value=100),
            ]),
            "user.signup": _FilterSet(mode="any", rules=[
                FilterRule(rule_id="r2", field="country", operator="eq", value="US"),
            ]),
        },
    )
    assert engine.should_forward({"event_type": "order.created", "amount": 500})[0]
    assert not engine.should_forward({"event_type": "order.created", "amount": 5})[0]
    assert engine.should_forward({"event_type": "user.signup", "country": "US"})[0]
    assert not engine.should_forward({"event_type": "user.signup", "country": "DE"})[0]
    assert not engine.should_forward({"event_type": "anything.else"})[0]


def test_custom_event_type_field():
    engine = FilterEngine(
        event_type_field="type",
        event_filters={
            "ORDER": _FilterSet(mode="any", rules=[
                FilterRule(rule_id="r1", field="amount", operator="gt", value=0),
            ]),
        },
    )
    assert engine.should_forward({"type": "ORDER", "amount": 10})[0]
    assert not engine.should_forward({"event_type": "ORDER", "amount": 10})[0]  # wrong field


def test_nested_event_type_field():
    engine = FilterEngine(
        event_type_field="metadata.event_type",
        event_filters={
            "order.created": _FilterSet(mode="any", rules=[
                FilterRule(rule_id="r1", field="amount", operator="gt", value=0),
            ]),
        },
    )
    assert engine.should_forward({"metadata": {"event_type": "order.created"}, "amount": 10})[0]


# --- Edge cases ---

def test_missing_field_returns_false():
    engine = make_engine("order.created", [{"rule_id": "r1", "field": "missing_field", "operator": "eq", "value": "x"}])
    ok, reason = engine.should_forward({"event_type": "order.created", "other": "value"})
    assert not ok


def test_invalid_operator_raises():
    with pytest.raises(ValueError, match="unsupported operator"):
        FilterRule(rule_id="r1", field="f", operator="unknown_op", value="x")


def test_reason_includes_event_type_and_rule():
    engine = make_engine("order.created", [{"rule_id": "high-value", "field": "amount", "operator": "gte", "value": 500}])
    ok, reason = engine.should_forward({"event_type": "order.created", "amount": 1000})
    assert ok
    assert "order.created" in reason
    assert "high-value" in reason
