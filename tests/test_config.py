import json
import pytest
from app.config import EventFilterConfig, PipelineConfig, Settings


def make_pipeline_dict(**overrides) -> dict:
    base = {
        "name": "dev",
        "kafka_bootstrap_servers": "broker:9092",
        "kafka_topics": ["dev-events"],
        "n8n_webhook_url": "https://n8n.example.com/webhook/abc",
        "filter_mode": "none",
        "filter_rules": [],
    }
    base.update(overrides)
    return base


# --- PipelineConfig ---

def test_pipeline_parses_topics_from_string():
    p = PipelineConfig(**make_pipeline_dict(kafka_topics="topic-a,topic-b , topic-c"))
    assert p.kafka_topics == ["topic-a", "topic-b", "topic-c"]


def test_pipeline_parses_topics_from_list():
    p = PipelineConfig(**make_pipeline_dict(kafka_topics=["topic-a", "topic-b"]))
    assert p.kafka_topics == ["topic-a", "topic-b"]


def test_pipeline_empty_topics_raises():
    with pytest.raises(Exception, match="at least one topic"):
        PipelineConfig(**make_pipeline_dict(kafka_topics=""))


def test_pipeline_defaults():
    p = PipelineConfig(**make_pipeline_dict())
    assert p.kafka_consumer_group_id == "kafka-n8n-forwarder"
    assert p.filter_mode == "none"
    assert p.filter_rules == []
    assert p.webhook_max_retries == 3


# --- Settings.get_pipelines ---

def _make_settings(pipelines: list[dict], **kwargs) -> Settings:
    return Settings(
        pipelines_json=json.dumps(pipelines),
        **kwargs,
    )


def test_settings_parses_three_pipelines():
    pipelines = [
        make_pipeline_dict(name="dev"),
        make_pipeline_dict(name="stage"),
        make_pipeline_dict(name="prod"),
    ]
    s = _make_settings(pipelines)
    result = s.get_pipelines()
    assert len(result) == 3
    assert [p.name for p in result] == ["dev", "stage", "prod"]


def test_settings_duplicate_names_raises():
    pipelines = [make_pipeline_dict(name="dev"), make_pipeline_dict(name="dev")]
    s = _make_settings(pipelines)
    with pytest.raises(ValueError, match="unique"):
        s.get_pipelines()


def test_settings_empty_pipelines_raises():
    with pytest.raises(Exception):
        Settings(pipelines_json="[]")


def test_settings_invalid_json_raises():
    with pytest.raises(Exception):
        Settings(pipelines_json="not-json")


def test_pipeline_with_filter_rules():
    rules = [{"rule_id": "r1", "field": "type", "operator": "eq", "value": "order"}]
    p = PipelineConfig(**make_pipeline_dict(filter_mode="any", filter_rules=rules))
    assert p.filter_mode == "any"
    assert len(p.filter_rules) == 1
    assert p.filter_rules[0]["rule_id"] == "r1"


def test_pipeline_event_filters():
    p = PipelineConfig(**make_pipeline_dict(
        event_type_field="event_type",
        event_filters={
            "order.created": {"filter_mode": "all", "filter_rules": [
                {"rule_id": "r1", "field": "amount", "operator": "gte", "value": 100}
            ]},
            "test.ping": {"filter_mode": "drop", "filter_rules": []},
        },
        default_filter_mode="drop",
    ))
    assert len(p.event_filters) == 2
    assert p.event_filters["order.created"].filter_mode == "all"
    assert p.event_filters["test.ping"].filter_mode == "drop"
    assert p.default_filter_mode == "drop"


def test_event_filter_config_defaults():
    ef = EventFilterConfig()
    assert ef.filter_mode == "none"
    assert ef.filter_rules == []
