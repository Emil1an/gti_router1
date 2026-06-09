# Story 4.3: Ejecución y feedback de comandos

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **operador en GTI Satélites**,
I want **que el comando PTZ se ejecute en la cámara y se reporte el resultado con la posición resultante en `ptz_commands`**,
so that **tenga feedback inmediato y confiable de mis acciones, incluso si la actualización de estado falla temporalmente**.

## Acceptance Criteria

1. **Ejecución vía `PTZController`:** dado un comando en estado `processing` (entregado por el `CommandReceiver` de la Story 4.2), el ejecutor invoca el método correspondiente del `PTZController` (Story 4.1) según `command_type` (p. ej. `ptz_continuous_move`, `ptz_relative_move`, `ptz_absolute_move`, `ptz_stop`, `ptz_goto_preset`), pasando los parámetros desde `payload (jsonb)`.
2. **Mapeo `command_type` → método:** existe un mapeo explícito y validado de cada `command_type` a su método del controller; un `command_type` desconocido marca el comando `failed` con `error_message` claro (no se ejecuta nada en la cámara).
3. **Actualización de feedback:** tras ejecutar, actualiza la fila en `ptz_commands` con: `status` (`completed` si OK / `failed` si error), `executed_at` (UTC ISO-8601 `Z`), `error_message` (null en éxito; mensaje tipado en fallo) y la **posición post-ejecución** (obtenida con `PTZController.get_position()`) escrita en `payload` (campo dedicado, p. ej. `result_position`) o donde la convención lo defina, sin pisar el payload de entrada.
4. **Retry de la actualización con encolado:** la actualización de estado a Supabase se reintenta (**máx 3 intentos**) y, si aún falla, el resultado se **encola localmente** para reenvío posterior (no se pierde el feedback); la operación de red usa `@with_retry`.
5. **Errores de ejecución tipados:** si el `PTZController` lanza una excepción tipada (de la Story 4.1), el comando se marca `failed` con el `error_message` derivado de ella; nunca se propaga un `Exception` genérico ni se deja el comando colgado en `processing`.
6. **Latencia:** mide y emite `ptz_command_latency_ms` (desde que se toma el comando hasta que se confirma el estado) y la registra en el log con `camera_id` en contexto.
7. **No-bloqueante:** la ejecución y la actualización no bloquean el event loop ni el `CommandReceiver` (la recepción de nuevos comandos —incluido `ptz_stop`— sigue fluyendo).
8. **Tests:** `tests/camera/test_ptz_execution.py` mockea `PTZController` y Supabase y cubre: ejecución OK → `completed` + posición; ejecución con error → `failed` + `error_message`; `command_type` desconocido → `failed` sin tocar la cámara; fallo de update → reintenta máx 3 y encola; emisión de `ptz_command_latency_ms`.

## Tasks / Subtasks

- [ ] **Task 1: Ejecutor de comandos** (AC: #1, #2, #7)
  - [ ] Implementar el componente que recibe el comando `processing` del `CommandReceiver` y lo despacha al `PTZController`
  - [ ] Mapa explícito `command_type → método`; `command_type` desconocido → `failed` con mensaje
  - [ ] Parsear `payload (jsonb)` a los argumentos del método
- [ ] **Task 2: Feedback a `ptz_commands`** (AC: #3, #5)
  - [ ] Tras ejecutar, leer posición con `get_position()` y actualizar `status`, `executed_at`, `error_message` y `result_position`
  - [ ] En error tipado del controller → `failed` + `error_message` derivado; nunca dejar `processing` colgado
- [ ] **Task 3: Retry + encolado del update** (AC: #4)
  - [ ] Actualización de estado con `@with_retry` (máx 3); si falla, encolar localmente para reenvío
- [ ] **Task 4: Latencia y logging** (AC: #6)
  - [ ] Medir `ptz_command_latency_ms` (toma→confirmación) y loguear con `camera_id`
- [ ] **Task 5: Tests** (AC: #8)
  - [ ] `tests/camera/test_ptz_execution.py` con mocks de `PTZController` + Supabase; cubrir éxito, fallo, desconocido, retry/encolado y métrica

## Dev Notes

### Esquema real de `ptz_commands` (verificado — columnas a actualizar)
`id`, `camera_id`, `command_type`, `payload (jsonb)`, `status`, `issued_by`, `issued_at`, `expires_at`, `executed_at`, `error_message`.
- Esta story escribe: `status` (`completed`/`failed`), `executed_at`, `error_message`, y la posición resultante dentro de `payload` (p. ej. `payload.result_position`) — **no existe una columna dedicada de posición**, por eso va en `payload (jsonb)`. [Source: gtisatelites-brownfield-database.md#10 (PTZ: ptz_commands) · #4 (cameras no expone columna de URL → análogamente la posición va en jsonb)]
- Estados: `pending → processing → completed`/`failed`. El receiver (4.2) deja el comando en `processing`; esta story lo cierra en `completed`/`failed`. [Source: architecture-GTI_Router.md#Communication Patterns]
- El Router escribe con `service_role` (bypassa RLS). [Source: epics.md#Story 0.7]

### Decisión clave de la Épica 4
- Toda la escritura de feedback es sobre **`ptz_commands`** filtrada por **`camera_id`**, NO `router_commands`. [Source: gtisatelites-brownfield-database.md#8 · DB8]

### Patrones obligatorios (de 1.1 / arquitectura)
- **Retry:** `@with_retry` para la actualización a Supabase; máx de intentos del update = **3** (regla de la story), encolar si se agota. [Source: architecture-GTI_Router.md#Process Patterns / #Enforcement Guidelines]
- **Supabase no-bloqueante y degradable**; nunca bloquear el event loop. [Source: architecture-GTI_Router.md#Process Patterns]
- **Errores tipados**: reusar las excepciones del `PTZController` (4.1); prohibido `Exception` genérico. [Source: architecture-GTI_Router.md#Format Patterns]
- **Tiempo:** `executed_at` en UTC ISO-8601 con `Z`. [Source: architecture-GTI_Router.md#Format Patterns]
- **Métricas:** sufijo de unidad (`ptz_command_latency_ms`). **Logging** con `camera_id`. [Source: architecture-GTI_Router.md#Naming Patterns / #Process Patterns]

### Anti-patrones a evitar
- ❌ dejar comandos colgados en `processing` ante error · ❌ pisar el `payload` de entrada · ❌ `Exception` genérico · ❌ bloquear el event loop esperando Supabase · ❌ usar `router_commands`. [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Integración
- Esta story consume `PTZController` (4.1) y es invocada por `CommandReceiver` (4.2). La consulta de posición sin movimiento (`ptz_get_position`) tiene su propio flujo en la Story 4.6 (reusa `get_position()` + el mismo patrón de feedback). [Source: architecture-GTI_Router.md#Integration Points · epics.md#Story 4.6]

### Testing standards
- `pytest` + `pytest-asyncio`; mocks de `PTZController` y Supabase (sin red ni hardware). Tests en `tests/camera/`. [Source: architecture-GTI_Router.md#Infrastructure & Deployment (CI)]

### Project Structure Notes
- El ejecutor puede vivir junto a `command_receiver.py`/`ptz_control.py` en `src/camera/` (respetar "una clase de servicio por módulo" — si se separa, módulo propio; si es parte del flujo del receiver, método de servicio claro). No introducir lógica PTZ fuera de `src/camera/`.

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 4 / Story 4.3]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Communication Patterns (pending→processing→completed/failed)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Integration Points]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Enforcement Guidelines]
- [Source: project-planning-artifacts/gtisatelites-brownfield-database.md#10 (columnas reales de ptz_commands · DB8)]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
