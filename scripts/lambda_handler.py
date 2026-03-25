"""
AWS Lambda handler — reads events from MSK Kafka and forwards to Cloud Run /ingest endpoint.

Add this to your existing Lambda, or deploy as a standalone Lambda with an MSK trigger.

Required environment variables (set in Lambda console or via AWS SSM):
  CLOUD_RUN_URL    — base URL of the Cloud Run service, e.g. https://kafka-n8n-forwarder-xxx.run.app
  INGEST_ENV       — pipeline name to target: dev | stage | prod
  INGEST_SECRET    — shared secret matching ingest_secret in PIPELINES_JSON (store in SSM Parameter Store)

Lambda trigger:
  Type: MSK (Amazon MSK)
  MSK cluster: your cluster
  Topic: your Kafka topic
  Starting position: LATEST (or TRIM_HORIZON to replay from beginning)
  Batch size: 10–100 (tune based on event volume)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import urllib.request
import urllib.error

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CLOUD_RUN_URL = os.environ["CLOUD_RUN_URL"].rstrip("/")
INGEST_ENV    = os.environ["INGEST_ENV"]
INGEST_SECRET = os.environ["INGEST_SECRET"]

INGEST_ENDPOINT = f"{CLOUD_RUN_URL}/ingest/{INGEST_ENV}"


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

            status = _forward(payload, topic, offset)
            results[status] = results.get(status, 0) + 1

    logger.info(f"Batch complete: {results}")
    return results


def _decode(raw: str) -> dict:
    """MSK delivers message values as base64-encoded strings."""
    decoded = base64.b64decode(raw).decode("utf-8")
    return json.loads(decoded)


def _forward(payload: dict, topic: str, offset: int | None) -> str:
    """POST event to Cloud Run ingest endpoint. Returns status string."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=INGEST_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
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
        # Re-raise so Lambda retries the batch on 5xx
        if e.code >= 500:
            raise
        return "errors"

    except urllib.error.URLError as e:
        logger.error(f"Network error calling Cloud Run: {e} (offset={offset})")
        raise  # Lambda will retry
