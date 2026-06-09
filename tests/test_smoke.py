"""Smoke tests: import the package and call main() without errors."""

from __future__ import annotations

import importlib

import pytest


def test_import_main() -> None:
    """src/main.py must be importable without side effects."""
    mod = importlib.import_module("main")
    assert hasattr(mod, "main")


@pytest.mark.asyncio
async def test_main_runs_without_error() -> None:
    """async main() must complete without raising (it's a placeholder)."""
    from main import main
    await main()


def test_import_utils() -> None:
    import utils.errors  # noqa: F401
    import utils.logging  # noqa: F401
    import utils.retry  # noqa: F401


def test_import_config() -> None:
    import config.loader  # noqa: F401
    import config.schema  # noqa: F401
