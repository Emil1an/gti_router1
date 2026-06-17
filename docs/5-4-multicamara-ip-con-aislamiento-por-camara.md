# Story 5.4: Multicámara IP con aislamiento por cámara

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router Pro**,
I want **capturar varias cámaras IP desde un switch sin que una afecte a otra**,
so that **la caída o reconexión de una cámara no degrade la captura ni el upload de las demás**.

## Acceptance Criteria

1. **Una frontera de fallo por cámara:** cada cámara configurada se ejecuta como **1 `VideoSource` + 1 subprocess FFmpeg (`HLSPipeline`) + 1 task asyncio supervisora dedicada**. La supervisora es la frontera de fallo dura: captura excepciones/exit codes de su cámara sin propagarlos a las demás.
2. **Aislamiento real:** la caída, timeout o reconexión de **una** cámara (FFmpeg exit, RTSP perdido, capturadora sin señal) **no** interrumpe la captura ni el upload de las otras. Las demás supervisoras siguen operando sin verse afectadas.
3. **Reconexión por cámara:** cada supervisora reintenta su propia fuente con backoff (`@with_retry`, 1→60s + jitter) de forma independiente, manteniendo intactos su buffer y la cola compartida (reutiliza la auto-recuperación de la Story 3.4 por cámara).
4. **Pool de upload compartido con reparto justo:** todas las cámaras comparten el pool de upload workers; el reparto es **justo** (round-robin) y respeta el ratio **realtime/backlog 3:1** por cámara (reutiliza la `UploadQueue` de la Épica 2). Una cámara con mucho backlog no acapara el ancho de banda de las demás.
5. **Prefijos S3 por cámara:** cada cámara sube bajo su propio prefijo `{user_id}/{router_id}/{camera_id}/` (sin colisiones entre cámaras).
6. **Orquestación multicámara:** `main.py` (orquestación, 1.5/3.7) lanza N pares (pipeline + supervisora) según la lista `cameras` de la config, gestionados como tasks concurrentes en el event loop asyncio, y los detiene ordenadamente en shutdown.
7. **Estado individual:** el estado de cada cámara se refleja por separado en el health report (bloque `per_camera`, Story 5.7); la caída de una se ve como su `connected/streaming=false` sin afectar el estado de las otras.
8. **Logging con `camera_id`:** todas las operaciones multicámara loguean con `camera_id` en el contexto (patrón de la Story 1.1) para diagnosticar qué fuente falla.
9. **Tests de aislamiento (sin hardware):** tests con mocks que simulan N cámaras y fuerzan la caída de una, verificando que las demás siguen capturando/subiendo, que la caída solo afecta su propio `per_camera`, y que el reparto de upload es justo (3:1 por cámara). Todo en x86 en CI.

## Tasks / Subtasks

- [ ] **Task 1: Modelo de aislamiento por cámara** (AC: #1, #2)
  - [ ] Estructura: por cámara, 1 `VideoSource` + 1 `HLSPipeline` (subprocess FFmpeg) + 1 task supervisora
  - [ ] La supervisora captura excepciones/exit de su cámara sin propagar (frontera de fallo dura)
- [ ] **Task 2: Reconexión independiente** (AC: #3, #7)
  - [ ] Cada supervisora usa `@with_retry` (1→60s + jitter) para su fuente, sin tocar las demás
  - [ ] Mantener buffer y cola intactos durante reconexión; loguear con `camera_id`
- [ ] **Task 3: Pool de upload con reparto justo** (AC: #4, #5)
  - [ ] Reutilizar `UploadQueue` (E2) con reparto round-robin justo entre cámaras y ratio 3:1 por cámara
  - [ ] Verificar prefijos S3 `{user_id}/{router_id}/{camera_id}/` sin colisión
- [ ] **Task 4: Orquestación multicámara** (AC: #6, #7)
  - [ ] `main.py` lanza N pares según `cameras` de la config; tasks concurrentes; shutdown ordenado
  - [ ] Estado por cámara reflejado en `per_camera` (enlaza con 5.7)
- [ ] **Task 5: Tests de aislamiento** (AC: #9)
  - [ ] `tests/`: N cámaras simuladas, forzar caída de una → las demás siguen; `per_camera` correcto; reparto 3:1 justo

## Dev Notes

**El aislamiento por cámara es la decisión D2 de la arquitectura (crítica). El patrón "1 subprocess + 1 task supervisora por cámara" ya está prescrito; esta story lo materializa para N cámaras IP. No se reinventa el upload (E2), el retry (1.1) ni la auto-recuperación (3.4): se aplican por cámara.**

### Concurrencia y aislamiento (D2) — de la arquitectura
> Un subprocess FFmpeg por cámara, supervisado por una task asyncio dedicada (**frontera de fallo dura**). Pool de upload workers compartido con reparto round-robin justo; ratio realtime/backlog 3:1 por cámara. **La caída de una cámara no afecta a las demás (Story 5.4).**
[Source: architecture-GTI_Router.md#Concurrency & Fault Isolation (D2)]

### Frontera de cámara (aislamiento)
> Cada cámara = 1 `VideoSource` + 1 `HLSPipeline` (subprocess FFmpeg) + 1 task supervisora. El fallo de una **no** propaga a otras. La cola de upload es compartida con reparto justo.
[Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera de cámara)]

### Reuso (no reinventar)
- **Upload/cola (E2):** `UploadQueue` con colas `realtime`/`backlog` y ratio 3:1 ya existe (Story 2.5). Multicámara comparte el pool con reparto justo.
- **Auto-recuperación RTSP (3.4):** backoff por cámara, mantener buffer/cola, marcar "no disponible" tras N fallos (default 30) — se aplica **por cámara**.
- **Retry (1.1):** único `@with_retry` (1→60s + jitter). Ningún agente reimplementa retry.
[Source: _bmad-output/gti-router/epics.md#Story 2.5 / 3.4 / 1.1]
[Source: architecture-GTI_Router.md#Process Patterns]

### Recursos y límites (NFR2/NFR11/NFR12)
- RAM Pro multicámara <1.5GB (NFR2): validar techo en piloto con límites systemd (`MemoryMax`/`CPUQuota`) y tope de cámaras (Story 5.6).
- Ancho de banda ≥5 Mbps × N cámaras sostenido (NFR11); streams simultáneos máx por hardware/licencia (NFR12: RPi4 2 IP +1 capturadora; RPi5 3 IP +1 capturadora).
- **Calidad sobre cantidad:** si el hardware/ancho de banda no soportan N cámaras a calidad plena, se reduce el **nº de cámaras**, nunca la calidad por stream (el tope lo aplica la Story 5.6).
[Source: architecture-GTI_Router.md#Cross-Cutting Concerns / Principio calidad sobre cantidad / NFR12]
[Source: _bmad-output/gti-router/epics.md#NFR2 / NFR11 / NFR12]

### Orquestación
- `main.py` **solo orquesta** (sin lógica de negocio): lanza N pares (pipeline + supervisora) según la lista `cameras` y los detiene en shutdown ordenado (init 12 pasos / shutdown 6 pasos, Stories 1.5/3.7).
- Workers respetan `asyncio.CancelledError` y limpian en `stop()`.
[Source: architecture-GTI_Router.md#Structure Patterns / Process Patterns / _bmad-output/gti-router/epics.md#Story 1.5 / 3.7]

### Patrones obligatorios
- Logging con `camera_id` en toda operación multicámara; métricas con sufijo de unidad (`queue_size`, `realtime_queue_size`, `backlog_queue_size`, `rtsp_reconnect_count`).
- Errores tipados; **prohibido** `Exception` genérico; nunca degradar calidad para sumar cámaras.
[Source: architecture-GTI_Router.md#Enforcement Guidelines / Communication Patterns]

### Testing standards
- `pytest` + `pytest-asyncio`; mocks de RTSP/V4L2 para simular N cámaras y caídas — sin hardware. Hardware real = checklist manual en RPi (validar NFR2/NFR12 en piloto).
[Source: architecture-GTI_Router.md#Development Experience / CI]

### Anti-patrones a evitar
- ❌ que la caída de una cámara tumbe a otras (sin frontera dura) · ❌ una cola/subprocess único compartido para todas · ❌ retry/upload ad-hoc por cámara · ❌ degradar calidad para sumar cámaras · ❌ poner lógica en `main.py`.
[Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Project Structure Notes
```
src/main.py                      # lanza N pares (pipeline + supervisora) según `cameras`
src/camera/sources/*             # VideoSource por cámara (5.1)
src/pipeline/ffmpeg_hls.py       # 1 subprocess FFmpeg por cámara (1.4)
src/upload/queue.py              # UploadQueue compartida, reparto justo 3:1 (2.5)
src/health/reporter.py           # per_camera por fuente (5.7)
```
[Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 5 / Story 5.4]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Concurrency & Fault Isolation (D2)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Architectural Boundaries (Frontera de cámara)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Principio arquitectónico: Calidad sobre cantidad]
- [Source: prd-GTI_Router-2026-01-22.md#FR19 / NFR2 / NFR11 / NFR12]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
