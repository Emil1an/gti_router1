"""Tests for src/utils/logging.py."""

from __future__ import annotations

import json
import logging

import pytest

from utils.logging import CameraContextFilter, get_logger, setup_logging


def test_setup_logging_is_idempotent() -> None:
    """Calling setup_logging twice must not add duplicate handlers."""
    root = logging.getLogger()
    initial_count = len(root.handlers)
    setup_logging()
    setup_logging()
    assert len(root.handlers) == max(initial_count, 1)


def test_get_logger_returns_named_logger() -> None:
    logger = get_logger("test.module")
    assert logger.name == "test.module"


def test_camera_context_filter_injects_camera_id(caplog: pytest.LogCaptureFixture) -> None:
    """Records emitted through a camera-bound logger must contain camera_id."""
    logger = get_logger("test.cam", camera_id="cam-01")

    with caplog.at_level(logging.INFO, logger="test.cam"):
        logger.info("stream started")

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.camera_id == "cam-01"  # type: ignore[attr-defined]


def test_camera_context_filter_does_not_override_existing(caplog: pytest.LogCaptureFixture) -> None:
    """If camera_id is already on the record, the filter must not overwrite it."""
    filt = CameraContextFilter("cam-default")
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=(), exc_info=None,
    )
    record.camera_id = "cam-explicit"  # type: ignore[attr-defined]
    filt.filter(record)
    assert record.camera_id == "cam-explicit"  # type: ignore[attr-defined]


def test_log_message_smoke(caplog: pytest.LogCaptureFixture) -> None:
    """Smoke test: logging with extra dict does not raise."""
    logger = get_logger("test.smoke")
    with caplog.at_level(logging.DEBUG, logger="test.smoke"):
        logger.debug("probe ok", extra={"codec": "H.264", "fps": 25})
    assert caplog.records[0].message == "probe ok"
