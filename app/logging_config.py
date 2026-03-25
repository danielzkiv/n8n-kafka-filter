from __future__ import annotations

import logging
import sys

from pythonjsonlogger import jsonlogger


class GCPJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter compatible with Google Cloud Logging structured logs."""

    def add_fields(self, log_record: dict, record: logging.LogRecord, message_dict: dict) -> None:
        super().add_fields(log_record, record, message_dict)
        # GCP expects "severity" not "levelname"
        log_record["severity"] = record.levelname
        log_record.pop("levelname", None)
        log_record.pop("level", None)

        # GCP expects "message" not "msg"
        if "message" not in log_record:
            log_record["message"] = log_record.pop("msg", "")


def configure_logging(service_name: str, log_level: str) -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level.upper())

    handler = logging.StreamHandler(sys.stdout)
    formatter = GCPJsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)
    root_logger.handlers = [handler]

    # Suppress noisy third-party loggers
    logging.getLogger("aiokafka").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    logging.getLogger(service_name).info(
        "Logging configured",
        extra={"service": service_name, "log_level": log_level},
    )
