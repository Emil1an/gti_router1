# Story 2.5: Priorización de backlog (ratio 3:1)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router**,
I want **priorizar el video en tiempo real sobre el backlog al reconectar, con ratio 3:1**,
so that **los operadores vean video actual mientras el histórico acumulado se recupera en segundo plano (FR6)**.

## Acceptance Criteria

1. **Dos colas lógicas:** `src/upload/queue.py` distingue dos clases de items sobre el índice SQLite de la 2.2: **`realtime`** (segmentos recién generados) y **`backlog`** (segmentos atrasados acumulados durante una desconexión). La clasificación se deriva del estado/antigüedad del segmento (p. ej. `created_at` vs ahora, o un umbral configurable), sin duplicar el almacenamiento — siguen siendo filas del mismo índice.
2. **Ratio 3:1 configurable:** el worker consume con un ratio **3:1** (3 segmentos `realtime` por cada 1 del `backlog`), configurable vía `get_config()`. El reparto es justo y determinista bajo el ratio.
3. **Drenado cuando una se agota:** si una de las dos colas se vacía, el worker consume **solo de la no vacía** (sin desperdiciar ciclos esperando la cola vacía) hasta que vuelva a haber items de ambas.
4. **Reparto justo multicámara:** el ratio convive con el aislamiento por cámara: el pool de upload compartido reparte de forma justa entre cámaras (round-robin) y, dentro de cada flujo, aplica el 3:1 realtime/backlog. [Compatible con la frontera de cámara de la arquitectura.]
5. **Métricas:** emite `realtime_queue_size`, `backlog_queue_size`, `backlog_oldest_age_seconds` (en `snake_case` + sufijo de unidad), logueadas con `camera_id`.
6. **Reutiliza retry/cliente:** sigue usando el `S3Uploader` (2.1) y `@with_retry` (2.3) sin reimplementarlos; esta story solo cambia el **orden de selección** de qué subir.
7. **Tests:** `tests/upload/test_priorization.py` valida: con ambas colas llenas se respeta 3:1, cuando una se agota se drena solo la otra, métricas correctas (incl. `backlog_oldest_age_seconds`), y reparto justo entre dos cámaras. Mocks/`moto`; sin hardware.

## Tasks / Subtasks

- [ ] **Task 1: Clasificación realtime/backlog** (AC: #1)
  - [ ] Definir el criterio realtime vs backlog (umbral de antigüedad configurable sobre `created_at`/`enqueued_at`)
  - [ ] Consultas al índice de 2.2 que devuelvan el siguiente `pending` de cada clase
- [ ] **Task 2: Scheduler 3:1** (AC: #2, #3)
  - [ ] Implementar el reparto 3:1 configurable en el worker (contador de cuota); si una clase se agota, drenar la otra
  - [ ] `ratio` desde `get_config()` con default 3:1 documentado
- [ ] **Task 3: Reparto justo multicámara** (AC: #4)
  - [ ] Asegurar round-robin justo entre `camera_id` en el pool compartido, con el 3:1 aplicado por flujo
- [ ] **Task 4: Métricas** (AC: #5)
  - [ ] `realtime_queue_size`, `backlog_queue_size`, `backlog_oldest_age_seconds`; loguear con `camera_id`
- [ ] **Task 5: Tests** (AC: #7)
  - [ ] `tests/upload/test_priorization.py`: ratio 3:1, drenado de la no-vacía, métricas, justicia entre 2 cámaras

## Dev Notes

**Esta story cambia el ORDEN de subida, no el mecanismo: reutiliza el `S3Uploader` (2.1), el índice SQLite (2.2) y `@with_retry` (2.3). No crea una segunda persistencia: realtime/backlog son una clasificación lógica sobre las mismas filas.**

### Decisiones de arquitectura aplicables
- **Ratio realtime/backlog 3:1 (D2):** pool de upload workers compartido con reparto round-robin justo; ratio realtime/backlog 3:1 por cámara; la caída de una cámara no afecta a las demás. [Source: architecture-GTI_Router.md#Concurrency & Fault Isolation (D2)]
- **FR6 (priorización 3:1 al reconectar):** 3 segmentos nuevos por 1 del backlog. [Source: epics.md#Epic 2 (FR6); prd FR6]
- **Frontera de cámara compartiendo cola:** cada cámara aísla su captura, pero la cola de upload es compartida con reparto justo. [Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera de cámara)]

### Patrones obligatorios (de la 1.1 / arquitectura)
- **Retry:** reutilizar `@with_retry` (de 2.3/1.1); no reimplementar. [Source: architecture-GTI_Router.md#Process Patterns]
- **Métricas:** `snake_case` + sufijo de unidad; loguear con `camera_id`. [Source: architecture-GTI_Router.md#Naming Patterns / Process Patterns]
- **Errores tipados:** **prohibido** `raise Exception(...)`. [Source: architecture-GTI_Router.md#Format Patterns]
- **No bloquear el loop:** el scheduler es async y no frena otras cámaras. [Source: architecture-GTI_Router.md#Concurrency & Fault Isolation]
- **Estado único en SQLite:** la clasificación se deriva del índice de 2.2; no crear segunda fuente de verdad. [Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera de estado local)]

### Notas de diseño
- "Dos colas" es lógico: pueden ser dos consultas/filtros sobre la misma tabla `upload_queue`, no dos tablas. El epic dice "dos colas (`realtime`/`backlog`)" — implementarlo como vistas/consultas evita duplicar estado y mantiene la durabilidad de 2.2.
- El criterio realtime/backlog (umbral de antigüedad) debe ser configurable y documentado; al reconectar tras una desconexión, los segmentos viejos pasan a `backlog` y los nuevos entran como `realtime`.
- `backlog_oldest_age_seconds` es clave para observabilidad de recuperación: mide cuánto histórico falta por drenar.
- No tocar el borrado FIFO (2.4) ni la durabilidad del índice (2.2): esta story solo selecciona el siguiente item a subir.

### Anti-patrones a evitar
- ❌ Segunda persistencia paralela al índice SQLite · ❌ reimplementar retry · ❌ esperar en una cola vacía en vez de drenar la otra · ❌ que una cámara monopolice el pool (romper el reparto justo) · ❌ `raise Exception` genérico. [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Testing standards
- `pytest` + `pytest-asyncio`; `moto`/mocks de S3; poblar el índice con items realtime/backlog de varias cámaras para verificar el reparto. CI en x86 sin hardware. [Source: architecture-GTI_Router.md#Development Experience / CI]

### Project Structure Notes
```
src/upload/queue.py    # añade clasificación realtime/backlog + scheduler 3:1  ← ESTA STORY (edita)
src/upload/s3_client.py # S3Uploader (2.1) — reutilizar
src/utils/retry.py     # @with_retry (1.1) — reutilizar vía 2.3
src/storage/db.py      # índice (2.2) — consultas realtime/backlog
tests/upload/test_priorization.py   ← ESTA STORY
```
Variance: el wiring E2E (callback HLS→enqueue, shutdown con flush) es la 2.6. [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 2 / Story 2.5]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Concurrency & Fault Isolation (D2, ratio 3:1)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Architectural Boundaries (Frontera de cámara)]
- [Source: project-planning-artifacts/prd-GTI_Router-2026-01-22.md#FR6] (priorización 3:1 al reconectar)

### Notas de contexto del proyecto
- El ratio 3:1 es un default; debe quedar configurable para ajustarse en piloto según ancho de banda (NFR11) sin tocar código.

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
