from __future__ import annotations

import json
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EventFilterConfig(BaseModel):
    """Filter config for a single event type."""
    # "any" — forward if at least one rule matches
    # "all" — forward only if every rule matches
    filter_mode: Literal["any", "all"] = "any"
    filter_rules: list[dict] = []


class PipelineConfig(BaseModel):
    """Config for one environment's full pipeline: events → Filter → n8n webhook.

    Two ingestion modes (can be combined):
    - Kafka consumer: set kafka_bootstrap_servers + kafka_topics
    - HTTP ingest:    set ingest_secret; Lambda POSTs to POST /ingest/{name}
    """

    name: str  # "dev" | "stage" | "prod" — used in logs and metrics

    # --- HTTP ingest (Lambda → Cloud Run) ---
    # Secret Lambda sends in X-Ingest-Secret header.
    # If None, the /ingest/{env} endpoint rejects all requests.
    ingest_secret: SecretStr | None = None

    # --- Kafka (optional — omit when using HTTP ingest only) ---
    kafka_bootstrap_servers: str | None = None
    kafka_topics: list[str] = []
    kafka_consumer_group_id: str = "kafka-n8n-forwarder"
    kafka_security_protocol: str = "SASL_SSL"
    kafka_sasl_mechanism: str = "PLAIN"
    kafka_sasl_username: str | None = None
    kafka_sasl_password: SecretStr | None = None
    kafka_auto_offset_reset: str = "earliest"

    # --- Webhook ---
    n8n_webhook_url: AnyHttpUrl
    n8n_webhook_secret: SecretStr | None = None  # Sent as X-Webhook-Secret header
    webhook_timeout_seconds: int = 10
    webhook_max_retries: int = 3
    webhook_retry_backoff_seconds: float = 2.0   # Exponential base (capped at 30s)

    # --- Per-event-type filtering ---
    # Which field in the event body identifies its type.
    event_type_field: str = "event_type"
    # Map of event_type value → filter config with rules.
    # Events whose type is not listed here are skipped.
    event_filters: dict[str, EventFilterConfig] = {}

    # --- Dead Letter Queue ---
    # Kafka topic where events are written when webhook delivery fails after all retries.
    # Leave empty to just log failures without re-queueing.
    dlq_topic: str | None = None

    # --- Schema validation ---
    # JSON Schema per event type. Events failing validation are logged and dropped
    # before filtering. Only define schemas for types you want to enforce.
    event_schemas: dict[str, dict] = {}

    @field_validator("kafka_topics", mode="before")
    @classmethod
    def parse_topics(cls, v: str | list | None) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(t).strip() for t in v if str(t).strip()]
        return [t.strip() for t in str(v).split(",") if t.strip()]

    @property
    def kafka_enabled(self) -> bool:
        return bool(self.kafka_bootstrap_servers and self.kafka_topics)

    @property
    def ingest_enabled(self) -> bool:
        return self.ingest_secret is not None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8-sig",  # handles Windows BOM
        case_sensitive=False,
    )

    # JSON array of PipelineConfig objects.
    # Store in Secret Manager and inject as PIPELINES_JSON env var.
    pipelines_json: str

    # --- Service-wide ---
    port: int = 8080
    log_level: str = "INFO"
    service_name: str = "kafka-n8n-forwarder"

    @field_validator("pipelines_json", mode="before")
    @classmethod
    def validate_pipelines_json(cls, v: str) -> str:
        try:
            parsed = json.loads(v)
            if not isinstance(parsed, list) or len(parsed) == 0:
                raise ValueError("pipelines_json must be a non-empty JSON array")
        except json.JSONDecodeError as e:
            raise ValueError(f"pipelines_json is not valid JSON: {e}") from e
        return v

    def get_pipelines(self) -> list[PipelineConfig]:
        raw: list[dict] = json.loads(self.pipelines_json)
        pipelines = [PipelineConfig(**p) for p in raw]
        names = [p.name for p in pipelines]
        if len(names) != len(set(names)):
            raise ValueError(f"Pipeline names must be unique, got: {names}")
        return pipelines


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
