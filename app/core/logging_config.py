from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

SENSITIVE_LOG_KEYS = (
    "api_key",
    "authorization",
    "cookie",
    "password",
    "raw_bytes",
    "secret",
    "token",
)
RESERVED_LOG_ATTRS = {
    "args",
    "asctime",
    "color_message",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in RESERVED_LOG_ATTRS or key.startswith("_"):
                continue
            payload[key] = _redact_log_value(key, value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(*, level: str, log_format: str) -> None:
    root_logger = logging.getLogger()
    formatter: logging.Formatter
    log_level = getattr(logging, level.upper(), logging.INFO)
    if log_format == "json":
        formatter = JsonLogFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        )
    if root_logger.handlers:
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)
        root_logger.setLevel(log_level)
        _configure_uvicorn_loggers(formatter, log_level)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)
    _configure_uvicorn_loggers(formatter, log_level)


def _configure_uvicorn_loggers(
    formatter: logging.Formatter,
    log_level: int,
) -> None:
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.setLevel(log_level)
        for handler in logger.handlers:
            handler.setFormatter(formatter)


def _redact_log_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(marker in lowered for marker in SENSITIVE_LOG_KEYS):
        return "[redacted]"
    return value
