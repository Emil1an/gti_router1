# Story 5.7: Estado operativo individual por cámara

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **administrador del sistema GTI**,
I want **ver el estado operativo individual de cada cámara/stream en el health report**,
so that **pueda diagnosticar qué fuente falla en un nodo multicámara**.

## Acceptance Criteria

1. **Bloque `per_camera` en health:** el health report (Story 3.2, hacia `router_health`) incluye un bloque `per_camera` que es un **array** con una entrada por cámara, con el contrato fijo `{camera_id, input_type, connected, streaming, last_segment_at, error}`.
2. **Semántica de los campos:** `connected` = la fuente está alcanzable/sincronizada (RTSP conectado o capturadora con señal); `streaming` = el pipeline está produciendo segmentos; `last_segment_at` = timestamp UTC ISO-8601 (`Z`) del último segmento; `error` = mensaje descriptivo cuando `connected`/`streaming` son `false` (o `null`). `input_type` refleja `rtsp_ip | capture_card`.
3. **Actualización ante eventos:** los estados se actualizan ante conexión/desconexión/recuperación de cada fuente (la supervisora por cámara de la Story 5.4 actualiza el estado de su cámara). Una cámara caída aparece con `connected/streaming=false` + `error`, sin alterar el estado de las demás.
4. **Persistencia en `router_health`:** el bloque `per_camera` se persiste en la columna `router_health.per_camera` (jsonb, Story 0.4) en cada reporte (cada 60s configurable), respetando el modo degradado de la Story 3.2 (encolar local si Supabase no responde).
5. **Multifuente:** el bloque cubre tanto cámaras IP (RTSP) como fuentes por capturadora (incluido el feed DJI de la Story 5.3: si no hay señal ⇒ `connected/streaming=false`).
6. **Formato y naming:** claves `snake_case` (coinciden con columnas/contrato), booleanos nativos, tiempos UTC ISO-8601 `Z` en el payload; logging con `camera_id`. El contrato `per_camera` es **el mismo** definido en los patrones de comunicación de la arquitectura.
7. **Errores tipados:** la recolección de estado no debe tumbar el health report; fallos parciales por cámara se reflejan en su `error` sin abortar el reporte global; **prohibido** `Exception` genérico.
8. **Tests (sin hardware):** tests que verifican: con N cámaras, `per_camera` tiene N entradas con el contrato correcto; una cámara caída ⇒ su entrada `false`+`error` sin afectar las otras; cámara recuperada ⇒ vuelve a `true`; el bloque se incluye en el payload a `router_health`. Todo en x86 en CI.

## Tasks / Subtasks

- [ ] **Task 1: Modelar el estado por cámara** (AC: #1, #2, #6)
  - [ ] Estructura/dataclass `PerCameraStatus` con `{camera_id, input_type, connected, streaming, last_segment_at, error}`
  - [ ] Serialización `snake_case`, booleanos nativos, tiempos UTC ISO-8601 `Z`
- [ ] **Task 2: Recolección del estado** (AC: #3, #5, #7)
  - [ ] Cada supervisora por cámara (5.4) publica/actualiza su estado (conexión/desconexión/recuperación, último segmento)
  - [ ] Cubrir RTSP y capturadora (incl. feed DJI sin señal); fallo parcial por cámara → `error`, no aborta el reporte
- [ ] **Task 3: Integrar en el HealthReporter** (AC: #4)
  - [ ] El `HealthReporter` (3.2) agrega el array `per_camera` al payload y lo persiste en `router_health.per_camera` (jsonb)
  - [ ] Respetar modo degradado (encolar local máx 1h si Supabase no responde)
- [ ] **Task 4: Tests** (AC: #8)
  - [ ] `tests/health/`: N entradas con contrato correcto; caída/recuperación de una sin afectar otras; bloque presente en el payload

## Dev Notes

**Esta story cierra la Épica 5: hace observable el aislamiento por cámara (5.4). El contrato `per_camera` ya está fijado por la arquitectura y la BD; aquí se llena y se reporta. Cumple FR23 y alimenta el panel de dispositivos de Satélites (E7).**

### Contrato `per_camera` (fijado por la arquitectura)
> Health report: estructura fija con bloque `per_camera` (array de `{camera_id, input_type, connected, streaming, last_segment_at, error}`).
[Source: architecture-GTI_Router.md#Communication Patterns (Health report)]

### Persistencia en `router_health` (de la BD)
- La Story 0.4 crea `router_health` con `per_camera jsonb` (entre otras: métricas cpu/mem/disk/temp, `gps jsonb`, `services_status jsonb`, `reported_at`). Esta story llena `per_camera`.
[Source: _bmad-output/gti-router/epics.md#Story 0.4 / DB3]

### Reuso del HealthReporter (E3)
- El `HealthReporter` (Story 3.2) ya inserta en `router_health` cada 60s (configurable), incluye `gps` y el bloque `per_camera`, encola local (máx 1h) si Supabase no responde, y hace llamadas **no-bloqueantes**. Esta story aporta el contenido de `per_camera`; no reimplementa el reporter.
[Source: _bmad-output/gti-router/epics.md#Story 3.2 / FR8]
[Source: architecture-GTI_Router.md#Process Patterns (Supabase no-bloqueante / modo degradado)]

### Origen del estado: las supervisoras por cámara (5.4)
- El aislamiento de la Story 5.4 (1 subprocess FFmpeg + 1 task supervisora por cámara) es la fuente de verdad del estado de cada cámara. La caída de una se refleja solo en su entrada `per_camera`.
- Estados consistentes con la auto-recuperación RTSP (3.4): `rtsp_connected`, último conectado, reintentos.
[Source: _bmad-output/gti-router/epics.md#Story 5.4 / 3.4]
[Source: architecture-GTI_Router.md#Concurrency & Fault Isolation (D2) / Architectural Boundaries (Frontera de cámara)]

### Formato (patrones obligatorios)
- Tiempo: UTC ISO-8601 con `Z` en payloads; `TIMESTAMPTZ`/jsonb en DB. JSON a Supabase en `snake_case`; booleanos nativos.
- Logging con `camera_id`; errores tipados (no `Exception` genérico).
[Source: architecture-GTI_Router.md#Format Patterns / Enforcement Guidelines]

### Consumo aguas abajo (contexto)
- El panel de dispositivos de Satélites (E7, Story 7.1) consume la salud resumida desde `router_health`. Esta story provee el detalle por cámara (FR23).
[Source: _bmad-output/gti-router/epics.md#Story 7.1 / FR23]

### Testing standards
- `pytest` + `pytest-asyncio`; mocks de cámaras/estado y de Supabase — sin hardware. CI en x86.
[Source: architecture-GTI_Router.md#Development Experience / CI]

### Anti-patrones a evitar
- ❌ que un fallo por cámara aborte el health report global · ❌ inventar un contrato distinto al `per_camera` definido · ❌ bloquear el event loop esperando Supabase · ❌ `Exception` genérico.
[Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Project Structure Notes
```
src/health/reporter.py   # HealthReporter (3.2) → agrega bloque per_camera y persiste en router_health.per_camera  ← ESTA STORY
src/camera/sources/*     # estado por fuente (5.1/5.4)
tests/health/            # espeja src/health
```
[Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 5 / Story 5.7]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Communication Patterns (Health report / per_camera)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Concurrency & Fault Isolation (D2)]
- [Source: _bmad-output/gti-router/epics.md#Story 0.4 (router_health.per_camera) / Story 3.2]
- [Source: prd-GTI_Router-2026-01-22.md#FR23]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
