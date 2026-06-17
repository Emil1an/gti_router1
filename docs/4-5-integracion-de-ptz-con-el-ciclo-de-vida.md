# Story 4.5: Integración de PTZ con el ciclo de vida

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **operador del sistema**,
I want **que el subsistema PTZ se integre coherentemente con el arranque, el apagado y el health report del Router**,
so that **funcione junto a los demás componentes sin romper el ciclo de vida cuando una cámara no soporta PTZ o Supabase no está disponible**.

## Acceptance Criteria

1. **Activación condicionada:** durante el arranque (orquestación de la Story 3.7 / `main.py`), el subsistema PTZ (`PTZController` + `CommandReceiver` + ejecutor) se activa **solo si** (a) la cámara soporta PTZ (`ptz_enabled = true` y `PTZController.connect()` reporta capacidades) **Y** (b) el registro en Supabase fue exitoso (hay vínculo/identidad para recibir comandos). Si ambas condiciones no se cumplen, el PTZ no se inicia y el Router continúa normalmente.
2. **Cámara sin PTZ:** si la cámara no soporta PTZ, se loguea **INFO** (no WARNING/ERROR) y el Router continúa con captura/upload/health sin PTZ; no se considera un fallo.
3. **Sin Supabase / sin registro:** si Supabase no está disponible o el registro falló (modo degradado, Story 3.6), el PTZ queda **inactivo** (documentado) y se reintenta activarlo cuando el registro/Supabase se recuperen, sin bloquear el resto del Router.
4. **`start()`/`stop()` ordenados:** el subsistema PTZ expone `async start()` / `async stop()` y se inserta en la secuencia de init y en el shutdown ordenado de la Story 3.7; al apagar, cierra la suscripción Realtime/polling y las conexiones ONVIF limpiamente, respetando `asyncio.CancelledError`.
5. **Aislamiento de fallo:** un fallo del subsistema PTZ (p. ej. caída ONVIF persistente) **no tumba** la captura ni el upload ni el health del Router; queda contenido y reportado.
6. **PTZ en el health report:** el health report (Story 3.2, hacia `router_health`) incluye, por cámara con PTZ, las **capacidades PTZ** (`supports_pan/tilt/zoom/presets`) y la **posición actual** (vía `get_position()`), además del estado del `CommandReceiver` (Realtime conectado / polling). Se integra en el bloque `per_camera` o un sub-bloque PTZ.
7. **Multicámara:** en un router con varias cámaras, cada cámara con PTZ tiene su propio `PTZController`; el `CommandReceiver` cubre todos los `camera_id` del router (consistente con la Story 4.2). Una cámara sin PTZ no afecta a las demás.
8. **Tests:** `tests/camera/test_ptz_lifecycle.py` cubre: activación solo con PTZ-soportado + registro OK; cámara sin PTZ → INFO + continúa; sin Supabase → PTZ inactivo + Router sigue; `start/stop` ordenados; inclusión de capacidades + posición en el health report; aislamiento de fallo PTZ.

## Tasks / Subtasks

- [ ] **Task 1: Wiring en la orquestación** (AC: #1, #4)
  - [ ] Integrar `PTZController` + `CommandReceiver` + ejecutor en la secuencia de init de la Story 3.7 / `main.py` (solo orquestación, sin lógica de negocio en `main.py`)
  - [ ] Exponer `async start()/stop()` del subsistema y engancharlos al shutdown ordenado
- [ ] **Task 2: Condiciones de activación** (AC: #1, #2, #3)
  - [ ] Activar solo si cámara soporta PTZ Y registro Supabase OK
  - [ ] Cámara sin PTZ → log INFO + continuar; sin Supabase/registro → PTZ inactivo + reintento al recuperarse (modo degradado 3.6)
- [ ] **Task 3: Aislamiento de fallo** (AC: #5, #7)
  - [ ] Contener fallos PTZ para que no afecten captura/upload/health; por-cámara en multicámara
- [ ] **Task 4: PTZ en health report** (AC: #6)
  - [ ] Añadir capacidades PTZ + posición actual + estado del receiver al health report (Story 3.2 / `router_health`)
- [ ] **Task 5: Tests** (AC: #8)
  - [ ] `tests/camera/test_ptz_lifecycle.py` con los casos del AC (mock de PTZController/CommandReceiver/Supabase)

## Dev Notes

### Dependencia de ciclo de vida (Stories 3.x)
- La orquestación final, init en pasos y shutdown ordenado viven en la **Story 3.7** (`async start()/stop()` por componente, fail-fast en config/cámara, degradado en Supabase). El PTZ se inserta ahí. [Source: epics.md#Story 3.7]
- **Modo degradado sin Supabase (Story 3.6):** "sin `gateway_id` el PTZ queda inactivo (documentado)". Esta story lo materializa: sin registro/Supabase, no hay recepción de comandos. [Source: epics.md#Story 3.6]
- **Health Reporter (Story 3.2):** inserta en `router_health` métricas + bloque `per_camera`; aquí se agregan las capacidades/posición PTZ. [Source: epics.md#Story 3.2 · architecture-GTI_Router.md#Communication Patterns (per_camera)]

### Decisión clave de la Épica 4
- El `CommandReceiver` se suscribe a **`ptz_commands`** por los **`camera_id`** del router; un router con varias cámaras tiene un receiver que cubre todos sus `camera_id` y un `PTZController` por cámara con PTZ. [Source: gtisatelites-brownfield-database.md#8 · DB8 · epics.md#Story 4.2]

### Patrones obligatorios (de 1.1 / arquitectura)
- **`main.py` solo orquesta** — sin lógica de negocio. Servicios exponen `async start()/stop()`. [Source: architecture-GTI_Router.md#Structure Patterns / #Naming Patterns]
- **Shutdown:** workers respetan `asyncio.CancelledError` y limpian en `stop()`. [Source: architecture-GTI_Router.md#Process Patterns]
- **Aislamiento de fallo:** la caída de un componente no propaga (mismo principio que "caída de una cámara no afecta a las demás"). [Source: architecture-GTI_Router.md#Concurrency & Fault Isolation]
- **Supabase no-bloqueante y degradable**; **logging** con `camera_id`. [Source: architecture-GTI_Router.md#Process Patterns]

### Anti-patrones a evitar
- ❌ poner lógica PTZ en `main.py` (solo wiring) · ❌ tumbar el Router por un fallo PTZ · ❌ loguear cámara-sin-PTZ como ERROR · ❌ bloquear el arranque esperando Supabase para PTZ. [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Integración
- Consume `PTZController` (4.1), `CommandReceiver` (4.2), ejecutor/feedback (4.3) y validación (4.4). La consulta de posición (4.6) usa el mismo subsistema ya activado. [Source: epics.md#Epic 4]

### Testing standards
- `pytest` + `pytest-asyncio`; mocks de PTZController/CommandReceiver/Supabase; sin hardware. Tests en `tests/camera/`. [Source: architecture-GTI_Router.md#Infrastructure & Deployment (CI)]

### Project Structure Notes
- El wiring vive en `src/main.py` (orquestación) reusando los servicios de `src/camera/`. La extensión del health report toca `src/health/reporter.py` (per_camera). No introducir lógica PTZ fuera de `src/camera/`.

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 4 / Story 4.5]
- [Source: _bmad-output/gti-router/epics.md#Story 3.2 / 3.6 / 3.7 (health, modo degradado, orquestación)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Concurrency & Fault Isolation]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Structure Patterns / #Process Patterns]
- [Source: project-planning-artifacts/gtisatelites-brownfield-database.md#8 (PTZ por camera_id · DB8)]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
