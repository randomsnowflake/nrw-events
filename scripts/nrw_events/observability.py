"""Structured, redacted logging for unattended importer runs."""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path


LOGGER_NAME = "nrw_events"
_SENSITIVE = re.compile(r"([?&](?:api[_-]?key|token|key|authorization)=)[^&\s]+", re.IGNORECASE)


def redact(value: object) -> str:
    """Remove credential-like query values from diagnostics before logging."""
    return _SENSITIVE.sub(r"\1[REDACTED]", str(value))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname.lower(),
            "message": redact(record.getMessage()),
            "run_id": getattr(record, "run_id", ""),
            "source": getattr(record, "source", ""),
            "endpoint": redact(getattr(record, "endpoint", "")),
            "error_type": getattr(record, "error_type", ""),
        }
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(run_id: str, level: str, log_path: str = "", json_log_path: str = "") -> logging.Logger:
    """Configure stderr plus optional durable text/JSON-lines log files."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    text_format = logging.Formatter(
        "%(asctime)s %(levelname)s run=%(run_id)s source=%(source)s %(message)s",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(text_format)
    logger.addHandler(stderr)
    for path, formatter in ((log_path, text_format), (json_log_path, JsonFormatter())):
        if not path:
            continue
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.info("run started", extra={"run_id": run_id, "source": "runner"})
    return logger


def log(logger: logging.Logger, level: int, message: str, *, run_id: str, source: str,
        endpoint: str = "", error_type: str = "") -> None:
    logger.log(level, redact(message), extra={
        "run_id": run_id,
        "source": source,
        "endpoint": redact(endpoint),
        "error_type": error_type,
    })
