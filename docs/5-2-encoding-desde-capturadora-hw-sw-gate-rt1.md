# Story 5.2: Encoding desde capturadora (HW/SW) + gate RT1

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router Pro**,
I want **codificar a H.264 el video de una capturadora según el hardware (HW en RPi4, SW en RPi5)**,
so that **el feed de la capturadora pueda segmentarse y subirse igual que un stream RTSP, sin sacrificar la calidad por stream**.

## Acceptance Criteria

1. **`EncoderSelector` por board:** `src/camera/encoder.py` define `EncoderSelector` que, dado el board detectado, elige el encoder: **RPi4 → `h264_v4l2m2m` (HW)**, **RPi5 → `libx264` (SW)** con preset acotado y tope de resolución/fps. La detección de board la provee `platform/board.py` (Story 5.5); aquí se **consume** (si 5.5 aún no existe, se inyecta el board como parámetro/abstracción para no bloquear).
2. **HEVC-SW prohibido:** `EncoderSelector` **nunca** selecciona HEVC por software (inviable a resolución significativa: 1080p50 ≈ 80% de 4 cores en RPi5). Cualquier intento de configurar HEVC-SW es rechazado con excepción tipada y mensaje claro.
3. **Encoding para `CaptureCardSource`:** la `CaptureCardSource` (Story 5.1) produce un pipeline FFmpeg que captura de V4L2 (`/dev/videoN`) y **codifica** con el encoder elegido por `EncoderSelector`, generando un stream H.264 apto para el `HLSPipeline` (mismo contrato de segmentación que RTSP). Las fuentes RTSP siguen siendo passthrough (`-c copy`) — esta story no las toca.
4. **Presupuesto de CPU (NFR1):** el encoding respeta el presupuesto **<70% CPU por stream** (NFR1). Se documentan los parámetros de encoder (preset, resolución/fps máximos) que mantienen el presupuesto por board.
5. **Calidad sobre cantidad:** si el presupuesto de CPU se excede, la regla es **reducir el número de cámaras**, nunca degradar la calidad/resolución/bitrate por stream. Este principio se documenta explícitamente en el módulo y se materializa en el límite por hardware (Story 5.6).
6. **Gate RT1 (benchmark documentado):** existe un **benchmark reproducible** (script o procedimiento documentado) que mide CPU/temperatura/latencia del encoding por capturadora en RPi4 y RPi5, registrando resultados. RT1 es un **gate previo al piloto**: valida la viabilidad del encoding por capturadora antes de desplegar. El entregable incluye dónde/cómo correrlo y cómo se registran los resultados (checklist manual en hardware, no en CI).
7. **Errores tipados:** selección/configuración inválida de encoder lanza excepción tipada bajo `RouterError` (p. ej. `EncoderError`, `UnsupportedEncoderError`); **prohibido** `Exception` genérico.
8. **Tests sin hardware:** tests unitarios (mock de board) verifican que RPi4→`h264_v4l2m2m`, RPi5→`libx264`, y que HEVC-SW es rechazado. El benchmark real (RT1) corre en hardware (checklist manual), **no** en CI; CI valida solo la lógica de selección con mocks en x86.

## Tasks / Subtasks

- [ ] **Task 1: Implementar `EncoderSelector`** (AC: #1, #2, #7)
  - [ ] `src/camera/encoder.py`: `EncoderSelector` que recibe el board (de `platform/board.py` o inyectado) y retorna config de encoder
  - [ ] RPi4 → `h264_v4l2m2m` (HW); RPi5 → `libx264` (SW, preset acotado, tope resolución/fps)
  - [ ] Rechazar HEVC-SW con excepción tipada (`UnsupportedEncoderError`)
  - [ ] Definir `EncoderError`/`UnsupportedEncoderError` en `src/utils/errors.py`
- [ ] **Task 2: Cablear encoding en `CaptureCardSource`** (AC: #3)
  - [ ] Hacer que `CaptureCardSource` (Story 5.1) construya el comando FFmpeg de captura V4L2 + encode con el encoder de `EncoderSelector`
  - [ ] El stream resultante alimenta el `HLSPipeline` con el mismo contrato que RTSP (segmentos `.ts` + `playlist.m3u8`)
  - [ ] No tocar el passthrough de `RTSPSource`
- [ ] **Task 3: Documentar presupuesto y calidad sobre cantidad** (AC: #4, #5)
  - [ ] Documentar parámetros (preset/resolución/fps) que mantienen <70% CPU por stream por board (NFR1)
  - [ ] Documentar la regla: exceder presupuesto ⇒ reducir nº de cámaras, nunca la calidad (enlaza con Story 5.6)
- [ ] **Task 4: Gate RT1 — benchmark de encoding por capturadora** (AC: #6, #8)
  - [ ] Script/procedimiento de benchmark (CPU/temp/latencia) para RPi4 y RPi5
  - [ ] Documentar cómo correrlo, cómo registrar resultados, y el criterio GO/NO-GO previo al piloto
  - [ ] Marcar claramente que RT1 es checklist manual en hardware (fuera de CI)
- [ ] **Task 5: Tests de selección (mocks)** (AC: #8)
  - [ ] `tests/camera/`: RPi4→`h264_v4l2m2m`, RPi5→`libx264`, HEVC-SW rechazado
  - [ ] Verificar que el comando FFmpeg de la capturadora incluye el encoder correcto (sin ejecutar FFmpeg real)

## Dev Notes

**El `EncoderSelector` es el ÚNICO punto del código que decide codec/board. RT1 es el gate de riesgo más importante de la arquitectura (única brecha "crítica" identificada): valida en hardware que el encoding por capturadora en RPi5 es viable antes del piloto.**

### Estrategia de encoder (D1 / RT1) — de la arquitectura
- Interfaz `VideoSource`: `RTSPSource` (passthrough `-c copy`) y `CaptureCardSource` (V4L2 + **encode**).
- `EncoderSelector` por detección de board: **RPi4 → `h264_v4l2m2m` (HW)**; **RPi5 → `libx264` SW** con preset acotado y tope de resolución/fps; **HEVC-SW evitado** (inviable a resolución significativa — verificado: 1080p50 ≈ 80% de 4 cores en RPi5).
- Principio calidad sobre cantidad: el encoding cuenta contra NFR1 (<70%/stream); si se excede, se reduce el nº de cámaras, no la calidad. **RT1 = gate de benchmark antes del piloto.**
[Source: architecture-GTI_Router.md#Video Source & Encoder Strategy (D1 / RT1)]

### Frontera de codec/board
> El `EncoderSelector` es el **único** punto que toca decisiones de codec/board. `pipeline/` consume `VideoSource` sin conocer el origen.
[Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera de fuente de video)]

### Por qué RPi5 usa SW (constraint de hardware)
- RPi4 2GB (Base): tiene encoder HW H.264 (`h264_v4l2m2m`). RPi5 (Pro): **NO** tiene encoder HW H.264 → software (`libx264`). HEVC-SW se descarta por costo de CPU.
[Source: architecture-GTI_Router.md#Technical Constraints & Dependencies / Starter Template Evaluation]

### Gate RT1 (riesgo crítico con mitigación)
> **Crítico (con mitigación):** RT1 — viabilidad de encoding por capturadora en RPi5; aislado en `EncoderSelector` + gate de benchmark antes del piloto.
> **Gate previo al piloto:** ejecutar benchmark RT1 (encoding por capturadora en RPi4 y RPi5) y registrar resultados.
- FFmpeg: apt 5.1 (Bookworm) para passthrough; build estático 7.1 reservado como **contingencia** para encoding HEVC-SW en RPi5 (a validar en RT1) — pero HEVC-SW está prohibido por defecto; H.264 SW (`libx264`) es la ruta.
[Source: architecture-GTI_Router.md#Gap Analysis Results / Implementation Handoff / Starter Template Evaluation]

### Relación con otras stories
- **5.1** define `CaptureCardSource` (abstracción de fuente V4L2, sin encoding). Esta story le **agrega el encoding**.
- **5.5** implementa `platform/board.py` (lee `/proc/device-tree/model`). Esta story **consume** el board; si 5.5 no está, inyectar el board como abstracción para no acoplar el orden.
- **5.6** materializa "reducir nº de cámaras, no calidad" como límite por hardware.
- **1.4** define el `HLSPipeline` (1 subprocess FFmpeg por cámara) que recibe el stream codificado.
[Source: _bmad-output/gti-router/epics.md#Story 5.1 / 5.5 / 5.6 / 1.4]

### Patrones obligatorios
- `@with_retry` para operaciones de red (no aplica al encode local, sí al pipeline/upload aguas abajo).
- Métricas con sufijo de unidad (`cpu_percent`, `*_celsius`, `encode_latency_ms`).
- Errores tipados; **prohibido** `Exception` genérico; nada de degradar resolución/bitrate para sumar cámaras.
[Source: architecture-GTI_Router.md#Process Patterns / Enforcement Guidelines]

### Testing standards
- CI (x86): solo la **lógica de selección** con mocks de board — no ejecuta FFmpeg/encoder real.
- RT1: checklist **manual en RPi4 y RPi5** (hardware real), fuera de CI.
[Source: architecture-GTI_Router.md#Development Experience / CI / Implementation Handoff]

### Anti-patrones a evitar
- ❌ seleccionar HEVC-SW · ❌ degradar resolución/bitrate por stream para sumar capturadoras · ❌ decidir codec fuera de `EncoderSelector` · ❌ `raise Exception(...)` genérico.
[Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Project Structure Notes
```
src/camera/
├── sources/capture_card_source.py  # (5.1) capta V4L2 → ahora también codifica con EncoderSelector
└── encoder.py                      # EncoderSelector (h264_v4l2m2m HW / libx264 SW; HEVC-SW prohibido) ← ESTA STORY
src/platform/board.py               # detección RPi4/RPi5 (Story 5.5; aquí se consume)
```
Variance: el benchmark RT1 puede vivir como script en `scripts/` o como procedimiento documentado; registrar resultados en el handoff de arquitectura. [Source: architecture-GTI_Router.md#Complete Project Directory Structure / Implementation Handoff]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 5 / Story 5.2]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Video Source & Encoder Strategy (D1 / RT1)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Gap Analysis Results / Architecture Readiness Assessment / Implementation Handoff]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Principio arquitectónico: Calidad sobre cantidad]
- [Source: prd-GTI_Router-2026-01-22.md#FR17 / NFR1 / NFR12]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
