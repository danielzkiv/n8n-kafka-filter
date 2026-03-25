# n8n Kafka Filter — Google Cloud Run

A persistent Cloud Run service that receives events (from Kafka or HTTP), filters them by event type and rules, and forwards matching events to n8n webhooks.

## Architecture

```
AWS MSK Kafka ──► Lambda handler ──► POST /ingest/{env} ──► Filter ──► n8n webhook
                                                              ▲
                            direct Kafka (via VPN) ──────────┘
```

- **HTTP ingest** (`/ingest/{env}`): AWS Lambda reads from MSK and POSTs events to Cloud Run
- **Direct Kafka** (optional): Cloud Run connects directly to Kafka over Site-to-Site VPN
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

### PIPELINES_JSON format

```json
[
  {
    "name": "dev",
    "n8n_webhook_url": "https://your-n8n.com/webhook/abc",
    "n8n_webhook_secret": "optional-secret",
    "ingest_secret": "shared-secret-for-lambda",
    "kafka_bootstrap_servers": "broker1:9092,broker2:9092",
    "kafka_topics": ["my-topic"],
    "kafka_sasl_username": "user",
    "kafka_sasl_password": "pass"
  }
]
```

Set `ingest_secret` to enable HTTP ingest (`/ingest/dev`). Omit `kafka_bootstrap_servers` for HTTP-only mode.

---

## Filter UI

Access at `/ui`. Requires Google Sign-In when `GOOGLE_CLIENT_ID` is set.

- Add event types with any/all rules
- Rules support: `eq`, `neq`, `contains`, `in`, `exists`, `regex`, `gt`, `gte`, `lt`, `lte`
- Changes apply instantly (hot-reload, no restart needed)
- Filter config is stored in `filter_config.json` (gitignored — persists in the container)

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

### 3. Set environment variables in Cloud Run

Go to your Cloud Run service → **Edit & Deploy New Revision** → **Variables & Secrets**:

| Variable | Where to get it |
|---|---|
| `PIPELINES_JSON` | Your Kafka broker details + n8n webhook URLs |
| `SESSION_SECRET` | Any random string (e.g. `openssl rand -hex 32`) |
| `GOOGLE_CLIENT_ID` | Google Cloud Console → APIs & Services → Credentials |
| `GOOGLE_CLIENT_SECRET` | Same as above |
| `ALLOWED_EMAILS` | Comma-separated list, e.g. `user@gmail.com,other@company.com` |

### 4. Fix public access (org policy)

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

## AWS Lambda (MSK Ingest)

For reading from AWS MSK Kafka (private VPC), deploy `scripts/lambda_handler.py` as an AWS Lambda function with an MSK trigger.

See the file header for full setup instructions including:
- GCP service account creation (Cloud Run Invoker role)
- Storing the service account key in AWS Secrets Manager
- Lambda packaging and deployment

Required Lambda env vars:

| Variable | Description |
|---|---|
| `CLOUD_RUN_URL` | Cloud Run service URL |
| `INGEST_ENV` | Pipeline name (`dev`, `stage`, `prod`) |
| `INGEST_SECRET` | Must match `ingest_secret` in `PIPELINES_JSON` |
| `GOOGLE_SA_SECRET` | AWS Secrets Manager secret name holding GCP service account JSON |

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
| `/ingest/{env}` | POST | Receive event from Lambda/HTTP client |
| `/ui` | GET | Filter configuration UI (auth-protected) |
| `/health` | GET | Service health — per-pipeline status |
| `/ready` | GET | Readiness probe — Kafka connection status |
| `/metrics` | GET | Uptime, forwarded/filtered counts |
| `/auth/login` | GET | Initiate Google Sign-In |
| `/auth/callback` | GET | OAuth callback |
| `/auth/logout` | GET | Clear session and redirect to `/ui` |
| `/api/pipelines` | GET | Get current filter config (no secrets) |
| `/api/pipelines/{env}/filters` | PUT | Update filter config (hot-reload) |
