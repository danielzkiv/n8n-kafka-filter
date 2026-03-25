from __future__ import annotations

import asyncio
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
        self._messages_forwarded = 0
        self._webhook_errors = 0

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._pipeline.webhook_timeout_seconds),
            headers={
                "Content-Type": "application/json",
                "X-Forwarded-From": "kafka-n8n-forwarder",
                "X-Pipeline-Env": self._pipeline.name,
            },
        )

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

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
        logger.error(
            "Webhook delivery exhausted all retries — event dropped",
            extra={
                "env": p.name,
                "topic": kafka_metadata.get("topic"),
                "offset": kafka_metadata.get("offset"),
                "attempts": p.webhook_max_retries + 1,
                "last_error": str(last_error),
                "dropped_payload": payload,
            },
        )
        return False

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
        }
