# Story 5.1: Abstracción de fuente de entrada (`input_type`)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **desarrollador del equipo GTI**,
I want **una capa que abstraiga el origen del video (RTSP IP o capturadora)**,
so that **el pipeline trate ambas fuentes de forma uniforme sin conocer el origen concreto**.

## Acceptance Criteria

1. **Interfaz `VideoSource`:** `src/camera/sources/base.py` define la interfaz/ABC `VideoSource` con un contrato común: `async probe()` que retorna metadata uniforme (`resolution`, `framerate`, `codec`) y los métodos de ciclo de vida del servicio (`async start()` / `async stop()`). El contrato es el **mismo** para todas las fuentes.
2. **Implementación `RTSPSource`:** `src/camera/sources/rtsp_source.py` implementa `VideoSource` para fuentes RTSP por **passthrough** (`-c copy`, sin transcodificar), reutilizando lo definido en la Story 1.3 (`probe()`, `rtsp_transport=tcp`, excepciones tipadas). No se reimplementa la lógica RTSP: esta story la **adapta** a la interfaz común.
3. **Implementación `CaptureCardSource`:** `src/camera/sources/capture_card_source.py` implementa `VideoSource` para capturadoras V4L2, leyendo del dispositivo `/dev/videoN` (`device` configurable, p. ej. `/dev/video0`), exponiendo la misma metadata (`resolution`, `framerate`, `codec`). El **encoding** concreto vive en la Story 5.2 (`EncoderSelector`); aquí solo se abstrae la fuente y se expone su metadata vía V4L2.
4. **Selección por `input_type`:** existe una factory/dispatch (`create_source(camera_config)` o equivalente en `camera/sources/__init__.py`) que, dado `camera.input_type` (`rtsp_ip` | `capture_card`), instancia la `VideoSource` correcta. Un `input_type` desconocido lanza excepción tipada (`ConfigValidationError` o `VideoSourceError`).
5. **Pipeline agnóstico:** `pipeline/` consume **solo** la interfaz `VideoSource` (su metadata y ciclo de vida) y **nunca** conoce el origen concreto (no hay `if input_type == ...` en `pipeline/`). La frontera de fuente de video se respeta tal cual define la arquitectura.
6. **Metadata común:** ambas fuentes retornan metadata con las mismas claves y tipos; cuando un dato no aplica (p. ej. codec de entrada en capturadora analógica) se documenta el valor/convención (la capturadora reporta su formato V4L2 de captura, no un codec de stream).
7. **Errores tipados:** las fuentes lanzan excepciones tipadas bajo `RouterError` (p. ej. `VideoSourceError`, `CaptureCardError`, reutilizando `RTSPConnectionError`/`RTSPCodecError` de 1.3); **prohibido** `raise Exception(...)` genérico.
8. **Tests con mocks (sin hardware):** tests unitarios con mocks de **ambas** fuentes (`tests/fixtures/mock_rtsp.py`, `tests/fixtures/mock_v4l2.py`): la factory selecciona la clase correcta por `input_type`, `probe()` retorna metadata con el contrato esperado, y un `input_type` inválido falla con error claro. Todo corre en x86 en CI.

## Tasks / Subtasks

- [ ] **Task 1: Definir la interfaz `VideoSource`** (AC: #1, #6, #7)
  - [ ] `src/camera/sources/base.py`: ABC `VideoSource` con `async probe()` → metadata (`resolution`, `framerate`, `codec`) y `async start()`/`async stop()`
  - [ ] Documentar el contrato de metadata (claves, tipos, convención cuando un campo no aplica)
  - [ ] Definir `VideoSourceError` (y subclases si aplica) en `src/utils/errors.py` bajo `RouterError`
- [ ] **Task 2: Adaptar `RTSPSource` a la interfaz** (AC: #2, #7)
  - [ ] `src/camera/sources/rtsp_source.py`: hacer que `RTSPSource` implemente `VideoSource`, passthrough (`-c copy`), reutilizando `probe()` y excepciones de la Story 1.3
  - [ ] No duplicar la lógica RTSP: solo conformar la interfaz común
- [ ] **Task 3: Implementar `CaptureCardSource` (solo abstracción de fuente)** (AC: #3, #6, #7)
  - [ ] `src/camera/sources/capture_card_source.py`: `CaptureCardSource(VideoSource)` que lee de `/dev/videoN` (V4L2), `device` configurable
  - [ ] `probe()` expone metadata vía V4L2 (resolución/fps/formato de captura); dejar el **encoding** para la Story 5.2 (no implementarlo aquí)
  - [ ] Excepción tipada `CaptureCardError` ante dispositivo ausente/no accesible
- [ ] **Task 4: Factory por `input_type`** (AC: #4, #5)
  - [ ] `src/camera/sources/__init__.py`: `create_source(camera_config)` que despacha por `input_type` (`rtsp_ip`→`RTSPSource`, `capture_card`→`CaptureCardSource`)
  - [ ] `input_type` desconocido → excepción tipada con mensaje claro
  - [ ] Verificar que `pipeline/` consuma solo `VideoSource` (sin ramas por origen)
- [ ] **Task 5: Tests** (AC: #8)
  - [ ] `tests/camera/sources/`: factory selecciona la clase correcta por `input_type`
  - [ ] `probe()` de ambas fuentes (con mocks) retorna metadata conforme al contrato
  - [ ] `input_type` inválido → error tipado; todo en x86 sin hardware

## Dev Notes

**Esta story abre la Épica 5 (Pro) estableciendo la frontera de fuente de video. A partir de aquí, `pipeline/` es agnóstico al origen: solo habla con `VideoSource`. Las stories 5.2 (encoding), 5.4 (multicámara) y 5.5 (board) se apoyan en esta abstracción.**

### Frontera de fuente de video (OBLIGATORIA)
> Todo el pipeline consume la interfaz `VideoSource`; `pipeline/` **nunca** conoce si la fuente es RTSP o capturadora. El `EncoderSelector` es el único punto que toca decisiones de codec/board.
[Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera de fuente de video)]

### Interfaz y archivos (de la arquitectura)
- `camera/sources/base.py` → interfaz `VideoSource` (probe/metadata).
- `camera/sources/rtsp_source.py` → `RTSPSource` (passthrough `-c copy`).
- `camera/sources/capture_card_source.py` → `CaptureCardSource` (V4L2).
- `camera/encoder.py` → `EncoderSelector` (Story 5.2, **no** en esta story).
[Source: architecture-GTI_Router.md#Complete Project Directory Structure / Video Source & Encoder Strategy (D1)]

### Relación con la Story 1.3 (no reinventar RTSP)
- La Story 1.3 ya definió `RTSPSource.probe()` (conexión TCP, metadata H.264/H.265, timeout, excepciones `RTSPConnectionError`/`RTSPAuthError`/`RTSPCodecError`). Esta story **conforma** esa clase a la interfaz común `VideoSource`, no la reimplementa.
[Source: _bmad-output/gti-router/epics.md#Story 1.3]

### Config: `input_type` por cámara
- Cada cámara en `router.yaml` lleva `input_type: rtsp_ip | capture_card` (validado por `pydantic-settings` en la Story 1.2). El acceso es **solo** vía `get_config()`; ningún módulo fuera de `src/config/` lee YAML/env.
- Las fuentes `capture_card` **no** tienen `rtsp_url` (el esquema de BD lo permite nullable con CHECK — Story 0.5). El `device` (`/dev/videoN`) es el dato de la capturadora.
[Source: _bmad-output/gti-router/epics.md#Story 1.2 / Story 0.5]

### Patrones obligatorios
- `snake_case` (funciones/módulos), `PascalCase` (clases); corrutinas con prefijo verbal; servicios con `async start()`/`async stop()`.
- Una clase de servicio por módulo; utilidades transversales solo en `src/utils/`.
- Errores: excepciones tipadas por dominio; **prohibido** `Exception` genérico.
[Source: architecture-GTI_Router.md#Naming Patterns / Structure Patterns / Format Patterns]

### Calidad sobre cantidad
- `RTSPSource` es **passthrough** (preserva resolución/calidad original). No se degrada la imagen para sumar fuentes. La excepción acotada es la capturadora (Pro), que sí requiere encoding (Story 5.2) por no existir passthrough desde V4L2.
[Source: architecture-GTI_Router.md#Principio arquitectónico: Calidad sobre cantidad]

### Testing standards
- `pytest` + `pytest-asyncio`; mocks de RTSP (`mock_rtsp.py`) y V4L2 (`mock_v4l2.py`) — sin hardware. Hardware real = checklist manual en RPi.
- CI corre en x86 (`ubuntu-latest`).
[Source: architecture-GTI_Router.md#Development Experience / CI]

### Anti-patrones a evitar
- ❌ `if input_type == ...` dentro de `pipeline/` · ❌ duplicar la lógica RTSP de la Story 1.3 · ❌ `raise Exception(...)` genérico · ❌ leer YAML/`os.environ` fuera de `src/config/`.
[Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Project Structure Notes
```
src/camera/sources/
├── __init__.py            # create_source(camera_config) — factory por input_type   ← ESTA STORY
├── base.py                # interfaz VideoSource (probe/metadata, start/stop)        ← ESTA STORY
├── rtsp_source.py         # RTSPSource (1.3) → conformar a VideoSource               ← ESTA STORY (adapta)
└── capture_card_source.py # CaptureCardSource (V4L2 /dev/videoN)                     ← ESTA STORY
```
Variance: `camera/encoder.py` (`EncoderSelector`) es de la Story 5.2; `platform/board.py` de la 5.5. [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 5 / Story 5.1]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Video Source & Encoder Strategy (D1 / RT1)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Architectural Boundaries (Frontera de fuente de video / Frontera de cámara)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Complete Project Directory Structure]
- [Source: prd-GTI_Router-2026-01-22.md#FR16] (abstracción `input_type` rtsp_ip|capture_card)

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
