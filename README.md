# n8n Kafka Filter — Google Cloud Run

A persistent Cloud Run service that connects directly to AWS MSK Kafka over a Site-to-Site VPN, filters events by type and rules, and forwards matching events to n8n webhooks.

## Architecture

```
AWS MSK Kafka ──VPN tunnel──► GCP VPC ──VPC Connector──► Cloud Run ──► Filter ──► n8n webhook
```

- **Kafka consumer**: Cloud Run connects directly to MSK over a Site-to-Site VPN — no Lambda needed
- **VPC Connector**: routes all Cloud Run traffic through the GCP VPC and into the VPN tunnel
- **Filter engine**: routes by `event_type` field → applies rules (any/all) → forward or skip
- **Filter UI**: web interface at `/ui` to manage event types and rules (Google Sign-In protected)
- **Runtime**: Python 3.12 + asyncio (aiokafka + aiohttp + FastAPI)

---

## Environment Variables

### Service-level

| Variable | Required | Default | Description |
|---|---|---|---|
| `PIPELINES_JSON` | ✓ | — | JSON array defining all pipelines (see below) |
| `SESSION_SECRET` | ✓ | — | Secret key for signing session cookies |
| `GOOGLE_CLIENT_ID` | — | — | OAuth client ID — enables Google Sign-In for `/ui` |
| `GOOGLE_CLIENT_SECRET` | — | — | OAuth client secret |
| `ALLOWED_EMAILS` | — | — | Comma-separated emails allowed to access `/ui`. If empty, any authenticated Google user is allowed |
| `PORT` | — | `8080` | HTTP server port |
| `LOG_LEVEL` | — | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `GCS_BUCKET` | — | — | GCS bucket name to persist `filter_config.json` across redeployments. Recommended for Cloud Run. |
| `GCS_FILTER_CONFIG_OBJECT` | — | `filter_config.json` | Object name within the bucket |

### PIPELINES_JSON format

```json
[
  {
    "name": "dev",
    "kafka_bootstrap_servers": "b-1.your-cluster.kafka.us-east-1.amazonaws.com:9092",
    "kafka_topics": ["your-topic-name"],
    "kafka_security_protocol": "SASL_SSL",
    "kafka_sasl_username": "your-username",
    "kafka_sasl_password": "your-password",
    "n8n_webhook_url": "https://your-n8n.com/webhook/abc"
  }
]
```

### All PIPELINES_JSON fields

`PIPELINES_JSON` is a JSON array — one object per environment (dev, stage, prod). Each object is one pipeline: one Kafka connection, one n8n webhook, one set of filter rules.

**Required:**

| Field | Description |
|---|---|
| `name` | Pipeline name — must be unique. Used in logs, metrics, and the `/ingest/{name}` URL. Example: `"dev"` |
| `n8n_webhook_url` | Full URL of the n8n webhook to forward events to |

**Kafka connection** (needed when connecting directly to Kafka over VPN):

| Field | Default | Description |
|---|---|---|
| `kafka_bootstrap_servers` | — | Comma-separated broker addresses. Example: `"b-1.cluster.kafka.amazonaws.com:9092"` |
| `kafka_topics` | — | Topic name(s) to consume. Example: `["my-topic"]` |
| `kafka_consumer_group_id` | `"kafka-n8n-forwarder"` | Kafka consumer group ID |
| `kafka_security_protocol` | `"SASL_SSL"` | `PLAINTEXT`, `SSL`, `SASL_PLAINTEXT`, or `SASL_SSL` |
| `kafka_sasl_mechanism` | `"PLAIN"` | `PLAIN`, `SCRAM-SHA-256`, or `SCRAM-SHA-512` |
| `kafka_sasl_username` | — | Kafka username (omit if no auth) |
| `kafka_sasl_password` | — | Kafka password |
| `kafka_auto_offset_reset` | `"earliest"` | Where to start reading: `"earliest"` or `"latest"` |

**Webhook delivery:**

| Field | Default | Description |
|---|---|---|
| `n8n_webhook_secret` | — | Sent as `X-Webhook-Secret` header to n8n |
| `webhook_timeout_seconds` | `10` | How long to wait for n8n to respond |
| `webhook_max_retries` | `3` | Retry attempts on network errors or 5xx responses |
| `webhook_retry_backoff_seconds` | `2.0` | Seconds between retries (doubles each attempt, max 30s) |

**Filtering:**

| Field | Default | Description |
|---|---|---|
| `event_type_field` | `"event_type"` | Which field in the event body identifies its type |
| `event_filters` | `{}` | Filter rules per event type — managed via the `/ui` |

**Other:**

| Field | Default | Description |
|---|---|---|
| `ingest_secret` | — | Enables the `/ingest/{name}` HTTP endpoint. Any POST must include this as `X-Ingest-Secret` header |
| `dlq_topic` | — | Kafka topic to write failed events to after all retries are exhausted |

---

## Filter UI

Access at `/ui`. Requires Google Sign-In when `GOOGLE_CLIENT_ID` is set.

- Add event types with any/all rules
- Rules support: `eq`, `neq`, `contains`, `in`, `exists`, `regex`, `gt`, `gte`, `lt`, `lte`
- Changes apply instantly (hot-reload, no restart needed)
- Filter config is stored in GCS (`GCS_BUCKET` env var) so it survives redeployments. Falls back to local `filter_config.json` when `GCS_BUCKET` is not set.

**Logic**: unknown event type → skip. Known event type with no rules → skip. Rules match → forward.

---

## Deploy to Google Cloud Run

### 1. First-time GCP setup

```bash
export PROJECT_ID=your-project-id
export REGION=us-central1
bash scripts/setup_gcp.sh
```

This creates a service account, enables APIs, and grants required permissions.

### 2. Connect GitHub to Cloud Build

1. Go to: https://console.cloud.google.com/cloud-build/triggers/connect
2. Connect your GitHub repository
3. Run the CI/CD setup script:

```bash
export PROJECT_ID=your-project-id
export GITHUB_OWNER=your-github-username
export GITHUB_REPO=n8n-kafka-filter
bash scripts/setup_cicd.sh
```

Cloud Build will now build and deploy automatically on every push to `main`.

### 3. Set up VPN to AWS MSK

Follow `AWS_INTEGRATION.md` — this sets up the Site-to-Site VPN and VPC connector so Cloud Run can reach MSK.

### 4. Set up MSK DNS resolution

GCP cannot resolve AWS private DNS hostnames (e.g. `b-1.cluster.kafka.us-east-1.amazonaws.com`) without a forwarding zone. Run in Cloud Shell after the VPN is established:

```bash
# Replace 10.0.0.2 with your AWS VPC base CIDR + 2 (e.g. VPC is 10.0.0.0/16 → resolver is 10.0.0.2)
gcloud dns managed-zones create aws-kafka-dns \
  --dns-name="kafka.us-east-1.amazonaws.com." \
  --description="Forward MSK DNS to AWS resolver" \
  --visibility=private \
  --networks=default \
  --forwarding-targets=10.0.0.2
```

### 5. Set environment variables in Cloud Run

Go to your Cloud Run service → **Edit & Deploy New Revision** → **Variables & Secrets**:

| Variable | Where to get it |
|---|---|
| `PIPELINES_JSON` | MSK broker endpoints + n8n webhook URLs (see format above) |
| `SESSION_SECRET` | Any random string (e.g. `openssl rand -hex 32`) |
| `GOOGLE_CLIENT_ID` | Google Cloud Console → APIs & Services → Credentials |
| `GOOGLE_CLIENT_SECRET` | Same as above |
| `ALLOWED_EMAILS` | Comma-separated list, e.g. `user@gmail.com,other@company.com` |

### 6. Fix public access (org policy)

If your GCP org blocks unauthenticated Cloud Run services, ask a GCP admin to run:

```bash
gcloud run services update n8n-kafka-filter --region us-central1 --no-invoker-iam-check
```

Or via Console: Cloud Run service → **Security** → **Allow unauthenticated invocations** → Save.

---

## Google Sign-In Setup

### 1. Create OAuth credentials

1. Go to: https://console.cloud.google.com/apis/credentials
2. **Create Credentials** → **OAuth 2.0 Client ID** → Web application
3. Under **Authorized redirect URIs** add:
   ```
   https://YOUR_CLOUD_RUN_URL/auth/callback
   http://localhost:8080/auth/callback
   ```
4. Copy the **Client ID** and **Client Secret** into Cloud Run env vars

### 2. Configure OAuth consent screen

1. Go to: https://console.cloud.google.com/auth/overview
2. Click **Audience** → set to **External**
3. Keep status as **Testing** (no need to publish)
4. Under **Test users** → **Add users** → add every email that should be able to sign in

> **Important**: In Testing mode, Google only allows sign-in for emails explicitly listed as test users. Add all allowed emails here, not just in `ALLOWED_EMAILS`.

### 3. Add users

Every user needs to be added in **two places**:

1. **OAuth consent screen** → Audience → Test users (controls who Google allows to sign in)
2. **Cloud Run env var** `ALLOWED_EMAILS` (controls who the app lets through after sign-in)

Both lists must include the email, otherwise access will be denied.

---

## Local Development

```bash
cp .env.example .env
# Edit .env with your config

pip install -r requirements.txt
python -m app.main
```

Run tests:
```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Run with Docker:
```bash
docker build -t kafka-n8n-forwarder .
docker run --env-file .env -p 8080:8080 kafka-n8n-forwarder
```

---

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/ingest/{env}` | POST | Receive event from HTTP client |
| `/ui` | GET | Filter configuration UI (auth-protected) |
| `/health` | GET | Service health — per-pipeline status |
| `/ready` | GET | Readiness probe — Kafka connection status |
| `/metrics` | GET | Uptime, forwarded/filtered counts |
| `/auth/login` | GET | Initiate Google Sign-In |
| `/auth/callback` | GET | OAuth callback |
| `/auth/logout` | GET | Clear session and redirect to `/ui` |
| `/api/pipelines` | GET | Get current filter config (no secrets) |
| `/api/pipelines/{env}/filters` | PUT | Update filter config (hot-reload) |
