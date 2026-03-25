from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

if TYPE_CHECKING:
    from app.config import PipelineConfig
    from app.filter_engine import FilterEngine
    from app.schema_validator import SchemaValidator
    from app.webhook import WebhookForwarder

logger = logging.getLogger(__name__)


class KafkaConsumerService:
    def __init__(
        self,
        pipeline: "PipelineConfig",
        filter_engine: "FilterEngine",
        forwarder: "WebhookForwarder",
        schema_validator: "SchemaValidator | None" = None,
    ) -> None:
        self._pipeline = pipeline
        self._filter_engine = filter_engine
        self._forwarder = forwarder
        self._schema_validator = schema_validator
        self._consumer: AIOKafkaConsumer | None = None
        self._running = False
        self._connected = False
        self._messages_consumed = 0
        self._messages_filtered = 0
        self._messages_invalid = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def stats(self) -> dict:
        s = {
            "messages_consumed": self._messages_consumed,
            "messages_filtered": self._messages_filtered,
            "messages_invalid": self._messages_invalid,
        }
        if self._schema_validator:
            s.update(self._schema_validator.stats)
        return s

    async def start(self) -> None:
        p = self._pipeline
        kwargs: dict = dict(
            bootstrap_servers=p.kafka_bootstrap_servers,
            group_id=p.kafka_consumer_group_id,
            enable_auto_commit=False,
            auto_offset_reset=p.kafka_auto_offset_reset,
            value_deserializer=self._deserialize,
        )

        if p.kafka_sasl_username and p.kafka_sasl_password:
            kwargs.update(
                security_protocol=p.kafka_security_protocol,
                sasl_mechanism=p.kafka_sasl_mechanism,
                sasl_plain_username=p.kafka_sasl_username,
                sasl_plain_password=p.kafka_sasl_password.get_secret_value(),
            )

        self._consumer = AIOKafkaConsumer(*p.kafka_topics, **kwargs)

        logger.info(
            "Connecting to Kafka",
            extra={"env": p.name, "bootstrap_servers": p.kafka_bootstrap_servers, "topics": p.kafka_topics},
        )

        try:
            await self._consumer.start()
            self._connected = True
            self._running = True
            logger.info("Kafka consumer started", extra={"env": p.name, "topics": p.kafka_topics})
            await self._consume_loop()
        except KafkaError as e:
            logger.error("Kafka connection failed", extra={"env": p.name, "error": str(e)})
            raise
        finally:
            self._running = False
            self._connected = False
            if self._consumer:
                await self._consumer.stop()
                logger.info("Kafka consumer stopped", extra={"env": p.name})

    async def stop(self) -> None:
        self._running = False
        if self._consumer:
            await self._consumer.stop()

    async def _consume_loop(self) -> None:
        assert self._consumer is not None
        env = self._pipeline.name

        async for msg in self._consumer:
            if not self._running:
                break

            self._messages_consumed += 1
            event = msg.value

            if not isinstance(event, dict):
                logger.warning(
                    "Skipping non-dict message",
                    extra={"env": env, "topic": msg.topic, "offset": msg.offset, "type": type(event).__name__},
                )
                await self._consumer.commit()
                continue

            # Schema validation (if configured) — runs before filtering
            if self._schema_validator:
                is_valid, schema_error = self._schema_validator.validate(event)
                if not is_valid:
                    self._messages_invalid += 1
                    logger.warning(
                        "Event failed schema validation — skipped",
                        extra={"env": env, "topic": msg.topic, "offset": msg.offset, "error": schema_error},
                    )
                    await self._consumer.commit()
                    continue

            should_forward, reason = self._filter_engine.should_forward(event)

            log_extra = {
                "env": env,
                "topic": msg.topic,
                "partition": msg.partition,
                "offset": msg.offset,
                "forwarded": should_forward,
                "filter_reason": reason,
            }

            if should_forward:
                kafka_metadata = {
                    "env": env,
                    "topic": msg.topic,
                    "partition": msg.partition,
                    "offset": msg.offset,
                    "timestamp_ms": msg.timestamp,
                }
                await self._forwarder.forward(event, kafka_metadata)
                logger.debug("Message forwarded", extra=log_extra)
            else:
                self._messages_filtered += 1
                logger.debug("Message filtered out", extra=log_extra)

            await self._consumer.commit()

    @staticmethod
    def _deserialize(raw: bytes) -> dict | str:
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("Failed to deserialize message as JSON", extra={"error": str(e)})
            return raw.decode("utf-8", errors="replace")
