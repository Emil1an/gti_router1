# Story 4.1: Cliente ONVIF para PTZ

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router**,
I want **conectarme a la cámara vía ONVIF (Profile S) y exponer una API de movimiento PTZ tipada**,
so that **pueda mover la cámara según instrucciones remotas, reutilizando el mismo cliente para los comandos que lleguen por `ptz_commands`**.

## Acceptance Criteria

1. **Módulo y clase únicos:** `src/camera/ptz_control.py` expone la clase de servicio `PTZController` (una por módulo), construida sobre `onvif-zeep` (ONVIF **Profile S**). Es la única vía del proyecto para hablar ONVIF con la cámara.
2. **Conexión y descubrimiento:** `async PTZController.connect()` se conecta a la cámara (host/puerto/usuario/password desde la config de la cámara) con **timeout configurable**, obtiene el `media_profile` (token) y el servicio PTZ, y detecta capacidades exponiéndolas como flags: `supports_pan`, `supports_tilt`, `supports_zoom`, `supports_presets`.
3. **API de movimiento:** el controller expone corrutinas `async continuous_move(...)`, `async relative_move(...)`, `async absolute_move(...)`, `async stop()`, `async get_presets()`, `async go_to_preset(preset_token)` y `async get_position()`, mapeando cada una a la operación ONVIF correspondiente (`ContinuousMove`, `RelativeMove`, `AbsoluteMove`, `Stop`, `GetPresets`, `GotoPreset`, `GetStatus`). Los rangos de pan/tilt/zoom se normalizan según los `PTZConfiguration`/space límites reportados por la cámara.
4. **`get_position()` no mueve:** lee el estado vía `GetStatus` y retorna la posición actual (pan/tilt/zoom + preset activo si la cámara lo reporta) **sin afectar** la cámara. Es la base de las Stories 4.3 (feedback) y 4.6 (consulta sin movimiento).
5. **Errores tipados:** toda falla ONVIF lanza excepciones de dominio definidas en `src/utils/errors.py` (p. ej. `PTZConnectionError`, `PTZAuthError`, `PTZUnsupportedError`, `PTZCommandError`), subclases de `RouterError`. **Prohibido** `raise Exception(...)` genérico y prohibido propagar excepciones crudas de `onvif`/`zeep`/`requests`.
6. **Retry reutilizado:** todas las operaciones de red ONVIF se envuelven con el **único** `@with_retry` (`src/utils/retry.py`); no se implementa retry ad-hoc ni `time.sleep`.
7. **Logging con contexto:** todas las operaciones loguean con `camera_id` en el contexto (patrón de logging de la Story 1.1) y emiten la latencia de la operación como métrica con sufijo de unidad (`ptz_command_latency_ms`).
8. **Activación condicional:** si la cámara no tiene `ptz_enabled = true` (o el servicio PTZ no está disponible), `connect()` lanza `PTZUnsupportedError` y el controller no se inicializa; el caller (ciclo de vida, Story 4.5) lo maneja logueando INFO y continuando.
9. **Tests con mock ONVIF:** existe `tests/camera/test_ptz_control.py` que mockea `onvif-zeep` (sin hardware) y cubre: conexión OK + detección de capacidades, cada tipo de movimiento construye la request ONVIF correcta, `get_position()` no llama a ninguna operación de movimiento, y cada error ONVIF se traduce a su excepción tipada.

## Tasks / Subtasks

- [ ] **Task 1: Esqueleto del `PTZController`** (AC: #1, #2, #8)
  - [ ] Crear `src/camera/ptz_control.py` con la clase `PTZController` (recibe la config de la cámara: host, puerto ONVIF, credenciales, `camera_id`, timeout)
  - [ ] `async connect()`: instanciar `ONVIFCamera` (onvif-zeep), resolver `media_profile` + servicio PTZ, detectar capacidades (`supports_pan/tilt/zoom/presets`)
  - [ ] Si no hay servicio PTZ / `ptz_enabled` falso → `PTZUnsupportedError`
- [ ] **Task 2: API de movimiento** (AC: #3, #4)
  - [ ] Implementar `continuous_move`, `relative_move`, `absolute_move`, `stop` mapeando a las requests ONVIF y normalizando rangos por los spaces de la cámara
  - [ ] Implementar `get_presets` y `go_to_preset`
  - [ ] Implementar `get_position()` vía `GetStatus` (solo lectura — no mover)
- [ ] **Task 3: Errores tipados ONVIF** (AC: #5)
  - [ ] Agregar a `src/utils/errors.py` las subclases `PTZError(RouterError)` → `PTZConnectionError`, `PTZAuthError`, `PTZUnsupportedError`, `PTZCommandError`
  - [ ] Capturar fallos de `onvif`/`zeep`/`requests` y re-lanzarlos tipados (nunca crudos)
- [ ] **Task 4: Retry, logging y métrica** (AC: #6, #7)
  - [ ] Envolver las operaciones de red con `@with_retry`
  - [ ] Loguear con `camera_id` en contexto; medir y emitir `ptz_command_latency_ms`
- [ ] **Task 5: Tests con mock ONVIF** (AC: #9)
  - [ ] `tests/camera/test_ptz_control.py`: mock de `onvif-zeep`; cubrir conexión, capacidades, cada movimiento, `get_position()` sin movimiento y traducción de errores
  - [ ] Reusar/crear `tests/fixtures/mock_onvif.py` para simular la cámara ONVIF

## Dev Notes

**Esta es la story fundacional de la Épica 4: define el `PTZController` que las Stories 4.2–4.6 reutilizan. No reimplementar el cliente ONVIF ni el retry/logging — usa los patrones únicos de la Story 1.1.**

### Decisión clave de la Épica 4 (tabla `ptz_commands`, NO `router_commands`)
- El control PTZ se comanda vía la tabla **`ptz_commands`** filtrada por **`camera_id`** (una fila por comando). `router_commands` queda reservada para OTA/reboot/config del dispositivo y **no** se usa aquí. [Source: gtisatelites-brownfield-database.md#8 / #10 (PTZ: ptz_commands por camera_id) · DB8]
- Esta story NO toca la tabla todavía (solo el cliente ONVIF hacia la cámara); la recepción/escritura de `ptz_commands` es la Story 4.2/4.3. Aquí se construye la pieza que las ejecuta.

### Stack ONVIF (verificado, NO cambiar sin razón)
- `onvif-zeep` ya está fijada como dependencia desde la Story 1.1 (`uv add ... onvif-zeep`). No agregar otra librería ONVIF. [Source: architecture-GTI_Router.md#Initialization Command]
- **ONVIF Profile S** (streaming + PTZ) sobre **SOAP** (`onvif-zeep`) hacia la cámara. [Source: architecture-GTI_Router.md#API & Communication Patterns · #Complete Project Directory Structure (`ptz_control.py` → onvif-zeep, Profile S)]
- `onvif-zeep` es síncrono (usa `zeep`/`requests` por debajo). Envolver las llamadas bloqueantes para no bloquear el event loop (p. ej. `asyncio.to_thread`), y aun así aplicar `@with_retry`. [Source: architecture-GTI_Router.md#Process Patterns (Supabase no-bloqueante) — mismo principio para ONVIF]

### Patrones obligatorios (de la Story 1.1 / arquitectura)
- **Retry:** único `@with_retry` para toda operación de red, incluida **ONVIF**. [Source: architecture-GTI_Router.md#Enforcement Guidelines ("Usar @with_retry para … RTSP/ONVIF")]
- **Errores:** excepciones tipadas por dominio; prohibido `Exception` genérico. [Source: architecture-GTI_Router.md#Format Patterns / #Enforcement Guidelines]
- **Logging:** journald, `camera_id` en contexto por cámara. [Source: architecture-GTI_Router.md#Process Patterns]
- **Naming:** corrutinas con prefijo verbal (`async def connect()`); una clase de servicio por módulo; métricas con sufijo de unidad (`*_ms`). [Source: architecture-GTI_Router.md#Naming Patterns / #Structure Patterns]

### Anti-patrones a evitar
- ❌ retry ad-hoc con `time.sleep` · ❌ `raise Exception(...)` genérico · ❌ bloquear el event loop con la llamada SOAP síncrona · ❌ usar `router_commands` para PTZ. [Source: architecture-GTI_Router.md#Enforcement Guidelines · gtisatelites-brownfield-database.md#8]

### Contexto del `ptz_control` en la arquitectura
- Módulo `camera/ptz_control.py` = `PTZController` (onvif-zeep, Profile S); su par es `camera/command_receiver.py` (Story 4.2). [Source: architecture-GTI_Router.md#Complete Project Directory Structure · #Requirements to Structure Mapping (E4 PTZ)]
- Integración interna: comandos PTZ fluyen `command_receiver → ptz_control`. [Source: architecture-GTI_Router.md#Integration Points]

### Testing standards
- `pytest` + `pytest-asyncio`; mock de `onvif-zeep` (sin hardware). Tests en `tests/camera/` espejando `src/camera/`. Hardware real (cámara PTZ física) = checklist manual en RPi, no en CI. [Source: architecture-GTI_Router.md#Infrastructure & Deployment (CI)]

### Project Structure Notes
- Archivo a llenar: `src/camera/ptz_control.py` (creado vacío con `__init__.py` en la Story 1.1).
- Agregar excepciones a `src/utils/errors.py` (ya existe desde 1.1).
- Tests en `tests/camera/test_ptz_control.py` + fixture `tests/fixtures/mock_onvif.py`.

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 4 / Story 4.1]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#API & Communication Patterns]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Complete Project Directory Structure (`camera/ptz_control.py`)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Enforcement Guidelines]
- [Source: project-planning-artifacts/gtisatelites-brownfield-database.md#8 / #10 (PTZ por ptz_commands · DB8)]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
