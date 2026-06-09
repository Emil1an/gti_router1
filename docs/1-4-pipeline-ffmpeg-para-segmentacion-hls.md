# Story 1.4: Pipeline FFmpeg para segmentación HLS

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router**,
I want **segmentar el stream en HLS por passthrough con un subprocess FFmpeg por cámara**,
so that **el video quede listo para upload incremental sin transcodificar, preservando la calidad original (calidad sobre cantidad)**.

## Acceptance Criteria

1. **`HLSPipeline` por cámara:** `src/pipeline/ffmpeg_hls.py` define `HLSPipeline` que arranca **1 subprocess FFmpeg por cámara** (aislamiento por cámara) y expone `async start()` / `async stop()`. Recibe una `VideoSource` (Story 1.3) — no conoce si la fuente es RTSP o capturadora.
2. **Passthrough HLS:** FFmpeg segmenta con `-c copy` (passthrough, sin transcodificar — FR12, calidad sobre cantidad) y `-hls_time {segment_duration}` con `segment_duration` configurable en rango **2–8s** (default 4s, vía `get_config()` — FR2), generando `segment_%05d.ts` + `playlist.m3u8` en el buffer de la cámara.
3. **Callback por segmento:** por cada segmento nuevo, el pipeline emite un callback con el contrato exacto **`(camera_id, segment_path, created_at)`**. Este es el contrato de integración que la Épica 2 (UploadQueue) consume. `created_at` en UTC ISO-8601.
4. **Monitoreo y reintento del subprocess:** el pipeline monitorea el `exit code` y el `stderr` de FFmpeg; ante salida inesperada reintenta el arranque vía `@with_retry` (backoff 1→60s + jitter) sin bloquear el event loop. Los errores se logean con `camera_id` en contexto.
5. **Errores tipados:** fallos del pipeline lanzan/registran excepciones tipadas (p. ej. `PipelineError`/`FFmpegError` bajo `RouterError`); prohibido `raise Exception(...)` genérico.
6. **Shutdown limpio:** `stop()` termina el subprocess FFmpeg de forma ordenada (señal de término + espera con timeout, luego kill si necesario), respeta `asyncio.CancelledError` y limpia recursos.
7. **Tests de integración:** tests con `tests/fixtures/sample.mp4` (10s H.264, generado en Story 1.1) que verifican: se generan los `.ts` + `playlist.m3u8`, el callback se invoca con el contrato `(camera_id, segment_path, created_at)` por cada segmento, y el reinicio ante salida inesperada del subprocess (FFmpeg mockeado/simulado).

## Tasks / Subtasks

- [ ] **Task 1: Implementar `HLSPipeline`** (AC: #1, #2)
  - [ ] `src/pipeline/ffmpeg_hls.py`: `HLSPipeline` con `async start()`/`async stop()`, 1 subprocess FFmpeg por cámara, consume `VideoSource`
  - [ ] Construir el comando FFmpeg con `-c copy`, `-hls_time {segment_duration}` (2–8s, default 4), salida `segment_%05d.ts` + `playlist.m3u8` en el buffer de la cámara
  - [ ] Leer `segment_duration` y rutas vía `get_config()`
- [ ] **Task 2: Callback por segmento** (AC: #3)
  - [ ] Detectar segmentos nuevos y emitir callback `(camera_id, segment_path, created_at)` (`created_at` UTC ISO-8601)
- [ ] **Task 3: Supervisión y reintento del subprocess** (AC: #4, #5, #6)
  - [ ] Monitorear `exit code` + `stderr`; reintento de arranque vía `@with_retry`
  - [ ] Errores tipados (`PipelineError`/`FFmpegError` en `src/utils/errors.py`)
  - [ ] `stop()` ordenado (term → timeout → kill), respeta `asyncio.CancelledError`, limpia recursos
- [ ] **Task 4: Tests de integración** (AC: #7)
  - [ ] `tests/pipeline/test_ffmpeg_hls.py` con `tests/fixtures/sample.mp4`: genera `.ts` + `playlist.m3u8`, callback con contrato correcto, reinicio ante salida inesperada (subprocess simulado)

## Dev Notes

**Esta story define el contrato de evento interno `(camera_id, segment_path, created_at)` que la Épica 2 consume. Respétalo al pie de la letra: el callback HLS→UploadQueue de la Story 2.6 espera exactamente esa firma.** [Source: architecture-GTI_Router.md#Communication Patterns]

### Stack y versiones
- **FFmpeg apt 5.1** (de Raspberry Pi OS Bookworm) — es del **sistema**, NO se instala vía pip ni se agrega a `pyproject`. Suficiente para passthrough HLS. (El build 7.1 estático se reserva para HEVC-SW en RPi5, Épica 5.) [Source: architecture-GTI_Router.md#Starter Template Evaluation]
- **1 subprocess FFmpeg por cámara** = frontera de fallo dura; la caída de una cámara no afecta a otras (el aislamiento multicámara completo se cierra en la Story 5.4, pero el patrón de 1-subprocess-por-cámara se establece aquí). [Source: architecture-GTI_Router.md#Concurrency & Fault Isolation (D2)]
- Gestionar el subprocess con `asyncio` (p. ej. `asyncio.create_subprocess_exec`) — nunca bloquear el event loop. [Source: architecture-GTI_Router.md#Technical Constraints]

### Principio calidad sobre cantidad (invariante de arquitectura)
- **Passthrough `-c copy`**: NO transcodificar fuentes RTSP; se preserva resolución/bitrate original. Si el hardware/ancho de banda no alcanza para N cámaras, se reduce el **número de cámaras**, nunca la calidad por stream. [Source: architecture-GTI_Router.md#Principio arquitectónico: Calidad sobre cantidad]

### Patrones reutilizados (NO redefinir)
- **Retry:** `@with_retry` de `src/utils/retry.py` para el reintento de arranque del subprocess. No reimplementar backoff. [Source: architecture-GTI_Router.md#Process Patterns]
- **Logging:** journald + `camera_id` en contexto. [Source: architecture-GTI_Router.md#Process Patterns]
- **Errores:** tipados bajo `RouterError`; prohibido `Exception` genérico. [Source: architecture-GTI_Router.md#Format Patterns]
- **Config:** `segment_duration`/rutas vía `get_config()` (Story 1.2). [Source: architecture-GTI_Router.md#Process Patterns]
- **Naming/lifecycle:** servicio expone `async start()`/`async stop()`; `snake_case`; una clase por módulo. [Source: architecture-GTI_Router.md#Naming Patterns / Structure Patterns]
- **Tiempo:** `created_at` en UTC ISO-8601 con `Z`. [Source: architecture-GTI_Router.md#Format Patterns]

### Contrato de integración (crítico)
- Callback por segmento nuevo: **`(camera_id, segment_path, created_at)`** — exactamente esta firma (la Épica 2 enchufa aquí). [Source: architecture-GTI_Router.md#Communication Patterns (Eventos internos)]
- El buffer (FS) por cámara y su política FIFO los implementa la Story 2.4 (`pipeline/buffer.py`); aquí solo se escriben los segmentos al directorio de buffer de la cámara. No implementar la limpieza FIFO en esta story.

### Anti-patrones a evitar
- ❌ transcodificar fuentes RTSP (debe ser `-c copy`) · ❌ degradar resolución/bitrate para sumar cámaras · ❌ bloquear el event loop con subprocess síncrono · ❌ retry ad-hoc con `time.sleep` · ❌ `raise Exception(...)` genérico · ❌ leer config fuera de `get_config()`. [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Testing standards
- `pytest` + `pytest-asyncio`; usar `tests/fixtures/sample.mp4` (10s H.264) como entrada. Para los casos de fallo del subprocess, simular/mockear el proceso FFmpeg. Hardware real (cámara RTSP en vivo) = checklist manual en RPi. [Source: architecture-GTI_Router.md#Testing Framework]

### Project Structure Notes
Archivos de esta story:
```
src/pipeline/ffmpeg_hls.py    # HLSPipeline (1 subprocess FFmpeg por cámara, passthrough, callback)
tests/pipeline/test_ffmpeg_hls.py
```
`pipeline/buffer.py` (FIFO de buffer) → Story 2.4; `pipeline/snapshot.py` (last-frame) → Story 6.3. NO en esta story. [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 1 / Story 1.4]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Concurrency & Fault Isolation (D2)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Video Source & Encoder Strategy (D1)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Communication Patterns]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Principio arquitectónico: Calidad sobre cantidad]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
