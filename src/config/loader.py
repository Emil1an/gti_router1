"""Config loader — the single source of configuration for GTI Router.

Public API
----------
``get_config() -> RouterConfig``
    Load, validate and cache the configuration.  All other modules MUST call
    this function; reading YAML or ``os.environ`` directly is prohibited.

Config file resolution order (first found wins)
------------------------------------------------
1. ``/etc/gti-router/router.yaml``
2. ``/boot/router.yaml``            (SD-card boot partition — zero-terminal setup)
3. ``${ROUTER_CONFIG}``             (development / testing override)

First-boot copy
---------------
If ``/boot/router.yaml`` exists and ``/etc/gti-router/router.yaml`` does not,
the file is copied to ``/etc/gti-router/`` with mode 0600 owned by root.

``${ENV_VAR}`` expansion
------------------------
String values in the YAML that contain ``${VAR_NAME}`` are expanded from the
process environment before pydantic validation.  Secrets (AWS keys, Supabase
service-role key) must live exclusively in env vars (NFR9).
"""

from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from config.schema import RouterConfig
from utils.errors import ConfigValidationError

# ── Path constants ─────────────────────────────────────────────────────────────

_ETC_PATH = Path("/etc/gti-router/router.yaml")
_BOOT_PATH = Path("/boot/router.yaml")

# ── Module-level singleton ─────────────────────────────────────────────────────

_config_cache: RouterConfig | None = None

# ── Internal helpers ───────────────────────────────────────────────────────────

_ENV_RE = re.compile(r"\$\{([^}]+)\}")


def _expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` placeholders in string scalars."""
    if isinstance(value, str):
        def _replace(m: re.Match[str]) -> str:
            var = m.group(1)
            resolved = os.environ.get(var)
            if resolved is None:
                raise ConfigValidationError(
                    field=f"${{{var}}}",
                    reason=f"environment variable '{var}' is not set",
                )
            return resolved

        return _ENV_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    return value


def _resolve_config_path() -> Path:
    """Return the first existing config path according to precedence rules."""
    if _ETC_PATH.exists():
        return _ETC_PATH
    if _BOOT_PATH.exists():
        return _BOOT_PATH
    env_path = os.environ.get("ROUTER_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
        raise ConfigValidationError(
            field="ROUTER_CONFIG",
            reason=f"file pointed to by $ROUTER_CONFIG does not exist: {env_path}",
        )
    raise ConfigValidationError(
        field="router.yaml",
        reason=(
            "No configuration file found. "
            f"Checked: {_ETC_PATH}, {_BOOT_PATH}, $ROUTER_CONFIG"
        ),
    )


def _maybe_copy_boot_config() -> None:
    """Copy /boot/router.yaml → /etc/gti-router/ on first boot if needed."""
    if not _BOOT_PATH.exists():
        return
    if _ETC_PATH.exists():
        return
    dest_dir = _ETC_PATH.parent
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_BOOT_PATH, _ETC_PATH)
        _ETC_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        # Non-fatal: we'll still load from /boot directly.
        pass


def _load_raw(path: Path) -> dict[str, Any]:
    """Read and parse YAML; return a plain dict."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigValidationError(
            field="router.yaml",
            reason=f"cannot read config file '{path}': {exc}",
        ) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigValidationError(
            field="router.yaml",
            reason=f"YAML parse error in '{path}': {exc}",
        ) from exc
    if not isinstance(data, dict):
        raise ConfigValidationError(
            field="router.yaml",
            reason="top-level value must be a YAML mapping",
        )
    return data


def _validate(data: dict[str, Any]) -> RouterConfig:
    """Run pydantic validation; translate errors to ConfigValidationError."""
    try:
        return RouterConfig.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors()
        # Surface the first error with a clear field path.
        first = errors[0]
        field = ".".join(str(loc) for loc in first["loc"]) or "unknown"
        reason = first["msg"]
        raise ConfigValidationError(field=field, reason=reason) from exc


# ── Public API ─────────────────────────────────────────────────────────────────

def get_config(*, reload: bool = False) -> RouterConfig:
    """Load, validate and cache ``router.yaml``.

    This is the **only** authorised entry point to the application config.
    The result is cached for the lifetime of the process.

    Args:
        reload: if ``True``, discard the cached value and re-read the file.
                Useful in tests.

    Returns:
        A validated :class:`~config.schema.RouterConfig` instance.

    Raises:
        :class:`~utils.errors.ConfigValidationError`: on any parse, validation
            or missing-env-var error.  The service must not continue when this
            is raised (fail-fast, AC#4).
    """
    global _config_cache  # noqa: PLW0603

    if _config_cache is not None and not reload:
        return _config_cache

    _maybe_copy_boot_config()
    path = _resolve_config_path()
    raw = _load_raw(path)
    expanded = _expand_env(raw)
    _config_cache = _validate(expanded)
    return _config_cache


def reset_config() -> None:
    """Clear the cached config singleton (for use in tests only)."""
    global _config_cache  # noqa: PLW0603
    _config_cache = None
