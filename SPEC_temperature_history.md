# SPEC_temperature_history

> Produced by the Coordinator (Opus) from PLAN_FEATURE.md + Architect notes.
> Self-contained: the Implementer (Sonnet) should need nothing else.

## 1. Goal

Add **CPU temperature history monitoring**. A new async service samples the CPU
temperature once per minute, keeps a rolling in-memory history of the last 24 h
(1440 samples), and exposes it via the local console API at `GET /api/temperature`.
Sampling must never block the video-capture event loop and must be fully robust:
a read error or a bad snapshot must never stop the recorder or the router.

The temperature is **already sampled non-blockingly** by
`health/monitor.py::SystemMonitor` (via `asyncio.to_thread`) and reported to
Supabase by `HealthReporter`. This feature **reuses that reader** — it does not
add a new temperature source, no SQLite, and no disk writes (Zero-Disk-Write
mandate). It only reads `monitor.snapshot()`, appends into an in-memory
`collections.deque(maxlen=1440)` stored on `AppState`, and serves it read-only.

## 2. Acceptance criteria

- [ ] AC1 — A `TemperatureRecorder` service samples `monitor.snapshot()` every
  `interval_s` (default 60 s) and appends `{"celsius": <float|None>, "at": <iso8601 str>}`
  to `state.temperature_history`.
- [ ] AC2 — History is a fixed-size `deque(maxlen=…)`; once full, the oldest
  entry is evicted FIFO. Default cap 1440 (24 h @ 1/min). Lost on reboot (accepted).
- [ ] AC3 — The sampling loop never blocks the event loop (only reads an in-memory
  snapshot and `await asyncio.sleep`). A concurrent asyncio task keeps making
  progress while the recorder runs.
- [ ] AC4 — Robustness: if `monitor.snapshot()` returns `None` or raises, the loop
  logs and continues; the recorder and the router keep running.
- [ ] AC5 — `GET /api/temperature` returns
  `{"current_celsius": <float|null>, "sampled_at": <str|null>, "history": [{"celsius":.., "at":..}, ...]}`.
  `current_celsius`/`sampled_at` come from `monitor.snapshot()`; `history` is
  `list(state.temperature_history)`.
- [ ] AC6 — `start()`/`stop()` follow the exact `SystemMonitor` lifecycle pattern
  (background `asyncio.Task`, `_running` flag, cancel + await on stop).
- [ ] AC7 — Interval and history cap are configurable via `HealthConfig`
  (`config.schema` + `get_config()`); no hard-coded knobs.
- [ ] AC8 — `pytest -q` is green on Windows local dev AND in Docker (linux/arm64).

## 3. Files to touch (real paths)

| File | Change |
|------|--------|
| `src/health/state.py` | Add `from collections import deque` and field `temperature_history: deque = field(default_factory=lambda: deque(maxlen=1440))`. |
| `src/health/temperature_recorder.py` | **New.** `TemperatureRecorder(monitor, state, interval_s=60)` async service. |
| `src/web/local_api.py` | Add `TemperatureResponse` Pydantic model + `GET /api/temperature` endpoint. |
| `src/config/schema.py` | Add `temperature_sample_interval_s: int = 60` and `temperature_history_max: int = 1440` to `HealthConfig`. |
| `src/main.py` | Construct/start recorder as step `# 6a` (after monitor, before console); stop it in `shutdown()` just before `# 5. Stop the system monitor`. Add `self._temperature` field + import. |
| `tests/health/test_temperature_recorder.py` | **New.** Unit + robustness + non-blocking tests. |
| `tests/web/test_local_api.py` | Add `test_temperature` (endpoint shape + seeded history). |

## 4. Design detail

### 4.1 Config — `src/config/schema.py`

In `class HealthConfig`, under the existing `# Monitor (3.3)` block, add:

```python
    # Temperature history recorder (rolling in-memory, Zero-Disk-Write)
    temperature_sample_interval_s: Annotated[int, Field(ge=1, le=3600)] = 60
    temperature_history_max: Annotated[int, Field(ge=1, le=100_000)] = 1440
```

`Annotated` and `Field` are already imported in this module.

### 4.2 AppState — `src/health/state.py`

Add import at top (module currently imports only `from dataclasses import dataclass, field`):

```python
from collections import deque
```

Add field to `class AppState` (place it after the GPS block, before `per_camera`):

```python
    # ── Temperature history (rolling, in-memory only — Zero-Disk-Write) ──────────
    # Each entry: {"celsius": <float|None>, "at": <iso8601 str>}. Fixed maxlen so
    # the deque self-evicts FIFO. maxlen must be set at creation; the recorder
    # rebuilds it from config on start if a non-default cap is configured.
    temperature_history: deque = field(default_factory=lambda: deque(maxlen=1440))
```

> Note: `maxlen` is fixed at deque creation. The default here matches the config
> default (1440). The recorder is responsible for reconciling the deque to the
> configured `temperature_history_max` on `start()` (see 4.3) so a non-default
> config value is honored.

### 4.3 New service — `src/health/temperature_recorder.py`

Mirror `SystemMonitor`'s structure (background task, `_running` flag, contained
loop). Full module:

```python
"""CPU temperature history recorder.

Samples the CPU temperature once per ``interval_s`` and appends it to a rolling,
fixed-size in-memory deque on :class:`~health.state.AppState`. It does NOT read
hardware directly — it consumes the snapshot already produced non-blockingly by
:class:`~health.monitor.SystemMonitor`, so the loop only reads an in-memory value
and sleeps. History is memory-only (Zero-Disk-Write); it is lost on reboot, which
is acceptable because critical temperature data is already reported to Supabase by
:class:`~health.reporter.HealthReporter`.
"""

from __future__ import annotations

import asyncio
from collections import deque

from health.monitor import SystemMonitor
from health.state import AppState
from utils.logging import get_logger


class TemperatureRecorder:
    """Periodically records the current CPU temperature into a rolling history."""

    def __init__(
        self,
        monitor: SystemMonitor,
        state: AppState,
        interval_s: int = 60,
        history_max: int = 1440,
    ) -> None:
        self._monitor = monitor
        self._state = state
        self._interval = interval_s
        self._history_max = history_max
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._logger = get_logger(__name__)

    async def start(self) -> None:
        """Reconcile the history cap, take an initial sample, start the loop."""
        # Honor a non-default configured cap (deque maxlen is fixed at creation).
        if self._state.temperature_history.maxlen != self._history_max:
            self._state.temperature_history = deque(
                self._state.temperature_history, maxlen=self._history_max
            )
        self._running = True
        self._record_once()  # prime one sample so the history is non-empty
        self._task = asyncio.create_task(self._loop(), name="temperature-recorder")
        self._logger.info(
            "TemperatureRecorder started",
            extra={"interval_s": self._interval, "history_max": self._history_max},
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._logger.info("TemperatureRecorder stopped")

    def _record_once(self) -> None:
        """Append the current snapshot temperature. Never raises."""
        try:
            snap = self._monitor.snapshot()
            celsius = snap.temperature_celsius if snap is not None else None
            at = snap.sampled_at if snap is not None else None
            self._state.temperature_history.append({"celsius": celsius, "at": at})
        except Exception as exc:  # noqa: BLE001 — a bad sample must not kill the loop
            self._logger.error("Temperature record failed: %s", exc)

    async def _loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._interval)
                if not self._running:
                    break
                self._record_once()
        except asyncio.CancelledError:
            pass
```

Key behaviors the Implementer must preserve:
- The loop body only calls `self._monitor.snapshot()` (in-memory, non-blocking) and
  `asyncio.sleep` — **no `to_thread`, no I/O** here.
- `_record_once()` swallows every exception and logs it (AC4). If the snapshot is
  `None`, it still appends `{"celsius": None, "at": None}` (a real sample record).
- `stop()` cancels + awaits the task exactly like `SystemMonitor.stop()`.
- On `start()`, if the configured cap differs from the deque's current `maxlen`,
  the deque is rebuilt with the correct `maxlen`, preserving existing entries.

### 4.4 Endpoint — `src/web/local_api.py`

Add a response model alongside the others (after `QrResponse`):

```python
class TemperatureResponse(BaseModel):
    current_celsius: float | None = None
    sampled_at: str | None = None
    history: list[dict] = []
```

Add the endpoint inside `create_app`, in the `# ── Data endpoints ──` section
(e.g. right after `get_health`):

```python
    @app.get("/api/temperature", response_model=TemperatureResponse)
    async def get_temperature() -> TemperatureResponse:
        snap = monitor.snapshot()
        return TemperatureResponse(
            current_celsius=snap.temperature_celsius if snap else None,
            sampled_at=snap.sampled_at if snap else None,
            history=list(state.temperature_history),
        )
```

`state` and `monitor` are already captured by the `create_app` closure — no
signature change. The existing catch-all `@app.exception_handler(Exception)`
covers this route.

### 4.5 main.py integration — `src/main.py`

1. Import (with the other `health.*` imports, ~line 45–48):

```python
from health.temperature_recorder import TemperatureRecorder
```

2. Instance field in `__init__` (after `self._monitor` at ~line 86):

```python
        self._temperature: TemperatureRecorder | None = None
```

3. In `startup()`, insert a new `# 6a` block **between** `# 6. System monitor`
   (after `await self._monitor.start()`, ~line 121) and `# 6b. Local console`
   (~line 123):

```python
        # 6a. Temperature history recorder (rolling in-memory, best-effort).
        #     Consumes the monitor snapshot; a failure here never aborts startup.
        self._temperature = TemperatureRecorder(
            monitor=self._monitor,
            state=self._state,
            interval_s=self._cfg.health.temperature_sample_interval_s,
            history_max=self._cfg.health.temperature_history_max,
        )
        try:
            await self._temperature.start()
        except Exception as exc:  # noqa: BLE001 — non-essential subsystem
            self._logger.error("Temperature recorder failed to start (contained): %s", exc)
```

4. In `shutdown()`, stop it **immediately before** `# 5. Stop the system monitor`
   (~line 241), contained like the other services:

```python
        # 4b. Stop the temperature recorder (before the monitor it reads from)
        if self._temperature is not None:
            try:
                await self._temperature.stop()
            except Exception as exc:  # noqa: BLE001 — contained
                self._logger.error("Error stopping temperature recorder: %s", exc)
```

## 5. Quality gates that apply (from the skill)

- **Zero-Disk-Write** — history is an in-memory deque only; no SQLite, no files.
- **Async safety** — the loop reads an in-memory snapshot and `asyncio.sleep`s;
  no blocking call anywhere in `_loop`/`_record_once`.
- **Robustness / degraded-mode** — `_record_once` swallows all exceptions;
  `start()` in `main.py` is contained so a failure never aborts the router.
- **Config discipline** — interval + cap come from `HealthConfig` via `get_config()`.
- **Do not duplicate reporting** — no new Supabase writes; `HealthReporter`
  already reports `temperature_celsius`.

## 6. Tests

All new tests must **run on Windows** (no `platform.board` import, no Linux-only
deps). `asyncio_mode = "auto"` (async tests need no decorator).

### 6.1 `tests/health/test_temperature_recorder.py` (new)

Reuse the autouse `_health_config` fixture in `tests/health/conftest.py`
(sets `ROUTER_CONFIG` + `reset_config()`). Use a **fake monitor** so tests are
deterministic and hardware-independent — a tiny stub exposing `.snapshot()`:

```python
from health.state import AppState
from health.temperature_recorder import TemperatureRecorder

class _FakeSnap:
    def __init__(self, c, at="2026-07-13T00:00:00.000Z"):
        self.temperature_celsius = c
        self.sampled_at = at

class _FakeMonitor:
    def __init__(self, snap):
        self._snap = snap
    def snapshot(self):
        return self._snap
```

Cases:

- `test_appends_and_respects_maxlen` — create a recorder with a small
  `history_max` (e.g. 3) and a tiny `interval_s` (e.g. 0.01). `await start()`,
  then `await asyncio.sleep(...)` long enough for >3 samples, `await stop()`.
  Assert `len(state.temperature_history) == 3` (FIFO eviction) and each entry has
  keys `{"celsius", "at"}`. Confirms AC2.

- `test_start_reconciles_maxlen` — `AppState()` default deque has `maxlen == 1440`.
  Start a recorder with `history_max=5`; assert `state.temperature_history.maxlen == 5`.

- `test_non_blocking_loop` — start the recorder with a tiny interval; concurrently
  run a counter task (`while True: counter += 1; await asyncio.sleep(0)`); after a
  short real sleep assert the counter advanced by many iterations, proving the
  event loop is never blocked. Confirms AC3.

- `test_snapshot_none_is_recorded` — monitor returns `None`; run one cycle; assert
  the appended entry is `{"celsius": None, "at": None}` and the loop is still alive.

- `test_snapshot_raises_keeps_running` — monitor whose `.snapshot()` raises
  `RuntimeError`; start with tiny interval, sleep, then assert `stop()` completes
  cleanly and no exception propagated (history may be empty). Confirms AC4.

### 6.2 `tests/web/test_local_api.py` (extend)

The existing `client` fixture builds `create_app(state=..., monitor=..., cfg=...)`.
Add:

```python
def test_temperature(client: TestClient) -> None:
    r = client.get("/api/temperature")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"current_celsius", "sampled_at", "history"}
    assert isinstance(body["history"], list)
```

Also add a seeded-history assertion — mutate the fixture's `state` before the
request. Simplest: seed inside the test by reaching the app's captured state is
not exposed, so extend the `client` fixture to attach `state` (mirroring the
existing `c._hls_dir` pattern):

```python
    with TestClient(app) as c:
        c._hls_dir = hls_dir  # type: ignore[attr-defined]
        c._state = state       # type: ignore[attr-defined]
        yield c
```

Then:

```python
def test_temperature_history_seeded(client: TestClient) -> None:
    client._state.temperature_history.append({"celsius": 42.5, "at": "2026-07-13T00:00:00.000Z"})
    body = client.get("/api/temperature").json()
    assert body["history"][-1] == {"celsius": 42.5, "at": "2026-07-13T00:00:00.000Z"}
```

Confirms AC5.

## 7. Dependencies

None. `collections.deque`, `asyncio`, `pydantic`, and `fastapi.testclient` are
already in use. No new `pyproject.toml` entries.

## 8. Docker verification

```bash
docker build -t gti-router:test .
docker run --rm --shm-size=128m gti-router:test pytest -q
```

Both the local Windows `pytest -q` and the Docker run must be green.

## 9. Out of scope

- No new Supabase reporting — `HealthReporter` already reports `temperature_celsius`.
- No new temperature reader — reuse `SystemMonitor` only.
- No SQLite / disk persistence of history (Zero-Disk-Write); reboot loss accepted.
- No change to `create_app`'s signature, to `SystemMonitor`, or to `HealthReporter`.
- No changes to alert thresholds or the existing `/api/health` endpoint.
