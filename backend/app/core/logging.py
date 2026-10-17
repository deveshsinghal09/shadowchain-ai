"""Standard-library JSON logging with a bounded, explicit field allowlist."""

import json
import logging
from contextvars import ContextVar
from logging.config import dictConfig

from app.core.time import utc_now

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
SAFE_FIELDS = ("method", "route", "status_code", "duration_ms", "error_type", "event")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.name.startswith(("uvicorn", "websockets")) and (
            "WebSocket" in message or record.levelno < logging.INFO
        ):
            message = "websocket_transport_event"
        payload = {
            "timestamp": utc_now().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "request_id": request_id_context.get(),
        }
        payload.update({key: getattr(record, key) for key in SAFE_FIELDS if hasattr(record, key)})
        # Raw exception text, request bodies, query strings, headers, settings and keys
        # are intentionally excluded. Log exception class names through error_type.
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "json",
                }
            },
            "root": {"handlers": ["stdout"], "level": level},
            "loggers": {
                "uvicorn": {"handlers": [], "propagate": True},
                "uvicorn.error": {"handlers": [], "level": "INFO", "propagate": True},
                "uvicorn.access": {"handlers": [], "propagate": False},
                "websockets": {"handlers": [], "level": "WARNING", "propagate": True},
                "httpx": {"handlers": [], "level": "WARNING", "propagate": True},
            },
        }
    )
