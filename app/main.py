from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import PipelineConfig, get_settings
from app.consumer import KafkaConsumerService
from app.filter_engine import FilterEngine
from app.logging_config import configure_logging
from app.schema_validator import SchemaValidator
from app.webhook import WebhookForwarder

settings = get_settings()
configure_logging(settings.service_name, settings.log_level)

logger = logging.getLogger(__name__)

_start_time = time.time()


@dataclass
class PipelineState:
    config: PipelineConfig
    consumer: KafkaConsumerService
    forwarder: WebhookForwarder
    task: asyncio.Task | None = field(default=None, repr=False)


_pipelines: list[PipelineState] = []


def _build_pipeline(pipeline_config: PipelineConfig) -> PipelineState:
    filter_engine = FilterEngine.from_pipeline(pipeline_config)
    forwarder = WebhookForwarder(pipeline_config)
    schema_validator = SchemaValidator.from_pipeline(pipeline_config) if pipeline_config.event_schemas else None
    consumer = KafkaConsumerService(pipeline_config, filter_engine, forwarder, schema_validator)
    return PipelineState(config=pipeline_config, consumer=consumer, forwarder=forwarder)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipelines

    pipeline_configs = settings.get_pipelines()
    logger.info("Starting service", extra={"service": settings.service_name, "pipelines": [p.name for p in pipeline_configs]})

    # Build and start all pipelines
    for pc in pipeline_configs:
        state = _build_pipeline(pc)
        await state.forwarder.start()

        task = asyncio.create_task(state.consumer.start(), name=f"consumer-{pc.name}")
        state.task = task

        def on_done(t: asyncio.Task, name: str = pc.name) -> None:
            if t.cancelled():
                logger.info("Consumer task cancelled", extra={"env": name})
            elif t.exception():
                logger.error("Consumer task crashed", extra={"env": name, "error": str(t.exception())})

        task.add_done_callback(on_done)
        _pipelines.append(state)

        logger.info(
            "Pipeline started",
            extra={
                "env": pc.name,
                "topics": pc.kafka_topics,
                "filter_mode": pc.filter_mode,
                "rules_count": len(pc.filter_rules),
                "webhook_url": str(pc.n8n_webhook_url),
            },
        )

    yield

    logger.info("Shutting down all pipelines")
    for state in _pipelines:
        if state.task and not state.task.done():
            state.task.cancel()
            try:
                await asyncio.wait_for(state.task, timeout=10)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        await state.forwarder.close()

    _pipelines.clear()
    logger.info("Service stopped")


app = FastAPI(
    title="Kafka → n8n Event Forwarder",
    description="Multi-environment Kafka consumer that forwards filtered events to n8n webhooks",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness probe — reports per-pipeline status."""
    if not _pipelines:
        return JSONResponse({"status": "starting"}, status_code=503)

    pipeline_statuses = {}
    overall_ok = True

    for state in _pipelines:
        crashed = state.task and state.task.done() and not state.task.cancelled()
        error = str(state.task.exception()) if crashed and state.task.exception() else None
        ok = state.consumer.is_running and not crashed

        pipeline_statuses[state.config.name] = {
            "running": state.consumer.is_running,
            "error": error,
        }
        if not ok:
            overall_ok = False

    status_code = 200 if overall_ok else 503
    return JSONResponse(
        {"status": "ok" if overall_ok else "degraded", "pipelines": pipeline_statuses},
        status_code=status_code,
    )


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe — ready once all pipelines have connected to Kafka."""
    if not _pipelines:
        return JSONResponse({"status": "not_ready"}, status_code=503)

    not_ready = [s.config.name for s in _pipelines if not s.consumer.is_connected]
    if not_ready:
        return JSONResponse({"status": "not_ready", "waiting_for": not_ready}, status_code=503)

    return JSONResponse({"status": "ready", "pipelines": [s.config.name for s in _pipelines]})


@app.get("/metrics")
async def metrics() -> JSONResponse:
    """Per-pipeline operational metrics."""
    uptime = int(time.time() - _start_time)
    result: dict = {"uptime_seconds": uptime, "pipelines": {}}

    for state in _pipelines:
        result["pipelines"][state.config.name] = {
            **state.consumer.stats,
            **state.forwarder.stats,
            "connected": state.consumer.is_connected,
            "topics": state.config.kafka_topics,
            "webhook_url": str(state.config.n8n_webhook_url),
        }

    return JSONResponse(result)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_config=None,
        access_log=False,
    )
