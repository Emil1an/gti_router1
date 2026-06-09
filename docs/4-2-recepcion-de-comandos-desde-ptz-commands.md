# Story 4.2: Recepción de comandos desde ptz_commands

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router**,
I want **recibir en tiempo real los comandos PTZ insertados en `ptz_commands` para mis cámaras**,
so that **el control desde Satélites tenga latencia mínima, con un fallback robusto que no pierda comandos ante caídas de WebSocket**.

## Acceptance Criteria

1. **Módulo y clase únicos:** `src/camera/command_receiver.py` expone la clase de servicio `CommandReceiver` (con `async start()` / `async stop()`), única vía del proyecto para recibir comandos PTZ.
2. **Fuente correcta:** la suscripción/polling es sobre la tabla **`ptz_commands`** (NO `router_commands`), **filtrada por los `camera_id` de las cámaras de este router**. El conjunto de `camera_id` válidos se obtiene de las cámaras configuradas/registradas del router. [DECISIÓN CLAVE ÉPICA 4]
3. **Realtime con fallback:** se suscribe vía **Supabase Realtime (WebSocket)** a los `INSERT` en `ptz_commands` para esos `camera_id`. Si el WebSocket no está disponible o cae, hace **fallback a polling cada 2s** de filas en estado `pending`, y reconecta a Realtime con backoff vía `@with_retry`. No se duplica el procesamiento de un mismo comando entre Realtime y polling.
4. **Solo `pending`:** únicamente procesa filas en estado `pending` (las `processing`/`completed`/`failed` se ignoran; al arrancar tras un reinicio, recoge los `pending` rezagados vía el barrido de polling inicial).
5. **Marca `processing` (claim) antes de ejecutar:** antes de entregar un comando para ejecución, el receiver lo marca `pending → processing` mediante un update condicional (`status = 'pending'`) que actúa como **claim atómico**; si el update no afecta filas (otro consumidor ya lo tomó), descarta el comando sin ejecutarlo.
6. **Prioridad de `ptz_stop` y cancelación:** los comandos `command_type = ptz_stop` tienen **prioridad** sobre los pendientes; un nuevo comando de movimiento **cancela los movimientos pendientes** aún no ejecutados de esa cámara (no se acumulan movimientos en cola). El stop siempre se procesa.
7. **Entrega desacoplada:** el receiver entrega cada comando válido al ejecutor (Story 4.3) — vía callback/handler asyncio — sin ejecutar él mismo el movimiento ONVIF. La validación de seguridad (Story 4.4) se aplica antes de marcar `processing`.
8. **Modo degradado / no bloqueante:** todas las llamadas a Supabase son no-bloqueantes y tolerantes a fallo; sin conectividad Supabase el receiver sigue reintentando sin tumbar el resto del Router. Sin `gateway_id`/registro, el PTZ queda inactivo (documentado en 3.6/4.5).
9. **Logging y métricas:** loguea con `camera_id` en contexto cada comando recibido/claim/descarte; emite métricas (`ptz_commands_received`, `ptz_realtime_connected`, `ptz_polling_active`).
10. **Tests con mock Supabase:** `tests/camera/test_command_receiver.py` mockea Realtime y REST y cubre: filtrado por `camera_id` del router, claim atómico (update condicional), caída de Realtime → fallback a polling, no-duplicación, prioridad de `ptz_stop` y cancelación de pendientes.

## Tasks / Subtasks

- [ ] **Task 1: Esqueleto del `CommandReceiver`** (AC: #1, #2, #8)
  - [ ] Crear `src/camera/command_receiver.py` con `CommandReceiver` (`async start()/stop()`), recibe el set de `camera_id` del router y un handler de ejecución
  - [ ] Cargar los `camera_id` válidos desde las cámaras configuradas/registradas
- [ ] **Task 2: Suscripción Realtime** (AC: #3, #4)
  - [ ] Suscribir a `INSERT` en `ptz_commands` filtrado por `camera_id ∈ router` vía Supabase Realtime (WebSocket)
  - [ ] Reconexión con backoff vía `@with_retry`
- [ ] **Task 3: Fallback polling 2s** (AC: #3, #4)
  - [ ] Loop de polling cada 2s de filas `pending` para los `camera_id` del router cuando Realtime no esté activo
  - [ ] Barrido inicial al arrancar (recoge pendientes rezagados); evitar doble-procesamiento Realtime/polling
- [ ] **Task 4: Claim atómico `pending → processing`** (AC: #5)
  - [ ] Update condicional `set status='processing' where id=:id and status='pending'`; si no afecta filas, descartar
- [ ] **Task 5: Prioridad y cancelación** (AC: #6, #7)
  - [ ] `ptz_stop` se procesa con prioridad; nuevo movimiento cancela movimientos pendientes no ejecutados de esa cámara
  - [ ] Entregar el comando al handler de ejecución (Story 4.3) tras validación (Story 4.4)
- [ ] **Task 6: Logging y métricas** (AC: #9)
  - [ ] Log con `camera_id`; métricas `ptz_commands_received`, `ptz_realtime_connected`, `ptz_polling_active`
- [ ] **Task 7: Tests** (AC: #10)
  - [ ] `tests/camera/test_command_receiver.py` con mock de Realtime + REST; cubrir filtrado, claim, fallback, no-duplicación, prioridad y cancelación

## Dev Notes

### Decisión clave de la Épica 4 (tabla `ptz_commands` por `camera_id`)
- **PTZ usa `ptz_commands`** (una fila por comando, filtrada por **`camera_id`**), **NO `router_commands`**. `router_commands` queda para OTA/reboot/config. Suscribir Realtime por los `camera_id` del router (un router puede tener varias cámaras). [Source: gtisatelites-brownfield-database.md#8 / #10 · DB8]

### Esquema real de `ptz_commands` (verificado, brownfield — usar estos nombres exactos)
Columnas: `id`, `camera_id`, `command_type`, `payload (jsonb)`, `status`, `issued_by`, `issued_at`, `expires_at`, `executed_at`, `error_message`.
- Estados del flujo: `pending → processing → completed`/`failed`. [Source: architecture-GTI_Router.md#Communication Patterns · gtisatelites-brownfield-database.md#10]
- `ptz_commands.camera_id → cameras.id` con FK `CASCADE` (Story 0.3); `issued_by → users.user_id` (SET NULL). [Source: epics.md#Story 0.3]
- El servicio Router escribe con `service_role` (bypassa RLS). [Source: epics.md#Story 0.7 · gtisatelites-brownfield-database.md#8]

> Nota: la arquitectura (texto antiguo) menciona `router_commands`/`created_at`; **prevalece el esquema brownfield**: tabla `ptz_commands`, timestamp `issued_at`. [Source: gtisatelites-brownfield-database.md#13 (la BD verificada reemplaza suposiciones de la arquitectura)]

### Patrón de comunicación (verificado)
- Supabase: **Realtime (WebSocket)** para comandos PTZ con **fallback a polling cada 2s**; reconexión con backoff. [Source: architecture-GTI_Router.md#API & Communication Patterns]
- Comunicación interna: comandos fluyen `command_receiver → ptz_control` (la ejecución es 4.3). [Source: architecture-GTI_Router.md#Integration Points]

### Patrones obligatorios (de 1.1 / arquitectura)
- **Retry:** único `@with_retry` (backoff 1→60s + jitter) para Realtime/REST. [Source: architecture-GTI_Router.md#Enforcement Guidelines]
- **Supabase no-bloqueante y degradable** (no tumbar el event loop esperando). [Source: architecture-GTI_Router.md#Process Patterns]
- **Errores tipados** (subclases de `RouterError`); prohibido `Exception` genérico. [Source: architecture-GTI_Router.md#Format Patterns]
- **Logging** journald con `camera_id` en contexto; métricas con sufijo de unidad cuando aplique. [Source: architecture-GTI_Router.md#Process Patterns / #Naming Patterns]
- **Shutdown:** `stop()` respeta `asyncio.CancelledError` y cierra la suscripción/loops limpiamente. [Source: architecture-GTI_Router.md#Process Patterns]

### Anti-patrones a evitar
- ❌ usar `router_commands` para PTZ · ❌ procesar el mismo comando dos veces (Realtime + polling) · ❌ retry ad-hoc con `time.sleep` · ❌ bloquear el event loop esperando Supabase · ❌ ejecutar el movimiento ONVIF aquí (eso es 4.3). [Source: architecture-GTI_Router.md#Enforcement Guidelines · gtisatelites-brownfield-database.md#8]

### Testing standards
- `pytest` + `pytest-asyncio`; mock de Supabase Realtime + REST (sin red). Tests en `tests/camera/`. [Source: architecture-GTI_Router.md#Infrastructure & Deployment (CI)]

### Project Structure Notes
- Archivo a llenar: `src/camera/command_receiver.py` (creado vacío en 1.1).
- Depende del cliente de la Story 4.1 (`PTZController`) como destino de los comandos, y precede a 4.3 (ejecución) y 4.4 (validación). La validación de 4.4 se aplica **antes** del claim `processing`.

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 4 / Story 4.2]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#API & Communication Patterns]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Communication Patterns (estados pending→processing→completed/failed)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Complete Project Directory Structure (`camera/command_receiver.py`)]
- [Source: project-planning-artifacts/gtisatelites-brownfield-database.md#8 / #10 (ptz_commands por camera_id · DB8)]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
