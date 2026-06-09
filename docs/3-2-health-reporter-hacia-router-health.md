# Story 3.2: Health Reporter hacia router_health

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **administrador del sistema GTI**,
I want **que el Router reporte su salud a la tabla `router_health` cada 60s con métricas, GPS y estado por cámara**,
so that **pueda detectar problemas (CPU, temperatura, cola de upload, conectividad) antes de que causen pérdida de video**.

## Acceptance Criteria

1. **Reporter periódico:** existe `src/health/reporter.py` con la clase de servicio `HealthReporter` (`async start()` / `async stop()`) que cada **60s** (intervalo configurable vía `get_config()`) compone un health report y lo **inserta** en la tabla `router_health` (creada en Épica 0, Story 0.4).
2. **Métricas de sistema:** el report incluye CPU, memoria, disco, temperatura y uptime — provistos por el `SystemMonitor` de la Story 3.3 (no se duplica el muestreo de psutil aquí).
3. **Métricas de app:** incluye estado de la cola de upload (p. ej. `upload_queue` size, pendientes) y contadores de uploads, obtenidos del estado de app compartido.
4. **Conectividad:** incluye el estado de conectividad de `rtsp`, `s3` y `supabase` (booleanos/flags).
5. **GPS:** incluye un bloque `gps` (jsonb) con la última coordenada conocida (provista por `location/gps.py` cuando exista — Épica 6; mientras tanto, `null`/última conocida sin romper el contrato).
6. **Bloque `per_camera`:** incluye el array `per_camera` con un objeto por cámara `{camera_id, input_type, connected, streaming, last_segment_at, error}` (contrato fijo de health). [Source: architecture-GTI_Router.md#Communication Patterns]
7. **`reported_at`:** cada fila lleva `reported_at` en timestamptz UTC ISO-8601 con `Z`; el payload va en `snake_case` coincidiendo con las columnas de `router_health`.
8. **Modo degradado (cola local 1h):** si Supabase no está disponible, los reports se **encolan localmente** (máx **1h**, FIFO) y se envían en **batch** al reconectar; el envío nunca bloquea el event loop.
9. **No-bloqueante + retry:** toda inserción a Supabase usa `@with_retry` y `service_role`; jamás bloquea captura/upload. Errores permanentes no se reintentan; se loguean tipados.
10. **Tests:** `tests/health/test_reporter.py` verifica la composición del report (incluye `gps` y `per_camera`), el periodo de 60s (con clock mockeado), el encolado local y envío en batch tras caída, y el no-bloqueo. Sin red ni hardware.

## Tasks / Subtasks

- [ ] **Task 1: Componer el health report** (AC: #2, #3, #4, #5, #6, #7)
  - [ ] `src/health/reporter.py`: `HealthReporter` (`async start()`/`async stop()`) con loop de 60s configurable
  - [ ] Leer métricas de sistema del `SystemMonitor` (3.3) — no remuestrear psutil aquí
  - [ ] Componer métricas de app (cola/uploads), conectividad (`rtsp`/`s3`/`supabase`), `gps` (jsonb) y `per_camera` (array de `{camera_id, input_type, connected, streaming, last_segment_at, error}`)
  - [ ] Serializar en `snake_case`, `reported_at` UTC ISO-8601 con `Z`
- [ ] **Task 2: Inserción en `router_health`** (AC: #1, #9)
  - [ ] Insert en `router_health` con `router_id` (del registro 3.1) usando el cliente Supabase `service_role` + `@with_retry`
  - [ ] No-bloqueante: el loop nunca espera bloqueando el event loop
- [ ] **Task 3: Modo degradado con cola local (1h)** (AC: #8)
  - [ ] Buffer local FIFO de reports con cap temporal de **1h**; descartar lo más viejo al exceder
  - [ ] Al reconectar Supabase, drenar la cola en **batch** (insert múltiple) preservando orden temporal
- [ ] **Task 4: Errores tipados y métricas** (AC: #9)
  - [ ] Usar excepciones de `src/utils/errors.py`; emitir métricas con sufijo de unidad donde aplique
- [ ] **Task 5: Tests** (AC: #10)
  - [ ] `tests/health/test_reporter.py` con clock y cliente Supabase mockeados: composición, periodo, encolado/batch, no-bloqueo

## Dev Notes

**Prerrequisito (Épica 0):** esta story DEPENDE de la **Story 0.4**, que crea la tabla **`router_health`** con `id uuid PK`, `router_id uuid NOT NULL → routers.id (CASCADE)`, métricas (cpu/mem/disk/temp/uptime/latencias/connectivity/upload_queue), `gps jsonb`, `per_camera jsonb`, `services_status jsonb`, `reported_at`, e índice `router_health(router_id, reported_at desc)`. Sin esa migración no hay tabla donde insertar. **`router_health` NO existía** en el esquema brownfield (solo `gateway_health`). [Source: epics.md#Story 0.4] [Source: gtisatelites-brownfield-database.md#65 / #83 / #118]

### Contrato de health (de la arquitectura)
- El health report tiene estructura fija con bloque `per_camera` (array de `{camera_id, input_type, connected, streaming, last_segment_at, error}`). [Source: architecture-GTI_Router.md#Communication Patterns]
- Reporte cada **60s** con CPU/temperatura/conectividad/cola de upload/GPS (FR8). [Source: epics.md#Epic 3 (FR8)] [Source: architecture-GTI_Router.md#Requirements Overview]
- `router_health` es la tabla destino, **no** se reusa `gateway_health`. [Source: gtisatelites-brownfield-database.md#118] [Source: epics.md#Story 0.4]

### Relación con otras stories de la Épica 3
- **3.1 (registro):** provee el `router_id`/`gateway_id` para asociar el health a `routers`.
- **3.3 (monitor):** provee las métricas de sistema (CPU/RAM/disco/temperatura). El reporter **consume** del monitor; no remuestrea.
- **3.6 (modo degradado):** la cola local de 1h y el flag `supabase_connected` son el mismo mecanismo compartido; coordinar para no duplicar buffers.
- **3.5 (watchdog):** independiente; el reporter no envía `sd_notify`.

### Patrones obligatorios (heredados de 1.1)
- **Supabase no-bloqueante y degradable:** modo degradado obligatorio; nunca bloquear el event loop esperando Supabase. [Source: architecture-GTI_Router.md#Process Patterns / Enforcement Guidelines]
- **Retry único `@with_retry`** (backoff 1→60s + jitter ±20%); permanentes no se reintentan. [Source: architecture-GTI_Router.md#Process Patterns]
- **Escritura con `service_role`** (bypassa RLS); secretos solo por env. [Source: epics.md#Story 0.7] [Source: architecture-GTI_Router.md#Authentication & Security]
- **Errores tipados** (prohibido `Exception` genérico); **métricas** `snake_case` + sufijo de unidad (`*_percent`, `*_celsius`, `*_bytes`, `*_ms`). [Source: architecture-GTI_Router.md#Format / Naming Patterns]
- **Tiempo:** UTC ISO-8601 con `Z` en payloads; JSON en `snake_case`. [Source: architecture-GTI_Router.md#Format Patterns]

### Frontera cloud
`health/` es de los únicos módulos que hablan con Supabase; encapsular tras `@with_retry` y modo degradable. [Source: architecture-GTI_Router.md#Architectural Boundaries]

### Testing standards
- `pytest` + `pytest-asyncio`; clock/sleep y cliente Supabase mockeados; sin red ni hardware. [Source: architecture-GTI_Router.md#Testing Framework / CI]

### Project Structure Notes
```
src/health/
├── registration.py   (Story 3.1)
├── reporter.py       ← ESTA STORY (HealthReporter: 60s, per_camera, cola local 1h)
├── monitor.py        (Story 3.3 — provee métricas de sistema)
└── watchdog.py       (Story 3.5)
tests/health/test_reporter.py   ← ESTA STORY
```
[Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 3 / Story 3.2]
- [Source: _bmad-output/gti-router/epics.md#Story 0.4]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Communication Patterns]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Implementation Patterns & Consistency Rules]
- [Source: project-planning-artifacts/gtisatelites-brownfield-database.md#6 / #8 (Health del Router)]

### Notas de contexto del proyecto
- Reutilizar `@with_retry`, logging y errores de 1.1; reutilizar el cliente Supabase `service_role` y la cola local de 3.1/3.6. [Source: stories/1-1-scaffold-del-proyecto-logging-y-retry.md]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
