from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from app.config import PipelineConfig

logger = logging.getLogger(__name__)


class WebhookDeliveryError(Exception):
    pass


class WebhookForwarder:
    def __init__(self, pipeline: "PipelineConfig") -> None:
        self._pipeline = pipeline
        self._session: aiohttp.ClientSession | None = None
        self._dlq_producer = None   # aiokafka producer, created lazily if dlq_topic is set
        self._messages_forwarded = 0
        self._webhook_errors = 0
        self._dlq_sent = 0

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._pipeline.webhook_timeout_seconds),
            headers={
                "Content-Type": "application/json",
                "X-Forwarded-From": "kafka-n8n-forwarder",
                "X-Pipeline-Env": self._pipeline.name,
            },
        )
        if self._pipeline.dlq_topic:
            await self._start_dlq_producer()

    async def _start_dlq_producer(self) -> None:
        from aiokafka import AIOKafkaProducer
        p = self._pipeline
        kwargs: dict = dict(
            bootstrap_servers=p.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        if p.kafka_sasl_username and p.kafka_sasl_password:
            kwargs.update(
                security_protocol=p.kafka_security_protocol,
                sasl_mechanism=p.kafka_sasl_mechanism,
                sasl_plain_username=p.kafka_sasl_username,
                sasl_plain_password=p.kafka_sasl_password.get_secret_value(),
            )
        self._dlq_producer = AIOKafkaProducer(**kwargs)
        await self._dlq_producer.start()
        logger.info("DLQ producer started", extra={"env": p.name, "dlq_topic": p.dlq_topic})

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
        if self._dlq_producer:
            await self._dlq_producer.stop()
            self._dlq_producer = None

    async def forward(self, event: dict, kafka_metadata: dict) -> bool:
        """Forward event to n8n webhook. Returns True on success."""
        payload = {
            "event": event,
            "metadata": {
                **kafka_metadata,
                "forwarded_at": datetime.now(timezone.utc).isoformat(),
            },
        }

        p = self._pipeline
        last_error: Exception | None = None

        for attempt in range(p.webhook_max_retries + 1):
            try:
                await self._attempt(payload, attempt)
                self._messages_forwarded += 1
                logger.info(
                    "Event forwarded to webhook",
                    extra={
                        "env": p.name,
                        "topic": kafka_metadata.get("topic"),
                        "offset": kafka_metadata.get("offset"),
                        "attempt": attempt + 1,
                    },
                )
                return True
            except WebhookDeliveryError as e:
                last_error = e
                if attempt < p.webhook_max_retries:
                    wait = min(p.webhook_retry_backoff_seconds ** (attempt + 1), 30.0)
                    logger.warning(
                        "Webhook delivery failed, retrying",
                        extra={
                            "env": p.name,
                            "attempt": attempt + 1,
                            "max_retries": p.webhook_max_retries,
                            "wait_seconds": wait,
                            "error": str(e),
                        },
                    )
                    await asyncio.sleep(wait)

        self._webhook_errors += 1
        await self._send_to_dlq(payload, str(last_error))
        return False

    async def _send_to_dlq(self, payload: dict, error: str) -> None:
        p = self._pipeline
        dlq_record = {
            **payload,
            "dlq_metadata": {
                "env": p.name,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "error": error,
                "webhook_url": str(p.n8n_webhook_url),
                "attempts": p.webhook_max_retries + 1,
            },
        }

        if self._dlq_producer and p.dlq_topic:
            try:
                await self._dlq_producer.send_and_wait(p.dlq_topic, value=dlq_record)
                self._dlq_sent += 1
                logger.warning(
                    "Event sent to DLQ",
                    extra={"env": p.name, "dlq_topic": p.dlq_topic, "error": error},
                )
            except Exception as e:
                logger.error(
                    "Failed to send to DLQ — event permanently lost",
                    extra={"env": p.name, "dlq_topic": p.dlq_topic, "error": str(e), "payload": dlq_record},
                )
        else:
            logger.error(
                "Webhook delivery exhausted all retries — event dropped (no DLQ configured)",
                extra={
                    "env": p.name,
                    "topic": payload.get("metadata", {}).get("topic"),
                    "offset": payload.get("metadata", {}).get("offset"),
                    "error": error,
                    "dropped_payload": dlq_record,
                },
            )

    async def _attempt(self, payload: dict, attempt: int) -> None:
        assert self._session is not None, "WebhookForwarder.start() must be called first"

        headers: dict[str, str] = {}
        if self._pipeline.n8n_webhook_secret:
            headers["X-Webhook-Secret"] = self._pipeline.n8n_webhook_secret.get_secret_value()

        url = str(self._pipeline.n8n_webhook_url)
        try:
            async with self._session.post(url, json=payload, headers=headers) as resp:
                if resp.status >= 500:
                    body = await resp.text()
                    raise WebhookDeliveryError(f"HTTP {resp.status}: {body[:200]}")
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error(
                        "Webhook returned 4xx — check URL or payload",
                        extra={"env": self._pipeline.name, "status": resp.status, "body": body[:500]},
                    )
                    raise WebhookDeliveryError(f"HTTP {resp.status} (non-retryable): {body[:200]}")
        except aiohttp.ClientError as e:
            raise WebhookDeliveryError(f"Network error: {e}") from e

    @property
    def stats(self) -> dict:
        return {
            "messages_forwarded": self._messages_forwarded,
            "webhook_errors": self._webhook_errors,
            "dlq_sent": self._dlq_sent,
        }
