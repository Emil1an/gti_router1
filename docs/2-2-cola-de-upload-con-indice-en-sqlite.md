# Story 2.2: Cola de upload con índice en SQLite

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router**,
I want **una cola de upload cuyo índice de estado viva en SQLite durable**,
so that **el pipeline y el uploader estén desacoplados y la cola sobreviva reinicios y cortes de energía sin perder segmentos**.

## Acceptance Criteria

1. **Índice en SQLite (AR2 / D3):** `src/storage/db.py` define el acceso a una base **SQLite** (no JSON) que persiste el índice de cola/estado de cada segmento. Es la **única** vía de persistencia de cola/estado del proyecto. El esquema incluye al menos: `id`, `camera_id`, `segment_path`, `s3_key` (nullable), `state` (`pending`/`uploading`/`uploaded`/`failed`), `size_bytes`, `created_at`, `enqueued_at`, `uploaded_at` (nullable), `attempts`, `last_error` (nullable). Las transiciones de estado son **transaccionales** (commit atómico).
2. **Durabilidad ante cortes:** la BD se abre en modo durable (p. ej. WAL + `synchronous=NORMAL/FULL`) de modo que un corte de energía no corrompa el índice ni pierda items confirmados. Un item marcado `uploaded` nunca se re-sube; uno `pending`/`failed` se recupera al reiniciar.
3. **`UploadQueue` desacoplada:** `src/upload/queue.py` define `UploadQueue` con `async enqueue(camera_id, segment_path, created_at)` (contrato del callback HLS) y un worker `async start()`/`async stop()` que consume items `pending` y los entrega al `S3Uploader` (de la 2.1). El productor (pipeline) y el consumidor (worker) están desacoplados vía la cola persistida.
4. **Carga al iniciar + huérfanos:** al arrancar, la cola **carga la cola persistida** desde SQLite y **escanea el buffer** en busca de segmentos huérfanos (archivos `.ts` en disco sin fila en la BD, o filas `uploading` que quedaron a medias) y los normaliza a `pending` para no perder video.
5. **Idempotencia/consistencia:** encolar el mismo `segment_path` dos veces no crea duplicados (constraint UNIQUE o upsert); marcar `uploaded` registra `s3_key` y `uploaded_at`.
6. **Métricas:** expone `queue_size`, `items_processed`, `items_pending` (y se loguean con `camera_id` en contexto). Nombres de métrica en `snake_case` con sufijo de unidad cuando aplique.
7. **Tests:** `tests/storage/test_db.py` y `tests/upload/test_queue.py` validan: persistencia y recuperación tras "reinicio" (cerrar/reabrir la BD), transición de estados transaccional, detección de huérfanos, idempotencia de `enqueue`, y consumo por el worker con un `S3Uploader` mockeado (o `moto`). Sin hardware.

## Tasks / Subtasks

- [ ] **Task 1: Capa SQLite (`storage/db.py`)** (AC: #1, #2, #5)
  - [ ] Crear el esquema `upload_queue` (columnas del AC #1) con índice por `state` y por `camera_id`; UNIQUE en `segment_path`
  - [ ] Abrir la BD en modo durable (WAL, `synchronous`); ruta configurable (default bajo el dir de estado)
  - [ ] API de acceso: `add_segment()`, `mark_uploading()`, `mark_uploaded(s3_key)`, `mark_failed(error)`, `next_pending()`, `counts()` — todas transaccionales
- [ ] **Task 2: `UploadQueue` y worker** (AC: #3)
  - [ ] `src/upload/queue.py`: `async enqueue(camera_id, segment_path, created_at)` que inserta `pending` en SQLite
  - [ ] Worker `async start()`/`async stop()` que toma `next_pending()`, marca `uploading`, llama `S3Uploader.upload_segment()`, marca `uploaded`/`failed`; respeta `asyncio.CancelledError` y limpia en `stop()`
- [ ] **Task 3: Carga inicial y huérfanos** (AC: #4)
  - [ ] Al iniciar, releer filas no terminales (`pending`/`uploading`→`pending`)
  - [ ] Escanear el directorio de buffer por `.ts` sin fila y registrarlos como `pending`
- [ ] **Task 4: Métricas y logging** (AC: #6)
  - [ ] Exponer `queue_size`/`items_processed`/`items_pending`; loguear transiciones con `camera_id`
- [ ] **Task 5: Tests** (AC: #7)
  - [ ] `tests/storage/test_db.py`: persistencia/recuperación, transacciones, idempotencia
  - [ ] `tests/upload/test_queue.py`: huérfanos, worker con `S3Uploader` mock/`moto`, durabilidad simulada (cerrar/reabrir)

## Dev Notes

**Desviación deliberada del PRD:** el PRD proponía un índice JSON; la arquitectura lo cambia a **SQLite** por durabilidad/integridad ante cortes de energía, consistente con GTI Gateway. Esta story materializa esa decisión (AR2 / D3). [Source: architecture-GTI_Router.md#Data Architecture (Estado local D3); epics.md#AR2]

### Decisiones de arquitectura aplicables
- **Estado local en SQLite (D3 / AR2):** índice de cola/backlog y estado de segmentos en `storage/db.py`, durable y transaccional; segmentos en FS. Es la frontera única de estado local. [Source: architecture-GTI_Router.md#Data Architecture / Architectural Boundaries (Frontera de estado local)]
- **Sostiene NFR7 (upload ≥99.5%) y resiliencia:** la durabilidad del índice es lo que garantiza no re-subir ni perder segmentos. [Source: architecture-GTI_Router.md#Decision Impact Analysis]
- **Contrato callback HLS→cola:** `(camera_id, segment_path, created_at)` — `enqueue` lo respeta. [Source: architecture-GTI_Router.md#Communication Patterns]
- **Desacople productor/consumidor:** el pipeline encola; el worker consume; sobreviven reinicios. [Source: epics.md#Story 2.2]

### Patrones obligatorios (de la 1.1 / arquitectura)
- **Logging:** journald + `camera_id` en contexto; métricas en `snake_case` con sufijo de unidad. [Source: architecture-GTI_Router.md#Process Patterns / Naming Patterns]
- **Errores tipados:** **prohibido** `raise Exception("...")` genérico; usar la jerarquía de `src/utils/errors.py`. [Source: architecture-GTI_Router.md#Format Patterns]
- **Shutdown:** el worker respeta `asyncio.CancelledError` y limpia en `stop()`; el flush/persistencia de la cola en shutdown se afina en 2.6. [Source: architecture-GTI_Router.md#Process Patterns]
- **Estructura:** una clase de servicio por módulo; `storage/db.py` es la única vía de persistencia de cola/estado. [Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera de estado local)]

### Notas de diseño
- Esta story **no** implementa la priorización 3:1 (eso es 2.5: dos colas `realtime`/`backlog`). Aquí la cola es FIFO simple sobre `pending`; 2.5 introducirá la clasificación realtime/backlog reutilizando este índice. No sobre-diseñar la priorización aquí, pero dejar el `state`/columnas que 2.5 pueda extender.
- El retry de upload lo añade 2.3 envolviendo la llamada a `S3Uploader`; aquí basta con marcar `failed` ante error (2.3 refinará la lógica de reintento y la cola "failed").
- La política FIFO de borrado de archivos (solo subidos) y el umbral de espacio son de la 2.4 — esta story solo persiste el estado.

### Anti-patrones a evitar
- ❌ Volver a un índice JSON · ❌ `raise Exception` genérico · ❌ borrar/re-subir segmentos por estado inconsistente · ❌ bloquear el event loop con I/O de disco no gestionada · ❌ duplicar lógica de persistencia fuera de `storage/db.py`. [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Testing standards
- `pytest` + `pytest-asyncio`; usar una BD SQLite temporal por test (tmp_path). Mock del `S3Uploader` o `moto` para el worker. CI en x86 sin hardware. [Source: architecture-GTI_Router.md#Development Experience / CI]

### Project Structure Notes
Archivos de esta story (paquetes ya creados vacíos en 1.1):
```
src/storage/
├── __init__.py
└── db.py            # SQLite: índice de cola/backlog y estado de segmentos  ← ESTA STORY
src/upload/
├── s3_client.py     # S3Uploader (Story 2.1)
└── queue.py         # UploadQueue + worker (FIFO simple)  ← ESTA STORY
tests/storage/test_db.py · tests/upload/test_queue.py   ← ESTA STORY
```
Variance: dos colas realtime/backlog 3:1 → 2.5; retry envolvente → 2.3; política de espacio/FIFO de archivos → 2.4; flush en shutdown → 2.6. [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 2 / Story 2.2]
- [Source: _bmad-output/gti-router/epics.md#AR2]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Data Architecture (Estado local D3)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Architectural Boundaries (Frontera de estado local)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Communication Patterns (callback HLS)]
- [Source: project-planning-artifacts/prd-GTI_Router-2026-01-22.md#FR4] (upload resumible con reintentos — base de cola durable)

### Notas de contexto del proyecto
- SQLite es parte de la stdlib de Python (`sqlite3`); no requiere nueva dependencia. Si se prefiere acceso async, envolver el I/O sin bloquear el loop (p. ej. `asyncio.to_thread`) en vez de añadir libs no fijadas en 1.1.

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
