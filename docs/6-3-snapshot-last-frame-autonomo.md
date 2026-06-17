# Story 6.3: Snapshot last-frame autónomo

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **operador en GTI Satélites**,
I want **un snapshot JPEG (last-frame) periódico por cámara, generado de forma autónoma sin depender del Gateway**,
so that **pueda ver la imagen cruda de cada cámara aunque no haya detección ni Gateway vinculado**.

## Acceptance Criteria

1. **Snapshot periódico por cámara:** `src/pipeline/snapshot.py` genera un **JPEG last-frame** por cámara con frecuencia configurable (**default 10s**, NFR13), tomando el último frame disponible del stream/fuente (p. ej. extrayendo un frame con FFmpeg desde el stream o el segmento más reciente del buffer).
2. **Subida a S3:** sube el JPEG a S3 reutilizando el `S3Uploader` (`upload/s3_client.py`, aioboto3) bajo un key/prefijo por cámara coherente con `{user_id}/{router_id}/{camera_id}/` (p. ej. `.../last_frame.jpg`), con Content-Type `image/jpeg`.
3. **Actualiza `cameras` en Supabase:** tras subir, actualiza **`cameras.last_frame_url`** (URL del JPEG en S3) y **`cameras.last_frame_at`** (timestamp UTC ISO-8601 `Z`) por `camera_id`, vía Supabase (`service_role`, no-bloqueante, `@with_retry`).
4. **Autónomo sin Gateway:** funciona **aunque no haya Gateway vinculado** (sin `gateway_id`). El snapshot NO depende del Gateway ni de ninguna detección; corre como una task asyncio por cámara independiente del pipeline de upload de segmentos.
5. **SIN semántica de detección:** el snapshot **no lleva ninguna marca/metadato de detección**; es una vista cruda "sin analizar". (El marcado de origen "sin detección" del contrato cross-sistema lo formaliza la Story 6.4.)
6. **Resiliencia y modo degradado:** un fallo al generar/subir un snapshot no interrumpe la captura ni los demás snapshots; usa `@with_retry` para S3/Supabase; si S3/Supabase no responden, reintenta sin bloquear el event loop. Errores tipados (`SnapshotError`/`S3UploadError`), nunca `Exception` genérico.
7. **Aislamiento por cámara:** el snapshot respeta la frontera de aislamiento por cámara — el fallo del snapshot de una cámara no afecta a las demás ni al pipeline HLS.
8. **Tests sin hardware:** tests cubren generación periódica (con clock/intervalo mockeado), subida a S3 con `moto`, update de `cameras.last_frame_url`/`last_frame_at` con mock de Supabase, y operación sin `gateway_id`.

## Tasks / Subtasks

- [ ] **Task 1: Generación del JPEG last-frame** (AC: #1, #5, #6, #7)
  - [ ] `src/pipeline/snapshot.py`: task asyncio por cámara con intervalo configurable (default 10s)
  - [ ] Extraer el último frame (FFmpeg desde stream/segmento más reciente) a JPEG; sin metadato de detección
  - [ ] Errores tipados `SnapshotError` en `src/utils/errors.py`; aislamiento por cámara
- [ ] **Task 2: Subida a S3** (AC: #2, #6)
  - [ ] Reutilizar `S3Uploader` (no reimplementar) con key por cámara `{user_id}/{router_id}/{camera_id}/last_frame.jpg`, Content-Type `image/jpeg`, bajo `@with_retry`
- [ ] **Task 3: Actualización de `cameras` en Supabase** (AC: #3, #6)
  - [ ] Update de `cameras.last_frame_url` + `cameras.last_frame_at` por `camera_id` (Supabase `service_role`, no-bloqueante, `@with_retry`)
  - [ ] `last_frame_at` en UTC ISO-8601 `Z`; modo degradado si Supabase no responde
- [ ] **Task 4: Autonomía sin Gateway** (AC: #4)
  - [ ] Garantizar que la task corre sin `gateway_id` vinculado; documentar la independencia del Gateway
- [ ] **Task 5: Tests** (AC: #8)
  - [ ] `tests/pipeline/test_snapshot.py`: intervalo mockeado, `moto` para S3, mock de Supabase para `last_frame_url`/`last_frame_at`, caso sin `gateway_id`

## Dev Notes

**Esta story pertenece a la Épica 6 (`[ROUTER]`). El last-frame es la pieza autónoma "sin detección" del Router; el visor en Satélites es la Épica 7 (Story 7.3) y el frustum la Épica 8.**

### Dependencia de la Épica 0 (BD) — ANÓTALO
- **`cameras.last_frame_url`** se crea en la **Story 0.5** (Épica 0); **`cameras.last_frame_at`** YA existía. No se crean aquí. [Source: gtisatelites-brownfield-database.md#10 (last-frame: cameras.last_frame_url nueva) / #8 / epics.md#Story 0.5]
- **OJO (corrección de la arquitectura):** la arquitectura original mencionaba `camera_streams.last_frame_url`, pero esa columna **NO existe** y `camera_streams` es otra cosa (LEGACY, streams manuales del usuario). El last-frame del Router va en **`cameras.last_frame_url`**. [Source: gtisatelites-brownfield-database.md#13 / §8 / línea 67]
- El servicio Router escribe con `service_role`. [Source: epics.md#DB10]

### Patrones OBLIGATORIOS (de la Story 1.1 / arquitectura)
- **Reutilizar** el `S3Uploader` (`upload/s3_client.py`, Story 2.1) — no reimplementar el cliente S3. [Source: architecture-GTI_Router.md#Architectural Boundaries (frontera cloud única)]
- **Retry:** subidas S3 y updates Supabase bajo el único `@with_retry`. [Source: architecture-GTI_Router.md#Process Patterns]
- **Supabase no-bloqueante y degradable.** [Source: architecture-GTI_Router.md#Enforcement Guidelines]
- **Aislamiento por cámara:** task supervisora por cámara; el fallo de una no propaga. [Source: architecture-GTI_Router.md#Concurrency & Fault Isolation]
- **Logging:** incluir `camera_id` en el contexto. [Source: architecture-GTI_Router.md#Process Patterns]
- **Errores:** excepciones tipadas; **prohibido** `raise Exception("...")`. [Source: architecture-GTI_Router.md#Format Patterns]
- **Config:** intervalo del snapshot vía `get_config()` (default 10s, NFR13). [Source: architecture-GTI_Router.md#Process Patterns + NFR13]

### Decisión arquitectónica D5 (last-frame)
- Snapshot JPEG periódico **independiente por cámara** (default 10s, NFR13), subido a S3 + columna de last-frame; **autónomo: funciona sin Gateway vinculado; SIN semántica de detección.** [Source: architecture-GTI_Router.md#last-frame Snapshot (D5)]

### Anti-patrones a evitar
- ❌ `raise Exception("...")` genérico · ❌ retry ad-hoc con `time.sleep` · ❌ bloquear el event loop esperando S3/Supabase · ❌ reimplementar el cliente S3 · ❌ acoplar el snapshot al Gateway o a una detección. [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Modelo de datos (verificado)
- **`cameras.last_frame_url`** (`text`, nueva en Épica 0) + **`cameras.last_frame_at`** (timestamp, ya existía). [Source: gtisatelites-brownfield-database.md#8 / #10]
- S3 key por cámara coherente con el prefijo `{user_id}/{router_id}/{camera_id}/`. [Source: architecture-GTI_Router.md#Naming Patterns (S3 keys)]

### Testing standards
- `pytest` + `pytest-asyncio`; `moto` para S3; mock de Supabase. Intervalo con clock/sleep mockeado. Sin hardware. [Source: architecture-GTI_Router.md#Testing Framework / CI]

### Project Structure Notes
- Archivo objetivo: `src/pipeline/snapshot.py` (creado vacío en la Story 1.1). Tests en `tests/pipeline/`. `.gitignore` ya cubre `*.jpg` (Story 1.1). [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 6 / Story 6.3]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#last-frame Snapshot (D5)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Complete Project Directory Structure (pipeline/snapshot.py)]
- [Source: project-planning-artifacts/gtisatelites-brownfield-database.md#8 / #10 / #13 (cameras.last_frame_url/at; camera_streams es legacy)]
- [Source: prd-GTI_Router-2026-01-22.md#FR21, NFR13] (snapshot last-frame autónomo, default 10s)

### Notas de contexto del proyecto
- FR21 = generar y subir periódicamente un snapshot JPEG (last-frame) por cámara, autónomo y sin depender del Gateway. NFR13 = frecuencia configurable (default 10s). [Source: epics.md#FR Coverage Map (FR21: E6)]
- El visor de last-frame "sin detección" en Satélites es la **Story 7.3**; el marcado formal de origen del contrato es la **Story 6.4**. [Source: epics.md#Story 7.3 / #Story 6.4]
- Depende de la **Épica 0**: `cameras.last_frame_url` se agrega allí (Story 0.5). Esta story NO toca el esquema.

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
