# Story 3.4: Auto-recuperación RTSP

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router**,
I want **reconectarme automáticamente a la cámara al perder la conexión RTSP (FFmpeg exit/timeout)**,
so that **la captura continúe sin intervención manual y sin perder el buffer ni la cola de upload**.

## Acceptance Criteria

1. **Detección de pérdida:** la task supervisora de la cámara detecta la pérdida de conexión cuando el subprocess FFmpeg termina con error (exit code != 0) o cuando no produce segmentos dentro de un timeout configurable.
2. **Reconexión con backoff:** ante la pérdida, reintenta la conexión RTSP con backoff exponencial (**1→60s**) usando el patrón único `@with_retry` (jitter ±20%); la reconexión cumple NFR6 (<60s típico).
3. **Buffer y cola intactos:** durante la reconexión, el buffer local de segmentos y la cola de upload **permanecen intactos** — no se borran ni se interrumpe el upload de lo ya capturado.
4. **Tope de fallos:** tras **N fallos consecutivos (default 30, configurable)** marca la cámara como **"no disponible"** (estado reflejado en `per_camera` del health) y sigue reintentando a un ritmo acotado sin saturar.
5. **Aislamiento por cámara:** en multicámara, la caída/reconexión de una cámara **no interrumpe** la captura ni el upload de las demás (frontera de fallo dura: 1 subprocess FFmpeg + 1 task supervisora por cámara).
6. **Métricas:** emite `rtsp_reconnect_count`, `rtsp_connected` (bool por cámara) y `rtsp_last_connected` (timestamp), consumibles por el `HealthReporter` (3.2) en el bloque `per_camera`.
7. **Errores tipados:** usa excepciones de `src/utils/errors.py` (p. ej. `RTSPConnectionError`); **prohibido** `raise Exception(...)` genérico.
8. **Tests sin hardware:** `tests/health/` (o `tests/camera/`) verifica: detección de exit/timeout, reconexión por backoff (clock mockeado), preservación de buffer/cola, marcado "no disponible" tras N fallos y aislamiento entre cámaras. Con mock de FFmpeg/fuente RTSP.

## Tasks / Subtasks

- [ ] **Task 1: Detección de pérdida de conexión** (AC: #1)
  - [ ] En la task supervisora por cámara, vigilar exit code del subprocess FFmpeg y un timeout de "sin segmentos nuevos" (configurable)
  - [ ] Distinguir fin esperado (shutdown/cancel) de fallo real
- [ ] **Task 2: Reconexión con backoff** (AC: #2)
  - [ ] Reintentar el arranque del pipeline/fuente con `@with_retry` (1→60s + jitter); no reimplementar retry
  - [ ] Respetar NFR6 (<60s) en el primer reintento útil
- [ ] **Task 3: Preservar estado** (AC: #3, #5)
  - [ ] Garantizar que el buffer (FS) y la cola (SQLite) no se tocan durante la reconexión
  - [ ] Asegurar que el fallo de una cámara no propaga a las tasks supervisoras de otras
- [ ] **Task 4: Tope de fallos y estado** (AC: #4, #6)
  - [ ] Contador de fallos consecutivos; al llegar a N (default 30) marcar `connected=false`/"no disponible" en `per_camera`
  - [ ] Emitir métricas `rtsp_reconnect_count`, `rtsp_connected`, `rtsp_last_connected`
- [ ] **Task 5: Errores y tests** (AC: #7, #8)
  - [ ] Excepciones tipadas; tests con mock de FFmpeg/RTSP y clock

## Dev Notes

**Prerrequisito (Épica 0):** las métricas de esta story se publican en el bloque `per_camera` del health (`router_health`, Épica 0 Story 0.4) vía la Story 3.2. La auto-recuperación en sí no escribe en Supabase. [Source: epics.md#Story 0.4 / Story 3.2]

**Depende de la Épica 1:** reutiliza `HLSPipeline` (Story 1.4, 1 subprocess FFmpeg por cámara con monitoreo de exit code/stderr) y la `RTSPSource` (Story 1.3). Esta story añade la **política de reconexión** sobre esos componentes, no los reimplementa. [Source: epics.md#Story 1.3 / Story 1.4]

### Contrato / responsabilidad
- Ante pérdida (FFmpeg exit/timeout) reintenta con backoff (1→60s) manteniendo buffer y cola intactos; tras N fallos (default 30) marca "cámara no disponible". [Source: epics.md#Story 3.4]
- Métricas: `rtsp_reconnect_count`, `rtsp_connected`, `rtsp_last_connected`. [Source: epics.md#Story 3.4]
- NFR6: reconexión automática tras fallo **<60s**. NFR5: uptime del stream ≥99% con conectividad disponible. [Source: epics.md#NonFunctional Requirements]

### Aislamiento por cámara (de la arquitectura)
- Cada cámara = 1 `VideoSource` + 1 `HLSPipeline` (subprocess FFmpeg) + 1 task supervisora; el fallo de una **no** propaga a otras (frontera de fallo dura). [Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera de cámara)] [Source: architecture-GTI_Router.md#Concurrency & Fault Isolation (D2)]
- La cola de upload es compartida con reparto justo; la reconexión de una cámara no afecta el upload de las demás. [Source: architecture-GTI_Router.md#Concurrency & Fault Isolation]

### Patrones obligatorios (heredados de 1.1)
- **Retry único `@with_retry`** (backoff 1→60s + jitter ±20%) para toda reconexión de red; prohibido retry ad-hoc con `time.sleep`. [Source: architecture-GTI_Router.md#Process Patterns / Enforcement Guidelines]
- **Errores tipados** (`RTSP*` ya definidos en 1.3/utils); prohibido `Exception` genérico. [Source: architecture-GTI_Router.md#Format Patterns]
- **Logging** con `camera_id` en contexto multicámara; **métricas** `snake_case` + sufijo de unidad. [Source: architecture-GTI_Router.md#Process / Naming Patterns]
- **Shutdown:** las tasks respetan `asyncio.CancelledError` y limpian en `stop()` (no confundir cancelación con fallo). [Source: architecture-GTI_Router.md#Process Patterns]

### Relación con 3.7 (orquestación)
La task supervisora vive bajo el orquestador del ciclo de vida (3.7); la auto-recuperación es parte del comportamiento "degradado en cámara" que el init/run mantiene activo. [Source: epics.md#Story 3.7]

### Testing standards
- `pytest` + `pytest-asyncio`; mock de FFmpeg/RTSP (sin hardware) y clock para el backoff; verificar que buffer/cola no se borran. [Source: architecture-GTI_Router.md#Testing Framework / CI]

### Project Structure Notes
La lógica de auto-recuperación vive junto a la supervisión de la cámara/pipeline. La task supervisora se introduce con `pipeline/ffmpeg_hls.py` (1.4); el contador/política de reconexión y las métricas RTSP se exponen al health (`per_camera`). Los tests espejan `src/` (en `tests/camera/` y/o `tests/health/`).
```
src/pipeline/ffmpeg_hls.py   (1.4 — supervisión base; aquí se añade política de reconexión)
src/camera/sources/rtsp_source.py  (1.3 — fuente RTSP)
```
[Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 3 / Story 3.4]
- [Source: _bmad-output/gti-router/epics.md#Story 1.3 / Story 1.4]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Concurrency & Fault Isolation (D2)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Architectural Boundaries]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Implementation Patterns & Consistency Rules]

### Notas de contexto del proyecto
- Reutilizar `@with_retry`, logging con `camera_id`, y excepciones `RTSP*` de 1.1/1.3; no reinventar reconexión. [Source: stories/1-1-scaffold-del-proyecto-logging-y-retry.md]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
