# Project Overview

How the project works, all blockers encountered and their fixes, and everything still left to do.

---

## How it works

```
AWS MSK Kafka
     │
     │  (private VPC — not reachable from internet)
     │
AWS Lambda  ──HTTPS POST──►  Google Cloud Run  ──►  n8n webhook
                              /ingest/{env}
                              │
                              ├── Auth (X-Ingest-Secret header)
                              ├── Schema validation (optional)
                              ├── Filter engine
                              │     event_type → rules → forward or skip
                              └── Forward to n8n
```

### Why Lambda in the middle

MSK Kafka runs inside a private AWS VPC with no public access. Lambda is deployed in the same VPC so it can reach the Kafka brokers. It reads each Kafka message and POSTs it to Cloud Run over HTTPS. This avoids exposing MSK to the internet.

Alternative: set up a Site-to-Site VPN between AWS VPC and GCP VPC so Cloud Run connects to Kafka directly — see `SETUP.md`.

### Filter engine

Every incoming event goes through this logic:

1. Read the `event_type` field from the event body
2. Look up rules configured for that event type
3. If event type not configured → **skip**
4. If no rules defined → **skip**
5. Evaluate rules (any = at least one matches, all = every rule matches)
6. Rules match → **forward to n8n webhook**
7. Rules don't match → **skip**

Rules support: `eq`, `neq`, `contains`, `in`, `exists`, `regex`, `gt`, `gte`, `lt`, `lte`
Fields support dot notation: `payload.customer.tier`

### Filter UI

Accessible at `/ui`. Lets you add event types with rules, edit them, and delete them without restarting the service. Changes are hot-reloaded instantly and persisted to `filter_config.json`.

### Webhook payload

When an event passes the filter, this is sent to n8n:

```json
{
  "event": { ...original event body... },
  "metadata": {
    "env": "prod",
    "source": "http-ingest",
    "ingested_at": "2026-01-01T00:00:00Z",
    "event_type": "order.created"
  }
}
```

In n8n use `{{ $json.metadata.event_type }}` to branch by event type.

### Multi-pipeline

One Cloud Run service handles multiple environments (dev/stage/prod) simultaneously. Each pipeline has its own Kafka topics, webhook URL, and filter rules. Configured via `PIPELINES_JSON` environment variable stored in GCP Secret Manager.

---

## Project structure

```
app/
  main.py            — FastAPI app, HTTP ingest endpoint, health/metrics/UI routes
  config.py          — Pydantic config models (PipelineConfig, EventFilterConfig, Settings)
  filter_engine.py   — Filter logic: event_type routing + rule evaluation
  filter_store.py    — Persists filter config to filter_config.json
  consumer.py        — Kafka consumer (aiokafka), used when kafka_enabled
  webhook.py         — HTTP delivery to n8n with retry + DLQ
  schema_validator.py — JSON Schema validation per event type (optional)
  logging_config.py  — Structured JSON logging
  static/index.html  — Filter config UI

scripts/
  lambda_handler.py  — AWS Lambda handler: reads MSK → POSTs to Cloud Run
  setup_gcp.sh       — Deploy to Cloud Run (one-time)
  setup_vpn_step1.sh — Create GCP VPN gateway, get external IP for AWS team
  setup_vpn_step2.sh — Create VPN tunnels after AWS team sends tunnel info
  setup_cicd.sh      — Connect Cloud Build to GitHub
  setup_monitoring.sh — GCP alert policies

tests/
  test_filter_engine.py
  test_config.py
  test_webhook.py

SETUP.md             — Full deployment guide (start here)
PROJECT.md           — This file
```

---

## Blockers and fixes

### 1. Docker not working on Windows

**Problem:** `docker compose` and `docker-compose` both failed.

**Fix:** Abandoned Docker for local testing. Run the app directly:
```bash
pip install -r requirements.txt
python -m app.main
```

---

### 2. Python not found in terminal

**Problem:** `python` command not recognized on Windows.

**Fix:** `setup_local.ps1` now auto-installs Python via winget or direct download if not found. Run it in PowerShell:
```powershell
.\setup_local.ps1
```

---

### 3. Pydantic validation error on startup — `extra inputs not permitted`

**Problem:** `.env` file written by PowerShell `Set-Content` had a UTF-8 BOM (byte order mark) prepended. Pydantic-settings read the BOM as part of the first key name, causing it to not match any field.

**Fix:** Changed `env_file_encoding` in `Settings` from `utf-8` to `utf-8-sig`, which strips the BOM automatically.

```python
# app/config.py
model_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8-sig",  # handles Windows BOM
)
```

---

### 4. PowerShell `curl` syntax incompatible

**Problem:** `curl -H "..." -d "..."` failed because PowerShell's `curl` is an alias for `Invoke-WebRequest`, not the Unix curl.

**Fix:** Use `Invoke-WebRequest` syntax:
```powershell
Invoke-WebRequest -Uri "http://localhost:8080/ingest/dev" `
  -Method POST `
  -Headers @{"X-Ingest-Secret"="dev-secret-123"; "Content-Type"="application/json"} `
  -Body '{"event_type":"order.created","amount":150}'
```

---

### 5. MSK Kafka not reachable from Cloud Run

**Problem:** MSK is on a private AWS VPC. Cloud Run (GCP) has no network path to it.

**Two solutions:**

**Option A — AWS Lambda (simpler):**
Lambda runs in the same VPC as MSK. It reads Kafka messages and POSTs to Cloud Run via HTTPS. See `scripts/lambda_handler.py`.

**Option B — Site-to-Site VPN:**
Creates a private network tunnel between AWS VPC and GCP VPC. Cloud Run connects to MSK directly using private broker endpoints. See `scripts/setup_vpn_step1.sh` and `scripts/setup_vpn_step2.sh`.

---

### 6. Cloud Run can't reach VPC resources by default

**Problem:** Cloud Run is serverless — it runs outside any VPC by default, so even with a VPN tunnel set up it can't reach MSK.

**Fix:** `setup_vpn_step1.sh` creates a **Serverless VPC Access connector**. `setup_vpn_step2.sh` attaches it to the Cloud Run service with `--vpc-egress=all-traffic`, routing all outbound traffic through the GCP VPC and on to AWS via the tunnel.

---

### 7. Filter config lost on Cloud Run restart

**Problem:** `filter_config.json` is written to the container filesystem, which is ephemeral on Cloud Run.

**Current state:** This is a known limitation. On restart the filter config resets to whatever is in `PIPELINES_JSON`.

**Fix (not yet implemented):** Move `filter_store.py` to use GCP Firestore or Cloud Storage instead of the local filesystem. See "Still to do" below.

---

## Still to do

### Must do before going live

- [ ] **Deploy to Cloud Run** — run `bash scripts/setup_gcp.sh` with real project ID
- [ ] **Set PIPELINES_JSON** in Secret Manager with real n8n webhook URLs and ingest secrets
- [ ] **Connect to MSK** — choose Lambda (Option A) or VPN (Option B) and complete setup per `SETUP.md`
- [ ] **Add event type filters** in the UI for each event type your system produces

### Option A — Lambda path (if not doing VPN)

- [ ] Create Lambda function in AWS Console, paste `scripts/lambda_handler.py`
- [ ] Set Lambda env vars: `CLOUD_RUN_URL`, `INGEST_ENV`, `INGEST_SECRET`
- [ ] Add MSK trigger to Lambda (topic + consumer group)
- [ ] Attach Lambda to the same VPC/subnets as MSK
- [ ] Add outbound HTTPS (port 443) to Lambda security group

### Option B — VPN path (direct Kafka connection)

- [ ] Fill in and run `scripts/setup_vpn_step1.sh` — get GCP external IP
- [ ] Send GCP IP to AWS team with instructions from `SETUP.md` Phase 3
- [ ] Receive tunnel IPs + PSKs from AWS team
- [ ] Fill in and run `scripts/setup_vpn_step2.sh`
- [ ] Verify both tunnels show `ESTABLISHED`
- [ ] Update `PIPELINES_JSON` with MSK private broker endpoints
- [ ] Redeploy Cloud Run

### Nice to have (future)

- [ ] **Persist filter config** — move `filter_store.py` from local filesystem to GCP Firestore or Cloud Storage so filter rules survive Cloud Run restarts
- [ ] **CI/CD** — run `bash scripts/setup_cicd.sh` to auto-deploy on every push to main
- [ ] **Monitoring alerts** — run `bash scripts/setup_monitoring.sh` for GCP alert policies
- [ ] **Multiple environments** — add dev/stage pipelines to `PIPELINES_JSON` alongside prod
- [ ] **Dead letter queue** — set `dlq_topic` in pipeline config to re-queue events that fail all webhook retries
- [ ] **Schema validation** — set `event_schemas` in pipeline config to drop malformed events before filtering
