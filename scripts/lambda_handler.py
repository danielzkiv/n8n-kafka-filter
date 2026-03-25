"""
AWS Lambda handler — reads events from MSK Kafka and forwards to Cloud Run /ingest endpoint.

Cloud Run requires authentication (Google identity token). This handler fetches
a short-lived token from Google using a service account key stored in AWS Secrets Manager.

Required environment variables (set in Lambda console):
  CLOUD_RUN_URL       — base URL of the Cloud Run service, e.g. https://kafka-n8n-forwarder-xxx.run.app
  INGEST_ENV          — pipeline name to target: dev | stage | prod
  INGEST_SECRET       — shared secret matching ingest_secret in PIPELINES_JSON
  GOOGLE_SA_SECRET    — name of the AWS Secrets Manager secret holding the GCP service account JSON key

Lambda trigger:
  Type: MSK (Amazon MSK)
  MSK cluster: your cluster
  Topic: your Kafka topic
  Starting position: LATEST (or TRIM_HORIZON to replay from beginning)
  Batch size: 10–100 (tune based on event volume)

Setup required before deploying:
  1. Create a GCP service account with Cloud Run Invoker role (see below)
  2. Download the service account key as JSON
  3. Store it in AWS Secrets Manager (see below)
  4. Package this handler with google-auth library (see below)

--- GCP: Create service account ---
  1. Go to: https://console.cloud.google.com/iam-admin/serviceaccounts?project=bdi-apps-491216
  2. Click Create Service Account
     - Name: lambda-invoker
     - Click Create and Continue
  3. Grant role: Cloud Run Invoker → click Continue → Done
  4. Click the service account → Keys tab → Add Key → Create new key → JSON → Create
     (downloads a .json file — keep it safe)
  5. Go to your Cloud Run service → Permissions → Add principal
     - Principal: lambda-invoker@bdi-apps-491216.iam.gserviceaccount.com
     - Role: Cloud Run Invoker
     - Save

--- AWS: Store the key in Secrets Manager ---
  1. AWS Console → Secrets Manager → Store a new secret
  2. Secret type: Other type of secret → Plaintext
  3. Paste the entire contents of the downloaded .json file
  4. Secret name: gcp-lambda-invoker-key
  5. Click Next → Store
  6. Set GOOGLE_SA_SECRET=gcp-lambda-invoker-key in Lambda env vars

--- Package and deploy ---
  Run these commands locally, then upload lambda.zip to Lambda:

  mkdir lambda_package
  pip install google-auth requests -t lambda_package/
  cp scripts/lambda_handler.py lambda_package/lambda_handler.py
  cd lambda_package && zip -r ../lambda.zip . && cd ..

  In Lambda console:
  - Runtime: Python 3.12
  - Handler: lambda_handler.lambda_handler
  - Upload the lambda.zip file
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.request
import urllib.error

import boto3
import google.auth.transport.requests
from google.oauth2 import service_account

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CLOUD_RUN_URL    = os.environ["CLOUD_RUN_URL"].rstrip("/")
INGEST_ENV       = os.environ["INGEST_ENV"]
INGEST_SECRET    = os.environ["INGEST_SECRET"]
GOOGLE_SA_SECRET = os.environ["GOOGLE_SA_SECRET"]

INGEST_ENDPOINT = f"{CLOUD_RUN_URL}/ingest/{INGEST_ENV}"

# Cache the token — valid for 1 hour, refresh 5 min before expiry
_token_cache: dict = {"token": None, "expires_at": 0}


def _get_identity_token() -> str:
    """Fetch a Google identity token, using cache to avoid re-fetching every event."""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    # Load service account key from AWS Secrets Manager
    sm = boto3.client("secretsmanager")
    secret = sm.get_secret_value(SecretId=GOOGLE_SA_SECRET)
    sa_info = json.loads(secret["SecretString"])

    # Create credentials targeting the Cloud Run URL (audience)
    credentials = service_account.IDTokenCredentials.from_service_account_info(
        sa_info,
        target_audience=CLOUD_RUN_URL,
    )
    credentials.refresh(google.auth.transport.requests.Request())

    _token_cache["token"] = credentials.token
    _token_cache["expires_at"] = now + 3300  # 55 minutes
    logger.info("Google identity token refreshed")
    return credentials.token


def lambda_handler(event: dict, context) -> dict:
    """
    MSK trigger delivers records grouped by topic-partition:
    {
      "records": {
        "my-topic-0": [ { "value": "<base64>", "offset": 0, ... }, ... ],
        "my-topic-1": [ ... ]
      }
    }
    """
    records = event.get("records", {})
    total = sum(len(msgs) for msgs in records.values())
    logger.info(f"Received {total} records from MSK")

    # Fetch token once for the whole batch
    token = _get_identity_token()

    results = {"forwarded": 0, "filtered": 0, "invalid": 0, "errors": 0}

    for partition_key, messages in records.items():
        for msg in messages:
            offset = msg.get("offset")
            topic = msg.get("topic", partition_key.rsplit("-", 1)[0])

            try:
                payload = _decode(msg["value"])
            except Exception as e:
                logger.error(f"Failed to decode message offset={offset}: {e}")
                results["errors"] += 1
                continue

            status = _forward(payload, topic, offset, token)
            results[status] = results.get(status, 0) + 1

    logger.info(f"Batch complete: {results}")
    return results


def _decode(raw: str) -> dict:
    """MSK delivers message values as base64-encoded strings."""
    decoded = base64.b64decode(raw).decode("utf-8")
    return json.loads(decoded)


def _forward(payload: dict, topic: str, offset: int | None, token: str) -> str:
    """POST event to Cloud Run ingest endpoint. Returns status string."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=INGEST_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Ingest-Secret": INGEST_SECRET,
            "X-Source-Topic": topic,
            "X-Source-Offset": str(offset) if offset is not None else "",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body_resp = json.loads(resp.read())
            status = body_resp.get("status", "unknown")
            logger.debug(f"offset={offset} → {status}")
            return status

    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")
        logger.error(f"HTTP {e.code} from Cloud Run: {body_err[:300]} (offset={offset})")
        if e.code >= 500:
            raise
        return "errors"

    except urllib.error.URLError as e:
        logger.error(f"Network error calling Cloud Run: {e} (offset={offset})")
        raise
