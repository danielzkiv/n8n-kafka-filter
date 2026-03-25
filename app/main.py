from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import EventFilterConfig, PipelineConfig, get_settings
from app import filter_store
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
    filter_engine: FilterEngine
    forwarder: WebhookForwarder
    schema_validator: SchemaValidator | None
    consumer: KafkaConsumerService | None        # None when using HTTP ingest only
    task: asyncio.Task | None = field(default=None, repr=False)


# Keyed by pipeline name for O(1) lookup in the ingest endpoint
_pipelines: dict[str, PipelineState] = {}


def _build_pipeline(pc: PipelineConfig) -> PipelineState:
    filter_engine = FilterEngine.from_pipeline(pc)
    forwarder = WebhookForwarder(pc)
    schema_validator = SchemaValidator.from_pipeline(pc) if pc.event_schemas else None
    consumer = KafkaConsumerService(pc, filter_engine, forwarder, schema_validator) if pc.kafka_enabled else None
    return PipelineState(
        config=pc,
        filter_engine=filter_engine,
        forwarder=forwarder,
        schema_validator=schema_validator,
        consumer=consumer,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipelines

    pipeline_configs = settings.get_pipelines()
    logger.info(
        "Starting service",
        extra={"service": settings.service_name, "pipelines": [p.name for p in pipeline_configs]},
    )

    for pc in pipeline_configs:
        state = _build_pipeline(pc)
        await state.forwarder.start()

        if pc.kafka_enabled:
            task = asyncio.create_task(state.consumer.start(), name=f"consumer-{pc.name}")
            state.task = task

            def on_done(t: asyncio.Task, name: str = pc.name) -> None:
                if t.cancelled():
                    logger.info("Consumer task cancelled", extra={"env": name})
                elif t.exception():
                    logger.error("Consumer task crashed", extra={"env": name, "error": str(t.exception())})

            task.add_done_callback(on_done)

        _pipelines[pc.name] = state
        logger.info(
            "Pipeline started",
            extra={
                "env": pc.name,
                "mode": "kafka+http" if (pc.kafka_enabled and pc.ingest_enabled)
                        else "kafka" if pc.kafka_enabled
                        else "http-ingest",
                "webhook_url": str(pc.n8n_webhook_url),
            },
        )

    yield

    logger.info("Shutting down all pipelines")
    for state in _pipelines.values():
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
    description="Multi-environment event forwarder: Kafka consumer or HTTP ingest → filter → n8n",
    version="3.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


# ── HTTP ingest endpoint (Lambda → Cloud Run) ────────────────────────────────

@app.post("/ingest/{env}")
async def ingest(env: str, request: Request) -> JSONResponse:
    """
    Receive an event from AWS Lambda (or any HTTP client) and process it
    through the pipeline's filter engine before forwarding to n8n.

    Required header: X-Ingest-Secret matching the pipeline's ingest_secret.
    """
    state = _pipelines.get(env)
    if not state:
        return JSONResponse({"error": f"pipeline '{env}' not found"}, status_code=404)

    # Auth
    if not state.config.ingest_secret:
        return JSONResponse({"error": f"HTTP ingest not enabled for pipeline '{env}'"}, status_code=403)

    provided = request.headers.get("X-Ingest-Secret", "")
    if provided != state.config.ingest_secret.get_secret_value():
        logger.warning("Ingest auth failed", extra={"env": env, "ip": request.client.host if request.client else "unknown"})
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    # Parse body
    try:
        event = await request.json()
    except Exception:
        return JSONResponse({"error": "request body must be valid JSON"}, status_code=422)

    if not isinstance(event, dict):
        return JSONResponse({"error": "event must be a JSON object"}, status_code=422)

    # Schema validation
    if state.schema_validator:
        is_valid, schema_error = state.schema_validator.validate(event)
        if not is_valid:
            logger.warning("Ingest event failed schema validation", extra={"env": env, "error": schema_error})
            return JSONResponse({"status": "invalid", "error": schema_error}, status_code=422)

    # Filter
    should_forward, reason = state.filter_engine.should_forward(event)
    if not should_forward:
        logger.debug("Ingest event filtered out", extra={"env": env, "reason": reason})
        return JSONResponse({"status": "filtered", "reason": reason})

    # Forward to n8n
    metadata = {
        "env": env,
        "source": "http-ingest",
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "event_type": event.get(state.config.event_type_field),
    }
    success = await state.forwarder.forward(event, metadata)

    if success:
        return JSONResponse({"status": "forwarded"})
    else:
        return JSONResponse({"status": "webhook_error"}, status_code=502)


# ── Probes & metrics ─────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> JSONResponse:
    if not _pipelines:
        return JSONResponse({"status": "starting"}, status_code=503)

    pipeline_statuses = {}
    overall_ok = True

    for name, state in _pipelines.items():
        if state.consumer:
            crashed = state.task and state.task.done() and not state.task.cancelled()
            error = str(state.task.exception()) if crashed and state.task.exception() else None
            ok = state.consumer.is_running and not crashed
            pipeline_statuses[name] = {"mode": "kafka", "running": state.consumer.is_running, "error": error}
        else:
            ok = True
            pipeline_statuses[name] = {"mode": "http-ingest", "running": True}

        if not ok:
            overall_ok = False

    return JSONResponse(
        {"status": "ok" if overall_ok else "degraded", "pipelines": pipeline_statuses},
        status_code=200 if overall_ok else 503,
    )


@app.get("/ready")
async def ready() -> JSONResponse:
    if not _pipelines:
        return JSONResponse({"status": "not_ready"}, status_code=503)

    # HTTP-ingest pipelines are always ready; Kafka pipelines wait for connection
    not_ready = [
        name for name, s in _pipelines.items()
        if s.consumer and not s.consumer.is_connected
    ]
    if not_ready:
        return JSONResponse({"status": "not_ready", "waiting_for": not_ready}, status_code=503)

    return JSONResponse({"status": "ready", "pipelines": list(_pipelines.keys())})


@app.get("/metrics")
async def metrics() -> JSONResponse:
    uptime = int(time.time() - _start_time)
    result: dict = {"uptime_seconds": uptime, "pipelines": {}}

    for name, state in _pipelines.items():
        stats = {**state.forwarder.stats, "webhook_url": str(state.config.n8n_webhook_url)}
        if state.consumer:
            stats.update(state.consumer.stats)
            stats["connected"] = state.consumer.is_connected
        result["pipelines"][name] = stats

    return JSONResponse(result)


# ── Config UI ────────────────────────────────────────────────────────────────

@app.get("/ui")
async def ui() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/api/pipelines")
async def api_get_pipelines() -> JSONResponse:
    """Return current filter config for all pipelines (no secrets)."""
    stored = filter_store.load()
    result = {}
    for name, state in _pipelines.items():
        saved = stored.get(name, {})
        ef = saved.get("event_filters", {k: {"filter_mode": v.filter_mode, "filter_rules": v.filter_rules}
                                            for k, v in state.config.event_filters.items()})
        result[name] = {
            "name": name,
            "event_type_field":    saved.get("event_type_field",    state.config.event_type_field),
            "default_filter_mode": saved.get("default_filter_mode", state.config.default_filter_mode),
            "filter_mode":         saved.get("filter_mode",         state.config.filter_mode),
            "filter_rules":        saved.get("filter_rules",        state.config.filter_rules),
            "event_filters":       ef,
        }
    return JSONResponse(result)


@app.put("/api/pipelines/{env}/filters")
async def api_update_filters(env: str, request: Request) -> JSONResponse:
    """Update filter config for a pipeline and hot-reload the filter engine."""
    state = _pipelines.get(env)
    if not state:
        return JSONResponse({"error": f"Pipeline '{env}' not found"}, status_code=404)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=422)

    # Persist to filter_config.json
    filter_store.set_pipeline(env, data)

    # Update the live pipeline config
    state.config.filter_mode         = data.get("filter_mode", "none")
    state.config.filter_rules        = data.get("filter_rules", [])
    state.config.event_type_field    = data.get("event_type_field", "event_type")
    state.config.default_filter_mode = data.get("default_filter_mode", "none")
    state.config.event_filters       = {
        k: EventFilterConfig(**v) for k, v in data.get("event_filters", {}).items()
    }

    # Hot-reload the filter engine
    state.filter_engine = FilterEngine.from_pipeline(state.config)

    logger.info("Filters updated via UI", extra={"env": env})
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_config=None,
        access_log=False,
    )
