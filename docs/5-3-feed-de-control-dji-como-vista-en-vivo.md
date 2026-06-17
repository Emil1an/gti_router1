# Story 5.3: Feed de control DJI como vista en vivo

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **operador en GTI Satélites**,
I want **ver el feed del control DJI conectado por capturadora**,
so that **tenga vista en vivo del drone sin que el Router lo procese para detección**.

## Acceptance Criteria

1. **Feed DJI por capturadora:** una cámara configurada con `input_type: capture_card` cuya señal proviene de un control DJI (HDMI/AV → capturadora V4L2 `/dev/videoN`) se captura, codifica (vía `EncoderSelector`, Story 5.2) y segmenta como cualquier otra fuente de capturadora, reutilizando `CaptureCardSource` + `HLSPipeline`.
2. **Vista en vivo SIN detección:** el feed DJI se transmite a Satélites como **vista en vivo cruda, sin detección**. El Router **nunca** ejecuta modelos de detección sobre este feed (toda inferencia es del Gateway). El origen se marca como "sin detección" (contrato cross-sistema, alineado con E6/FR24).
3. **Marcado de origen "sin detección":** el feed/last-frame del DJI lleva el origen/contrato que indica **sin detección** (`source` del Router, no del Gateway), de modo que Satélites lo diferencie visualmente de los frames con detección del Gateway.
4. **Detección de ausencia de señal:** si la capturadora no recibe señal del control DJI (drone apagado, cable desconectado, sin sincronía), la fuente se marca como **inactiva** en el health report (bloque `per_camera`: `connected=false` / `streaming=false`, con `error` descriptivo).
5. **Aislamiento:** la pérdida o ausencia de señal del feed DJI **no** afecta la captura ni el upload de las demás cámaras del nodo (misma frontera de aislamiento de la Story 5.4: 1 subprocess FFmpeg + 1 task supervisora por fuente).
6. **Reconexión:** al recuperarse la señal, la fuente vuelve a `connected/streaming` sin intervención manual (supervisión con backoff vía el patrón único `@with_retry`).
7. **Errores tipados:** condiciones de la capturadora/feed se reportan con excepciones tipadas bajo `RouterError`; **prohibido** `Exception` genérico.
8. **Tests sin hardware:** tests con mock de V4L2 (`tests/fixtures/mock_v4l2.py`) que simulan presencia y ausencia de señal, verificando: feed activo ⇒ segmenta y se marca sin detección; sin señal ⇒ fuente inactiva en `per_camera`; recuperación ⇒ vuelve a activo. Todo en x86 en CI.

## Tasks / Subtasks

- [ ] **Task 1: Tratar el feed DJI como `capture_card`** (AC: #1)
  - [ ] Confirmar que una cámara `input_type: capture_card` con la fuente DJI usa `CaptureCardSource` (5.1) + `EncoderSelector` (5.2) + `HLSPipeline` (1.4) sin código especial de drone
  - [ ] Documentar en el schema/ejemplo cómo se declara una fuente DJI (capturadora HDMI/AV)
- [ ] **Task 2: Marcado "sin detección"** (AC: #2, #3)
  - [ ] Asegurar que el feed/last-frame del DJI se publica con el origen/contrato **sin detección** (`source` del Router)
  - [ ] No invocar ningún modelo de detección en el Router (invariante del ecosistema)
- [ ] **Task 3: Detección de señal y estado en health** (AC: #4, #6)
  - [ ] Detectar ausencia de señal V4L2 y reflejarla en `per_camera` (`connected/streaming=false`, `error`)
  - [ ] Supervisión con `@with_retry` (backoff 1→60s) para reconectar al recuperarse la señal
- [ ] **Task 4: Aislamiento** (AC: #5)
  - [ ] Verificar (con la frontera de la Story 5.4) que la caída del feed DJI no afecta otras cámaras
- [ ] **Task 5: Errores y tests** (AC: #7, #8)
  - [ ] Excepciones tipadas para condiciones de capturadora/feed
  - [ ] `tests/camera/` (mock V4L2): señal presente/ausente/recuperada; estado correcto en `per_camera`

## Dev Notes

**El feed DJI NO es un caso especial de drone en el Router: es una fuente `capture_card` más. Lo único distintivo es el contrato: vista en vivo SIN detección. La diferenciación visual y el render del drone como fuente móvil en el 3D viven en Satélites (Stories 8.4 / UX-DR9), no aquí.**

### Invariante del ecosistema (confirmado por el usuario)
> **El Router solo produce vista cruda / `last-frame` SIN detección** (autónoma, sin depender del Gateway). Toda detección (fuego/humo) es responsabilidad exclusiva del Gateway. Satélites distingue y muestra ambos orígenes por separado.
[Source: architecture-GTI_Router.md#Invariante de contrato confirmado por el usuario / Contrato cross-sistema]

### Contrato cross-sistema (sin detección)
- El Router produce `last-frame`/feed **sin** detección; el Gateway produce frames **con** detección; Satélites los diferencia. El feed DJI (FR18) entra en esta misma categoría "sin detección".
- La marca de origen (`source` del Router, sugerido `contract_version`) la formaliza la Épica 6 (Story 6.4); esta story produce el feed conforme a ese contrato.
[Source: architecture-GTI_Router.md#API & Communication Patterns / Contract cross-sistema]
[Source: _bmad-output/gti-router/epics.md#Story 6.4 / FR24]

### Es una `capture_card` (reuso, no código nuevo de drone)
- FR18: aceptar el feed de un control DJI (HDMI/AV) vía capturadora como vista en vivo (**sin** detección). Se implementa con `CaptureCardSource` (5.1) + `EncoderSelector` (5.2) + `HLSPipeline` (1.4).
- No hay passthrough desde V4L2 → requiere encoding (excepción acotada del principio calidad sobre cantidad).
[Source: _bmad-output/gti-router/epics.md#Story 5.1 / 5.2 / Epic 5 overview]
[Source: architecture-GTI_Router.md#Principio arquitectónico: Calidad sobre cantidad (Excepción acotada)]

### Estado en el health report (per_camera)
- El bloque `per_camera` reporta por cámara `{camera_id, input_type, connected, streaming, last_segment_at, error}` (Story 5.7). Sin señal DJI ⇒ `connected/streaming=false` con `error`.
[Source: architecture-GTI_Router.md#Communication Patterns (Health report) / _bmad-output/gti-router/epics.md#Story 5.7 / FR23]

### Aislamiento por cámara
- Cada fuente = 1 `VideoSource` + 1 `HLSPipeline` (subprocess FFmpeg) + 1 task supervisora. El fallo de una no propaga a otras (Story 5.4).
[Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera de cámara)]

### Lo que NO toca esta story (vive en Satélites)
- La **diferenciación visual** del feed sin detección y el render del **drone como fuente móvil** en el 3D son de la Épica 8 (Stories 8.4 / UX-DR9). El Router solo provee el feed marcado sin detección.
[Source: _bmad-output/gti-router/epics.md#Story 8.4 / UX-DR9]

### Patrones obligatorios
- `@with_retry` para supervisión/reconexión; logging con `camera_id`; métricas con sufijo de unidad; errores tipados.
[Source: architecture-GTI_Router.md#Process Patterns / Enforcement Guidelines]

### Testing standards
- Mock de V4L2 (`mock_v4l2.py`) para simular presencia/ausencia de señal — sin hardware. Hardware real (capturadora + control DJI) = checklist manual en RPi.
[Source: architecture-GTI_Router.md#Development Experience / CI]

### Anti-patrones a evitar
- ❌ ejecutar detección en el Router · ❌ publicar el feed sin la marca "sin detección" · ❌ permitir que la caída del feed DJI afecte otras cámaras · ❌ `Exception` genérico.
[Source: architecture-GTI_Router.md#Enforcement Guidelines / Invariante de contrato]

### Project Structure Notes
```
src/camera/sources/capture_card_source.py  # fuente DJI = capture_card (5.1 + encoding 5.2)
src/pipeline/ffmpeg_hls.py                  # segmenta el feed (1.4)
src/health/reporter.py                      # per_camera: estado del feed (5.7)
src/pipeline/snapshot.py                    # last-frame del feed, sin detección (6.3/6.4)
```
[Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 5 / Story 5.3]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Invariante de contrato confirmado por el usuario]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#API & Communication Patterns (Contrato cross-sistema)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Architectural Boundaries (Frontera de cámara)]
- [Source: prd-GTI_Router-2026-01-22.md#FR18 / FR24]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
