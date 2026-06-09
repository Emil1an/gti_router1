# Story 2.6: Integración pipeline → upload (E2E)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router**,
I want **que los segmentos generados por el pipeline se encolen y suban automáticamente como tasks concurrentes**,
so that **el flujo captura→S3 sea continuo y sin intervención manual, y el shutdown no pierda datos**.

## Acceptance Criteria

1. **Wiring del callback HLS:** el callback de `HLSPipeline` (de la Story 1.4, contrato `(camera_id, segment_path, created_at)`) llama a `UploadQueue.enqueue()` (de la 2.2). Productor (pipeline) y consumidor (worker de upload) corren como **tasks asyncio concurrentes** desacopladas por la cola persistida.
2. **Flujo E2E observable:** el ciclo de vida de un segmento queda logueado de extremo a extremo: `creado → encolado → subido → confirmado`, con la métrica **`upload_latency_seconds`** (tiempo desde `created_at` hasta confirmación S3) y `camera_id` en contexto.
3. **Concurrencia multicámara:** con varias cámaras, cada `HLSPipeline` encola en la cola compartida y el worker pool sube respetando el reparto justo + ratio 3:1 (de 2.5) sin que una cámara bloquee a otra (aislamiento por cámara, D2).
4. **Graceful shutdown que espera uploads:** al recibir shutdown (`SIGTERM`/`SIGINT`), el worker **espera a que terminen los uploads en curso con un timeout máximo de 30s** (configurable) antes de cancelar; los items no subidos quedan `pending`/`failed` en SQLite (no se pierden) y la cola se **persiste** en SQLite. Tras el timeout, cancela limpiamente respetando `asyncio.CancelledError`.
5. **Recuperación al reiniciar:** tras un reinicio, la cola persistida + el escaneo de huérfanos (de 2.2) reanudan los uploads pendientes sin duplicar lo ya subido.
6. **Sin lógica en `main.py`:** la orquestación de tasks vive en los servicios (`start()`/`stop()`); `main.py` solo coordina. (La orquestación completa del Router se cierra en 1.5/3.7; aquí se integra el sub-sistema de upload.)
7. **Tests E2E:** `tests/upload/test_pipeline_upload_e2e.py` valida con `moto` y el pipeline (o un productor simulado que emita el callback con `tests/fixtures/sample.mp4`): segmento generado → encolado → subido a S3 mock → confirmado en SQLite; `upload_latency_seconds` emitida; shutdown espera uploads (≤30s) y persiste la cola; reinicio reanuda pendientes. Sin hardware.

## Tasks / Subtasks

- [ ] **Task 1: Conectar callback HLS → enqueue** (AC: #1)
  - [ ] Registrar el callback de `HLSPipeline` para que invoque `UploadQueue.enqueue(camera_id, segment_path, created_at)`
  - [ ] Arrancar pipeline y worker como tasks concurrentes vía sus `async start()`
- [ ] **Task 2: Observabilidad E2E** (AC: #2)
  - [ ] Loguear cada transición (creado/encolado/subido/confirmado) con `camera_id`
  - [ ] Calcular y emitir `upload_latency_seconds` (desde `created_at` a confirmación S3)
- [ ] **Task 3: Concurrencia multicámara** (AC: #3)
  - [ ] Verificar que varias instancias de pipeline comparten la cola y el pool respeta justicia + 3:1 (de 2.5)
- [ ] **Task 4: Graceful shutdown** (AC: #4, AC: #5)
  - [ ] Implementar `async stop()` del subsistema que drena uploads en curso con timeout 30s configurable, luego cancela
  - [ ] Garantizar persistencia final de la cola en SQLite; items no subidos quedan recuperables
  - [ ] Verificar reanudación al reiniciar (carga de cola + huérfanos de 2.2)
- [ ] **Task 5: Integración con orquestación** (AC: #6)
  - [ ] Exponer el subsistema upload (pipeline+queue+worker) con `start()`/`stop()` para que `main.py`/orquestador (1.5/3.7) lo coordine sin meter lógica en `main.py`
- [ ] **Task 6: Tests E2E** (AC: #7)
  - [ ] `tests/upload/test_pipeline_upload_e2e.py`: flujo completo con `moto` + fixture; latencia; shutdown ≤30s con persistencia; reinicio reanuda

## Dev Notes

**Esta es la story de cierre de la Épica 2: cablea lo construido en 2.1–2.5 en un flujo continuo y resiliente. No reimplementa el cliente S3 (2.1), la cola/índice (2.2), el retry (2.3), el buffer (2.4) ni la priorización (2.5): los integra.**

### Decisiones de arquitectura aplicables
- **Contrato callback HLS→cola:** `(camera_id, segment_path, created_at)` — la integración usa exactamente ese contrato. [Source: architecture-GTI_Router.md#Communication Patterns]
- **Flujo de datos:** `VideoSource → HLSPipeline → buffer(FS)+SQLite → UploadQueue → S3` — esta story conecta `HLSPipeline → UploadQueue`. [Source: architecture-GTI_Router.md#Integration Points (Flujo de datos)]
- **Concurrencia/aislamiento (D2):** tasks asyncio; pool de upload compartido con reparto justo + 3:1; caída de una cámara no afecta a otras. [Source: architecture-GTI_Router.md#Concurrency & Fault Isolation]
- **Shutdown ordenado:** workers respetan `asyncio.CancelledError` y limpian en `stop()`; el Router tiene una secuencia de shutdown (timeout configurable 30s) que 3.7 formaliza; aquí el subsistema de upload cumple su parte. [Source: architecture-GTI_Router.md#Process Patterns (Shutdown); epics.md#Story 3.7]
- **`main.py` solo orquesta:** sin lógica de negocio. [Source: architecture-GTI_Router.md#Structure Patterns]

### Patrones obligatorios (de la 1.1 / arquitectura)
- **Logging/métricas:** journald + `camera_id`; `upload_latency_seconds` en `snake_case` con sufijo de unidad. [Source: architecture-GTI_Router.md#Process Patterns / Naming Patterns]
- **Errores tipados:** **prohibido** `raise Exception(...)`. [Source: architecture-GTI_Router.md#Format Patterns]
- **No bloquear el loop:** todo el flujo es async; el drenado del shutdown usa timeouts async. [Source: architecture-GTI_Router.md#Enforcement Guidelines]
- **Estado durable en SQLite:** la persistencia de cola en shutdown es la del índice de 2.2 (no un dump aparte). [Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera de estado local)]

### Notas de diseño
- El `HLSPipeline` (1.4) ya emite el callback por segmento; esta story conecta ese callback al `enqueue`. Si 1.4 aún no está disponible en el entorno de test, usar un productor simulado que emita el contrato `(camera_id, segment_path, created_at)` con `tests/fixtures/sample.mp4`.
- El timeout de 30s del shutdown debe ser configurable vía `get_config()`; default 30s documentado.
- Esta story no debe duplicar la secuencia de init/shutdown global (12/6 pasos) que pertenece a 1.5/3.7; solo provee el `start()`/`stop()` del subsistema de upload para que la orquestación lo invoque.
- Verificar la interacción con el buffer (2.4): tras confirmar `uploaded`, el segmento queda elegible para FIFO; durante el shutdown los no subidos permanecen.

### Anti-patrones a evitar
- ❌ Meter el wiring en `main.py` en vez de en servicios · ❌ perder items no subidos en shutdown · ❌ cancelar uploads sin respetar el timeout/`CancelledError` · ❌ reimplementar cliente/cola/retry/priorización · ❌ `raise Exception` genérico · ❌ persistencia de cola fuera de SQLite. [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Testing standards
- `pytest` + `pytest-asyncio`; `moto` para S3; usar `tests/fixtures/sample.mp4` (de 1.1) o un productor simulado del callback. CI en x86 sin hardware. [Source: architecture-GTI_Router.md#Development Experience / CI; epics.md#Story 2.6]

### Project Structure Notes
Esta story integra módulos existentes (edición/wiring, sin paquetes nuevos):
```
src/pipeline/ffmpeg_hls.py   # callback por segmento (1.4) → conecta a enqueue
src/upload/queue.py          # enqueue + worker + 3:1 (2.2/2.5)
src/upload/s3_client.py      # S3Uploader (2.1)
src/storage/db.py            # persistencia de cola (2.2)
src/main.py                  # solo coordina start()/stop() del subsistema (1.5/3.7 cierran la orquestación global)
tests/upload/test_pipeline_upload_e2e.py   ← ESTA STORY
```
Variance: la secuencia global init/shutdown (12/6 pasos) y los exit codes se cierran en 1.5/3.7; aquí solo el subsistema upload. [Source: architecture-GTI_Router.md#Complete Project Directory Structure; epics.md#Story 1.5 / 3.7]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 2 / Story 2.6]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Communication Patterns (callback HLS)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Integration Points (Flujo de datos)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Concurrency & Fault Isolation (D2)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Process Patterns (Shutdown)]
- [Source: project-planning-artifacts/prd-GTI_Router-2026-01-22.md#FR3 / FR4] (flujo continuo captura→S3 resumible)

### Notas de contexto del proyecto
- Esta story cierra el MVP de la Épica 2: tras ella, el Router captura, encola, sube resilientemente, bufferea ≥4h, prioriza 3:1 y sobrevive reinicios/shutdowns. E3 (registro/health/resiliencia) construye sobre este flujo ya estable.

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
