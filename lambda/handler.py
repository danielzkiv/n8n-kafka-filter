"""
AWS Lambda handler — MSK trigger → Cloud Run /ingest/{env}

Triggered automatically by the MSK event source mapping.
Each Kafka message is decoded and POSTed to Cloud Run for filtering and forwarding.

Environment variables:
  CLOUD_RUN_URL    — Base URL of the Cloud Run service (e.g. https://n8n-kafka-filter-xxx.run.app)
  INGEST_SECRET    — Must match ingest_secret in Cloud Run's PIPELINES_JSON for this pipeline
  PIPELINE_ENV     — Pipeline name: dev, stage, prod (used in /ingest/{env})
  TIMEOUT_SECONDS  — HTTP timeout in seconds (default: 10)
"""
import base64
import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CLOUD_RUN_URL = os.environ["CLOUD_RUN_URL"].rstrip("/")
INGEST_SECRET = os.environ["INGEST_SECRET"]
PIPELINE_ENV  = os.environ["PIPELINE_ENV"]
TIMEOUT       = int(os.environ.get("TIMEOUT_SECONDS", "10"))

INGEST_URL = f"{CLOUD_RUN_URL}/ingest/{PIPELINE_ENV}"


def lambda_handler(event, context):
    records = event.get("records", {})
    total = forwarded = filtered = failed = 0

    for partition_records in records.values():
        for record in partition_records:
            total += 1
            raw = record.get("value")
            if not raw:
                logger.warning("Empty record value, skipping", extra={"offset": record.get("offset")})
                continue

            try:
                payload = json.loads(base64.b64decode(raw))
            except Exception as e:
                logger.error("Failed to decode record", extra={"offset": record.get("offset"), "error": str(e)})
                failed += 1
                continue

            if not isinstance(payload, dict):
                logger.warning("Record is not a JSON object, skipping", extra={"offset": record.get("offset")})
                failed += 1
                continue

            status, resp_body = _post(payload, record.get("offset"))

            if status == 200:
                result = resp_body.get("status", "")
                if result == "filtered":
                    filtered += 1
                    logger.debug("Event filtered", extra={"offset": record.get("offset")})
                else:
                    forwarded += 1
            elif status == 422:
                # Schema/format error — retrying won't help, log and skip
                logger.warning("Event rejected by Cloud Run", extra={"offset": record.get("offset"), "response": resp_body})
                failed += 1
            else:
                logger.error("Cloud Run error", extra={"status": status, "offset": record.get("offset"), "response": resp_body})
                # Raise so the MSK trigger retries the batch
                raise RuntimeError(f"Cloud Run returned HTTP {status} — retrying batch")

    logger.info("Batch complete", extra={"total": total, "forwarded": forwarded, "filtered": filtered, "failed": failed})
    return {"total": total, "forwarded": forwarded, "filtered": filtered, "failed": failed}


def _post(payload: dict, offset) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        INGEST_URL,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Ingest-Secret": INGEST_SECRET,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = {}
        return e.code, body
    except Exception as e:
        logger.error("HTTP request failed", extra={"offset": offset, "error": str(e)})
        raise
