# Story 1.3: Cliente/Fuente RTSP con probe

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router**,
I want **conectarme a una cámara IP vía RTSP y verificar el stream con un probe**,
so that **confirmar conectividad y obtener metadata (codec, resolución, framerate) antes de arrancar la captura**.

## Acceptance Criteria

1. **Interfaz `VideoSource`:** `src/camera/sources/base.py` define la interfaz/clase base `VideoSource` con `async probe()` que retorna metadata común (`codec`, `resolution`, `framerate`) y la propiedad/identidad `camera_id`. Es el contrato que el pipeline consumirá sin conocer el origen concreto.
2. **`RTSPSource.probe()`:** `src/camera/sources/rtsp_source.py` implementa `RTSPSource(VideoSource)`. `probe()` conecta a la URL RTSP por **TCP** (`rtsp_transport=tcp`), obtiene la metadata (codec **H.264/H.265**, resolución, framerate) y la retorna en la estructura común, con un **timeout configurable**.
3. **Errores tipados:** `probe()` lanza excepciones tipadas según el fallo: `RTSPConnectionError` (host inalcanzable/timeout), `RTSPAuthError` (credenciales rechazadas) y `RTSPCodecError` (codec no soportado / sin video). Definidas en `src/utils/errors.py` bajo `RouterError`. Prohibido `raise Exception(...)` genérico.
4. **Passthrough-ready:** el probe identifica el codec para confirmar que es apto para passthrough (`-c copy`, H.264/H.265); un codec no soportado produce `RTSPCodecError` con el codec detectado en el mensaje.
5. **Config y logging:** la URL RTSP, credenciales y timeout se obtienen vía `get_config()` (Story 1.2); todo log incluye `camera_id` en el contexto. Las operaciones de red usan `@with_retry` donde aplique (probe puntual puede ser un intento con timeout, sin reintento infinito).
6. **Tests con mock RTSP:** tests unitarios con un **mock RTSP** (sin hardware) que cubren: probe exitoso (devuelve metadata H.264 y H.265), timeout/host inalcanzable → `RTSPConnectionError`, auth fallida → `RTSPAuthError`, codec no soportado → `RTSPCodecError`.

## Tasks / Subtasks

- [ ] **Task 1: Interfaz `VideoSource`** (AC: #1)
  - [ ] `src/camera/sources/base.py`: clase base `VideoSource` con `async probe()` y metadata común (`codec`, `resolution`, `framerate`, `camera_id`)
  - [ ] Documentar el contrato para que `pipeline/` no conozca el origen concreto
- [ ] **Task 2: Implementar `RTSPSource.probe()`** (AC: #2, #4)
  - [ ] `src/camera/sources/rtsp_source.py`: `RTSPSource(VideoSource)` con `probe()` por TCP (`rtsp_transport=tcp`), timeout configurable
  - [ ] Parsear metadata (codec H.264/H.265, resolución, framerate) hacia la estructura común
  - [ ] Confirmar aptitud passthrough; codec no soportado → `RTSPCodecError`
- [ ] **Task 3: Errores tipados RTSP** (AC: #3)
  - [ ] Añadir/confirmar `RTSPConnectionError`, `RTSPAuthError`, `RTSPCodecError` en `src/utils/errors.py` (subclases de `RouterError`/`RTSPError`)
- [ ] **Task 4: Integrar config y logging** (AC: #5)
  - [ ] Tomar URL/credenciales/timeout vía `get_config()`; loguear con `camera_id` en contexto
- [ ] **Task 5: Tests con mock RTSP** (AC: #6)
  - [ ] `tests/camera/sources/test_rtsp_source.py`: probe ok (H.264 y H.265), timeout → `RTSPConnectionError`, auth → `RTSPAuthError`, codec no soportado → `RTSPCodecError`
  - [ ] Fixture de mock RTSP en `tests/fixtures/` (ver Dev Notes)

## Dev Notes

**Esta story crea la PRIMERA implementación de `VideoSource`. La interfaz base que defines aquí es la que la Story 1.4 (pipeline) consume sin conocer el origen, y la que la Épica 5 extiende con `CaptureCardSource`. Mantén `base.py` mínimo y agnóstico al origen.** [Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera de fuente de video)]

### Stack y patrones
- **`RTSPSource` usa passthrough** (`-c copy`) — el probe solo verifica/lee metadata; la captura real es de la Story 1.4. [Source: architecture-GTI_Router.md#Video Source & Encoder Strategy (D1)]
- **TCP obligatorio:** `rtsp_transport=tcp` (evita pérdida UDP en enlaces inestables). [Source: epics.md#Story 1.3]
- El probe puede apoyarse en `ffprobe`/FFmpeg (apt 5.1, del sistema — NO vía pip) para leer metadata, o en una librería RTSP; lo que use, debe respetar el timeout configurable. [Source: architecture-GTI_Router.md#Starter Template Evaluation (FFmpeg apt 5.1)]

### Patrones reutilizados de la Story 1.1/1.2 (NO redefinir)
- **Errores tipados:** subclases de `RouterError` en `src/utils/errors.py` (`RTSPError` y sus hijas). Prohibido `Exception` genérico. [Source: architecture-GTI_Router.md#Format Patterns]
- **Retry:** `@with_retry` de `src/utils/retry.py` para operaciones de red; no reimplementar backoff. [Source: architecture-GTI_Router.md#Process Patterns]
- **Logging:** journald + `camera_id` en contexto por cámara. [Source: architecture-GTI_Router.md#Process Patterns]
- **Config:** URL/credenciales/timeout vía `get_config()` (Story 1.2); nunca leer YAML/env aquí. [Source: architecture-GTI_Router.md#Process Patterns]
- **Naming:** corrutinas con prefijo verbal (`async def probe()`), `snake_case`, una clase de servicio por módulo. [Source: architecture-GTI_Router.md#Naming Patterns]

### Anti-patrones a evitar
- ❌ usar UDP por defecto (debe ser TCP) · ❌ `raise Exception(...)` genérico · ❌ retry ad-hoc con `time.sleep` · ❌ leer config fuera de `get_config()` · ❌ poner lógica de codec/board aquí (eso es del `EncoderSelector`, Épica 5). [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Testing standards
- Mock RTSP en `tests/fixtures/` (la arquitectura prevé `mock_rtsp.py`); sin hardware. La cámara real es checklist manual en RPi. [Source: architecture-GTI_Router.md#Testing Framework]
- Cubrir explícitamente H.264 y H.265 en el caso de probe exitoso (ambos válidos para passthrough).

### Project Structure Notes
Archivos de esta story:
```
src/camera/sources/base.py         # interfaz VideoSource (probe/metadata)   ← base para 1.4 y E5
src/camera/sources/rtsp_source.py  # RTSPSource.probe() (TCP, timeout, metadata)
tests/camera/sources/test_rtsp_source.py
tests/fixtures/mock_rtsp.py
```
`capture_card_source.py` y `encoder.py` se implementan en la Épica 5 — NO en esta story. [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 1 / Story 1.3]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Video Source & Encoder Strategy (D1)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Architectural Boundaries (Frontera de fuente de video)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Implementation Patterns & Consistency Rules]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
