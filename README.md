# Kafka → n8n Event Forwarder (Google Cloud Run)

A persistent Cloud Run service that runs **one consumer pipeline per environment** (dev / stage / prod). Each pipeline connects to its own Kafka cluster, filters events, and forwards them to the matching n8n instance — all in a single deployment.

This is the GCP equivalent of an AWS Lambda Kafka trigger — deployed as a long-running Cloud Run service with always-on CPU so the Kafka consumers never go idle.

## Architecture

```
Kafka (dev)   → [Consumer] → [Filter] → n8n (dev)
Kafka (stage) → [Consumer] → [Filter] → n8n (stage)   ← single Cloud Run service
Kafka (prod)  → [Consumer] → [Filter] → n8n (prod)
```

All three pipelines run as concurrent asyncio tasks inside one container.

- **Runtime**: Python 3.12 + asyncio (`aiokafka` + `aiohttp` + FastAPI)
- **Delivery**: At-least-once (manual offset commit after webhook attempt)
- **Config**: Single `PIPELINES_JSON` env var (stored in Secret Manager) defines all environments
- **HTTP endpoints**: `/health`, `/ready`, `/metrics` — all report per-pipeline status

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
# Edit PIPELINES_JSON with your dev/stage/prod Kafka brokers, topics, and n8n webhook URLs
```

### 2. Run locally

```bash
pip install -r requirements.txt
python -m app.main
```

### 3. Run tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

### 4. Build and run with Docker

```bash
docker build -t kafka-n8n-forwarder .
docker run --env-file .env -p 8080:8080 kafka-n8n-forwarder
```

## Deploy to Google Cloud Run

### Prerequisites

```bash
# Enable required APIs
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com

# Build your PIPELINES_JSON (see .env.example for full template), then store it:
cat pipelines.json | gcloud secrets create pipelines-config --data-file=-
# (update with: gcloud secrets versions add pipelines-config --data-file=pipelines.json)

# Grant Cloud Run SA access
gcloud secrets add-iam-policy-binding pipelines-config \
  --member="serviceAccount:YOUR_SA@YOUR_PROJECT.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### Deploy

```bash
# Edit service.yaml — replace YOUR_PROJECT_ID, then:
gcloud run services replace service.yaml --region=YOUR_REGION
```

### CI/CD via Cloud Build

```bash
gcloud builds submit --config=cloudbuild.yaml --substitutions=_REGION=us-central1
```

## Configuration Reference

### Service-level env vars

| Variable | Required | Default | Description |
|---|---|---|---|
| `PIPELINES_JSON` | ✓ | — | JSON array of pipeline objects (see below) |
| `PORT` | | `8080` | HTTP server port |
| `LOG_LEVEL` | | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `SERVICE_NAME` | | `kafka-n8n-forwarder` | Appears in structured logs |

### Per-pipeline fields (inside `PIPELINES_JSON`)

| Field | Required | Default | Description |
|---|---|---|---|
| `name` | ✓ | — | Environment name (`"dev"`, `"stage"`, `"prod"`) — must be unique |
| `kafka_bootstrap_servers` | ✓ | — | Comma-separated broker addresses |
| `kafka_topics` | ✓ | — | Array or comma-separated topic names |
| `kafka_consumer_group_id` | | `kafka-n8n-forwarder` | Consumer group ID |
| `kafka_security_protocol` | | `SASL_SSL` | `PLAINTEXT`, `SSL`, `SASL_PLAINTEXT`, `SASL_SSL` |
| `kafka_sasl_mechanism` | | `PLAIN` | `PLAIN`, `SCRAM-SHA-256`, `SCRAM-SHA-512` |
| `kafka_sasl_username` | | — | Omit for unauthenticated Kafka |
| `kafka_sasl_password` | | — | SASL password |
| `n8n_webhook_url` | ✓ | — | Full n8n webhook URL for this environment |
| `n8n_webhook_secret` | | — | Sent as `X-Webhook-Secret` header |
| `webhook_timeout_seconds` | | `10` | Per-attempt HTTP timeout |
| `webhook_max_retries` | | `3` | Max retries on 5xx/network errors |
| `webhook_retry_backoff_seconds` | | `2.0` | Exponential backoff base (capped at 30s) |
| `filter_mode` | | `none` | `none` (forward all), `any` (OR rules), `all` (AND rules) |
| `filter_rules` | | `[]` | Array of filter rule objects |

## Filter Rules

Set `FILTER_MODE` to `any` or `all`, then provide rules as JSON in `FILTER_RULES_JSON`.

### Rule schema

```json
{
  "rule_id": "human-readable-id",
  "field": "dot.notation.path",
  "operator": "eq|neq|contains|in|exists|regex|gt|gte|lt|lte",
  "value": "<depends on operator>"
}
```

### Examples

Forward only high-value orders (all rules must match):

```bash
FILTER_MODE=all
FILTER_RULES_JSON='[
  {"rule_id":"type","field":"event_type","operator":"eq","value":"order.created"},
  {"rule_id":"value","field":"payload.amount","operator":"gte","value":1000}
]'
```

Forward any VIP-related event (any rule can match):

```bash
FILTER_MODE=any
FILTER_RULES_JSON='[
  {"rule_id":"vip-tier","field":"customer.tier","operator":"in","value":["gold","platinum"]},
  {"rule_id":"vip-tag","field":"tags","operator":"contains","value":"vip"}
]'
```

See `filter_rules.example.json` for all operator examples.

## Webhook Payload

Each forwarded event is wrapped in an envelope:

```json
{
  "event": { ...original Kafka message... },
  "metadata": {
    "topic": "my-topic",
    "partition": 0,
    "offset": 1234,
    "timestamp_ms": 1711234567000,
    "forwarded_at": "2026-03-25T10:00:00+00:00"
  }
}
```

## Cloud Run Notes

Three annotations in `service.yaml` are critical for a persistent Kafka consumer:

| Annotation | Value | Why |
|---|---|---|
| `autoscaling.knative.dev/minScale` | `"1"` | Prevent scale-to-zero — consumer must stay alive |
| `autoscaling.knative.dev/maxScale` | `"1"` | Single consumer per group (increase with partition-aware design) |
| `run.googleapis.com/cpu-throttling` | `"false"` | Consumer loop must run between Kafka messages |
