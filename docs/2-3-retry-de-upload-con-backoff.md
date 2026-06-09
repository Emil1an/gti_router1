# Story 2.3: Retry de upload con backoff

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router**,
I want **reintentar uploads fallidos con backoff inteligente reutilizando `@with_retry`**,
so that **fallas temporales de red no causen pérdida de video y se cumpla la tasa de éxito ≥99.5% (NFR7)**.

## Acceptance Criteria

1. **Reutiliza `@with_retry`:** el reintento usa el **único** decorator `@with_retry` de `src/utils/retry.py` (backoff exponencial 1→60s + jitter ±20%, `max_retries` configurable) — **prohibido** reimplementar retry o usar `time.sleep` ad-hoc. Se aplica a la llamada de upload del worker (`S3Uploader.upload_segment`).
2. **Solo errores transitorios:** se reintentan únicamente errores transitorios (timeout, connection reset, 5xx, throttling). Los errores **permanentes** (403, 404, credenciales inválidas) **NO** se reintentan: van directo a estado `failed`.
3. **Cola "failed":** al agotar `max_retries`, el segmento se mueve al estado/cola **`failed`** en SQLite (reutilizando el índice de 2.2), sin perder el archivo del buffer (no se borra). Queda disponible para inspección/reintento manual o futuro re-encolado.
4. **Persistencia de intentos:** cada intento incrementa `attempts` y registra `last_error` en el índice SQLite; el backoff no bloquea el event loop ni otras cámaras.
5. **Métricas:** emite `upload_success_count`, `upload_error_count`, `upload_retry_count` (y opcional `upload_failed_count`), en `snake_case`, logueadas con `camera_id` en contexto.
6. **Tests:** `tests/upload/test_upload_retry.py` valida con `moto`/mocks: éxito tras N fallos transitorios, agotamiento → `failed` (archivo conservado), error permanente (403/404) → `failed` sin reintentos, conteo correcto de métricas, y que el backoff/sleep esté mockeado (no espera real). Sin hardware.

## Tasks / Subtasks

- [ ] **Task 1: Clasificación de errores** (AC: #2)
  - [ ] Mapear excepciones de aioboto3/botocore a transitorio vs permanente (reutilizar/extender `S3UploadError` de 2.1)
  - [ ] Lista explícita de status permanentes (403/404/credenciales) que NO se reintentan
- [ ] **Task 2: Aplicar `@with_retry` al upload** (AC: #1, #4)
  - [ ] Envolver la llamada `S3Uploader.upload_segment()` del worker con `@with_retry` configurando `max_retries` y los tipos de excepción a reintentar (solo transitorios)
  - [ ] Asegurar que el sleep del backoff sea async (no bloquea el loop ni otras cámaras)
  - [ ] Incrementar `attempts`/`last_error` en SQLite por intento (vía API de 2.2)
- [ ] **Task 3: Estado `failed`** (AC: #3)
  - [ ] Al agotar reintentos o ante error permanente, `mark_failed()` en el índice; NO borrar el archivo del buffer
- [ ] **Task 4: Métricas** (AC: #5)
  - [ ] Contadores `upload_success_count` / `upload_error_count` / `upload_retry_count` (+ `upload_failed_count`), logueados con `camera_id`
- [ ] **Task 5: Tests** (AC: #6)
  - [ ] `tests/upload/test_upload_retry.py`: éxito-tras-N-fallos, agotamiento→failed (archivo intacto), permanente sin retry, métricas, sleep mockeado

## Dev Notes

**Esta story NO crea un mecanismo de retry nuevo: aplica el `@with_retry` definido en la Story 1.1 a la ruta de upload. Si necesitas tocar `src/utils/retry.py`, probablemente estás haciendo algo mal — el decorator ya existe y es la única fuente de retry del proyecto.**

### Decisiones de arquitectura aplicables
- **Patrón único `@with_retry` (AR7):** backoff exponencial + jitter para toda operación de red; ningún agente lo reimplementa. [Source: architecture-GTI_Router.md#Process Patterns; epics.md#AR7]
- **Permanentes no se reintentan:** errores 403/404 NO se reintentan; segmentos fallidos → cola "failed". [Source: architecture-GTI_Router.md#API & Communication Patterns]
- **NFR7 (upload ≥99.5%):** el retry transitorio + buffer durable es lo que sostiene la tasa de éxito objetivo. [Source: epics.md#Epic 2 (NFR7); architecture-GTI_Router.md#Decision Impact Analysis]
- **Índice SQLite (de 2.2):** el estado `failed`, `attempts` y `last_error` viven en `storage/db.py`. [Source: architecture-GTI_Router.md#Data Architecture]

### Patrones obligatorios (de la 1.1 / arquitectura)
- **Retry:** `@with_retry` único (1→60s + jitter ±20%, `max_retries` configurable). **Prohibido** `time.sleep`/retry ad-hoc. [Source: architecture-GTI_Router.md#Process Patterns / Enforcement Guidelines]
- **Errores tipados:** distinguir transitorio/permanente con tipos de `src/utils/errors.py`; **prohibido** `raise Exception(...)`. [Source: architecture-GTI_Router.md#Format Patterns]
- **No bloquear el event loop:** el backoff es async; el retry de una cámara no frena a las demás (aislamiento por cámara). [Source: architecture-GTI_Router.md#Concurrency & Fault Isolation / Enforcement Guidelines]
- **Métricas:** `snake_case` + logueo con `camera_id`. [Source: architecture-GTI_Router.md#Naming Patterns / Process Patterns]

### Notas de diseño
- El `max_retries` debe ser configurable vía `get_config()` (bloque upload/aws) — no hardcodear; default razonable documentado.
- Esta story define el destino `failed`; **no** define la política de re-encolado automático del backlog (eso es priorización 2.5) ni el borrado FIFO (2.4). Un `failed` conserva su archivo siempre.
- Coordinarse con 2.2: este retry vive en el worker de `UploadQueue`; las transiciones de estado usan la API transaccional de `storage/db.py`.

### Anti-patrones a evitar
- ❌ Reimplementar retry / `time.sleep` ad-hoc · ❌ reintentar 403/404 · ❌ borrar el archivo de un segmento `failed` · ❌ `raise Exception` genérico · ❌ bloquear el loop durante el backoff. [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Testing standards
- `pytest` + `pytest-asyncio`; `moto`/mocks para S3; mockear el sleep del backoff para no esperar en CI. CI en x86 sin hardware. [Source: architecture-GTI_Router.md#Development Experience / CI]

### Project Structure Notes
Esta story modifica/usa archivos existentes (no crea paquetes nuevos):
```
src/upload/queue.py    # worker: envuelve upload con @with_retry, marca failed  ← ESTA STORY (edita)
src/upload/s3_client.py # S3Uploader (de 2.1) — sin reimplementar retry dentro
src/utils/retry.py     # @with_retry (de 1.1) — REUTILIZAR, no modificar
src/storage/db.py      # estado failed/attempts/last_error (de 2.2)
tests/upload/test_upload_retry.py   ← ESTA STORY
```
Variance: re-encolado de backlog y ratio 3:1 → 2.5; FIFO de borrado → 2.4. [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 2 / Story 2.3]
- [Source: _bmad-output/gti-router/epics.md#AR7]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Process Patterns (Retry)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#API & Communication Patterns (permanentes no reintentables)]
- [Source: project-planning-artifacts/prd-GTI_Router-2026-01-22.md#FR4 / NFR7] (upload resumible con reintentos; éxito ≥99.5%)

### Notas de contexto del proyecto
- El `@with_retry` ya fue definido y testeado en la Story 1.1 (éxito tras N fallos, agotamiento, respeto del backoff con clock mockeado); esta story confía en ese comportamiento y solo lo aplica.

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
