"""Structured logging for GTI Router, targeting systemd-journald.

Format
------
``{timestamp} [{LEVEL}] [{module}] {message}  {extra_json}``

Usage
-----
```python
from utils.logging import get_logger

logger = get_logger(__name__)
logger.info("Stream started", extra={"camera_id": "cam-01", "codec": "H.264"})
```

Log levels
----------
DEBUG   — low-level tracing (FFmpeg stderr, retry countdown, packet counts)
INFO    — normal lifecycle events (service start/stop, segment uploaded, connected)
WARNING — degraded operation that does not yet require intervention
ERROR   — failures that need operator attention or trigger retry/fallback
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, MutableMapping

# ── Journal handler ─────────────────────────────────────────────────────────────

try:
    from systemd.journal import JournalHandler as _JournalHandler  # type: ignore[import-untyped]
    _JOURNAL_AVAILABLE = True
except ImportError:  # not running on a systemd host (CI, dev laptop)
    _JournalHandler = None
    _JOURNAL_AVAILABLE = False


# ── Formatter ──────────────────────────────────────────────────────────────────

class _GTIFormatter(logging.Formatter):
    """Formats log records as  ``TIMESTAMP [LEVEL] [module] message  {json}``."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        base = super().format(record)

        # Collect any extra keys the caller passed (exclude standard LogRecord attrs)
        _standard = {
            "name", "msg", "args", "created", "filename", "funcName", "levelname",
            "levelno", "lineno", "module", "msecs", "message", "pathname",
            "process", "processName", "relativeCreated", "stack_info", "thread",
            "threadName", "exc_info", "exc_text",
        }
        extra: dict[str, Any] = {
            k: v for k, v in record.__dict__.items() if k not in _standard
        }
        if extra:
            return f"{base}  {json.dumps(extra, default=str)}"
        return base


# ── Context filter for camera_id ───────────────────────────────────────────────

class CameraContextFilter(logging.Filter):
    """Injects ``camera_id`` into every record produced by this logger's children.

    Attach per-camera loggers to isolate log lines by source:

    ```python
    logger = get_logger(__name__, camera_id="cam-01")
    ```
    """

    def __init__(self, camera_id: str) -> None:
        super().__init__()
        self.camera_id = camera_id

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if not hasattr(record, "camera_id"):
            record.camera_id = self.camera_id  # type: ignore[attr-defined]
        return True


# ── Public API ─────────────────────────────────────────────────────────────────

def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger once at application startup.

    Targets journald when available; falls back to stderr with timestamps.
    Call this exactly once, from ``src/main.py``.
    """
    root = logging.getLogger()
    if root.handlers:
        return  # already initialised

    root.setLevel(level)

    fmt = "%(asctime)s [%(levelname)s] [%(module)s] %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"

    if _JOURNAL_AVAILABLE:
        handler: logging.Handler = _JournalHandler()
        # journald adds its own timestamp; omit asctime to avoid duplication
        handler.setFormatter(_GTIFormatter("%(levelname)s [%(module)s] %(message)s"))
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_GTIFormatter(fmt, datefmt=datefmt))

    root.addHandler(handler)


def get_logger(name: str, camera_id: str | None = None) -> logging.Logger:
    """Return a named logger, optionally bound to a specific camera context.

    Args:
        name:      typically ``__name__`` of the calling module.
        camera_id: when provided, every record emitted by this logger will
                   carry ``camera_id`` in its extra JSON payload.
    """
    logger = logging.getLogger(name)
    if camera_id is not None:
        logger.addFilter(CameraContextFilter(camera_id))
    return logger
