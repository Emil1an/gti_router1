"""Thin async Supabase (PostgREST) client — the single cloud boundary for health.

Story 3.1 Task 1 calls for a shared Supabase helper used by both
:class:`~health.registration.DeviceRegistration` (3.1) and
:class:`~health.reporter.HealthReporter` (3.2).  It talks to the Supabase REST
API (PostgREST) using the **service_role** key (bypasses RLS).

Design choices
--------------
* **No new dependency:** uses the stdlib :mod:`urllib.request`, executed inside
  :func:`asyncio.to_thread` so the event loop is never blocked.  (The project
  pins no Supabase/HTTP library; tests mock this client at the method boundary.)
* **service_role + env-only secrets:** URL and key come from ``get_config()``
  (which expands them from environment variables — never YAML plaintext, NFR9).
* **Typed transient/permanent errors:** so callers can let ``@with_retry`` retry
  only transient failures (timeout / network / 5xx / 429) and fail fast on
  permanent ones (4xx validation / constraint / auth).

The class itself does **not** retry — that is the caller's job via the single
``@with_retry`` decorator (no retry logic is re-implemented here).
"""

from __future__ import annotations

import asyncio
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from config.loader import get_config
from utils.errors import SupabasePermanentError, SupabaseTransientError
from utils.logging import get_logger

# Network timeout for a single request (seconds).  Patchable in tests.
_REQUEST_TIMEOUT_S: float = 10.0


class SupabaseClient:
    """Minimal async PostgREST client scoped to ``insert`` / ``upsert`` writes."""

    def __init__(self) -> None:
        cfg = get_config()
        self._base_url = cfg.supabase.url.rstrip("/")
        self._rest_url = f"{self._base_url}/rest/v1"
        self._key = cfg.supabase.service_role_key
        self._logger = get_logger(__name__)

    # ── Public API ──────────────────────────────────────────────────────────────

    async def insert(self, table: str, row: dict[str, Any]) -> list[dict[str, Any]]:
        """Insert a single row, returning the representation."""
        return await self._request(table, row, prefer="return=representation")

    async def insert_batch(
        self, table: str, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Insert multiple rows in one request (used for batch health flush)."""
        return await self._request(table, rows, prefer="return=representation")

    async def upsert(
        self, table: str, row: dict[str, Any], on_conflict: str
    ) -> list[dict[str, Any]]:
        """Upsert a row on the given conflict column (e.g. ``serial_number``)."""
        return await self._request(
            table,
            row,
            params={"on_conflict": on_conflict},
            prefer="resolution=merge-duplicates,return=representation",
            method="POST",
        )

    async def select(
        self, table: str, params: dict[str, str]
    ) -> list[dict[str, Any]]:
        """GET rows from ``table`` using PostgREST filter ``params``.

        Example ``params``: ``{"status": "eq.pending", "camera_id": "in.(a,b)",
        "order": "issued_at.asc"}``.
        """
        return await self._request(table, None, params=params, method="GET")

    async def update(
        self, table: str, params: dict[str, str], patch: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """PATCH rows matching ``params`` with ``patch``; returns affected rows.

        Used for the atomic claim (``status=eq.pending`` → ``processing``) and
        the command feedback update.  An empty result means no row matched (e.g.
        another consumer already claimed it).
        """
        return await self._request(
            table, patch, params=params, prefer="return=representation", method="PATCH"
        )

    # ── Internal ────────────────────────────────────────────────────────────────

    async def _request(
        self,
        table: str,
        body: Any,
        params: dict[str, str] | None = None,
        prefer: str | None = None,
        method: str = "POST",
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._blocking_request, table, body, params, prefer, method
        )

    def _blocking_request(
        self,
        table: str,
        body: Any,
        params: dict[str, str] | None,
        prefer: str | None,
        method: str = "POST",
    ) -> list[dict[str, Any]]:
        """Perform one blocking HTTP request (runs in a worker thread)."""
        url = f"{self._rest_url}/{table}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        data = (
            json.dumps(body, default=str).encode("utf-8") if body is not None else None
        )
        headers = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer

        request = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_S) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            self._raise_for_status(exc, table)
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
            # No HTTP response at all → network/timeout → transient.
            raise SupabaseTransientError(
                f"Supabase request to '{table}' failed (network/timeout): {exc}"
            ) from exc

        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
        return [parsed]

    @staticmethod
    def _raise_for_status(exc: urllib.error.HTTPError, table: str) -> None:
        """Map an HTTP error code to a transient or permanent typed error."""
        status = exc.code
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            detail = ""

        if status >= 500 or status == 429:
            raise SupabaseTransientError(
                f"Supabase '{table}' transient HTTP {status}: {detail}"
            ) from exc
        raise SupabasePermanentError(
            f"Supabase '{table}' permanent HTTP {status}: {detail}"
        ) from exc
