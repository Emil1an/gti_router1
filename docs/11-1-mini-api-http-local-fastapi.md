# Story 11.1: Mini-API HTTP local (FastAPI) en el Router

Status: done

## Story

As a **consola local del Router**,
I want **una API HTTP local dentro del propio Router**,
so that **la UI táctil pueda leer datos reales del equipo (identidad, estado, cámaras) sin depender de la nube**.

## Acceptance Criteria

1. Existe un servidor **FastAPI + uvicorn** integrado en el proceso del Router (arranca/cae junto con `src/main.py`), con acceso en vivo a `AppState` y `SystemMonitor`.
2. El servidor escucha **solo en `127.0.0.1`** (loopback) — NO se expone en la LAN.
3. Endpoints read-only disponibles:
   - `GET /api/identity` → `serial_number`, `name`, `sku`, `firmware_version`, `router_id` (uuid), `gateway_id`.
   - `GET /api/health` → snapshot de `SystemMonitor` (CPU, RAM, temperatura, disco, uptime) + flags de conectividad (Supabase/S3/RTSP) + contadores de la cola de upload.
   - `GET /api/cameras` → fusión de la config de cámaras + el estado por cámara (`per_camera`).
4. Levantar la API **no degrada** el pipeline de captura (corre como tarea async no bloqueante).
5. Respuestas en JSON; errores con código claro (no vuelca trazas crudas).

## Tasks / Subtasks

- [ ] **Task 1: Dependencias y arranque** (AC: #1, #4)
  - [ ] Agregar `fastapi` + `uvicorn` al `pyproject.toml`
  - [ ] Crear `src/web/local_api.py` con la app FastAPI
  - [ ] Arrancar el server como tarea async desde `main.py` (junto a los demás servicios), apagarlo en el shutdown
- [ ] **Task 2: Inyección de estado** (AC: #1)
  - [ ] Pasar referencias a `AppState` y `SystemMonitor` a la app (dependencia/closure), sin copiar datos (lectura en vivo)
- [ ] **Task 3: Endpoints** (AC: #3, #5)
  - [ ] `/api/identity`, `/api/health`, `/api/cameras` con modelos Pydantic de respuesta
- [ ] **Task 4: Binding loopback** (AC: #2)
  - [ ] uvicorn `host="127.0.0.1"`, puerto configurable (default p.ej. 8770)
- [ ] **Task 5: Tests** (AC: all)
  - [ ] Tests con `TestClient` mockeando `AppState`/`SystemMonitor`

## Dev Notes

- **Bloqueante de casi toda la Épica 11:** las pantallas (11.7/11.8) y la capa de datos (11.5) consumen esta API.
- Hoy el Router NO tiene servidor web (verificado): es Python headless. Esto lo agrega de forma mínima dentro del mismo proceso, para leer `AppState` en memoria (si fuera proceso aparte, no vería el estado vivo).
- `SystemMonitor` ya calcula CPU/RAM/temp (vía `psutil`); `AppState` tiene `per_camera` y flags de conectividad; la cola vive en SQLite (`upload_queue`).
- Contexto completo: `_bmad-output/gti-router/epic-11-consola-local-router.md`.

## References

- [Source: epic-11-consola-local-router.md#Story 11.1]
- [Source: GTIservices/Router/gti_router1/src/main.py, src/health/state.py, src/health/monitor.py, src/storage/db.py]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code)

### Completion Notes List

- FastAPI app + uvicorn run as a non-blocking asyncio task inside the Router
  process, wired into `main.py` startup (step 6b) / shutdown (step 1b). Reads
  `AppState` + `SystemMonitor` live via closure (no copies). uvicorn signal
  handlers neutralised so `main.py` keeps control of shutdown. Bound to
  `127.0.0.1` (config `console.host`). Console failure never aborts the Router.
- Endpoints `/api/identity`, `/api/health`, `/api/cameras` with Pydantic models;
  clean JSON errors (no raw tracebacks).

### File List

- `pyproject.toml` (fastapi + uvicorn deps)
- `src/config/schema.py` (ConsoleConfig block)
- `src/web/__init__.py`
- `src/web/local_api.py`
- `src/web/server.py`
- `src/main.py` (startup/shutdown wiring)
- `tests/web/test_local_api.py`
