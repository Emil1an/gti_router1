# Story 4.6: Consulta de posición sin movimiento

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **operador en GTI Satélites**,
I want **conocer la posición actual de la cámara sin moverla**,
so that **pueda orientarme antes de enviar movimientos, incluso mientras la cámara ya se está moviendo**.

## Acceptance Criteria

1. **Comando dedicado:** el `command_type = ptz_get_position` se procesa por el mismo flujo `CommandReceiver → ejecutor` (Stories 4.2/4.3), pero se trata como **operación de solo lectura**: invoca `PTZController.get_position()` y **nunca** ejecuta ningún movimiento en la cámara.
2. **Respuesta con posición (+ preset):** actualiza la fila en `ptz_commands` con `status = completed`, `executed_at` (UTC ISO-8601 `Z`) y la posición actual (pan/tilt/zoom) escrita en `payload` (p. ej. `result_position`); si la cámara reporta un **preset activo**, lo incluye también. Si la cámara no soporta `GetStatus`/posición, marca `failed` con `error_message` claro.
3. **Sin afectar la cámara:** la consulta no cancela movimientos en curso ni encola movimientos; es segura de ejecutar **durante** un movimiento activo y devuelve la posición instantánea sin interrumpirlo.
4. **Exenta de rate-limit:** `ptz_get_position` **no** está sujeta al rate-limit de 60/min (Story 4.4) — se puede consultar libremente. Sí pasa por las validaciones de frescura (`issued_at` > 30s) y pertenencia de `camera_id` al router.
5. **Latencia y logging:** emite `ptz_command_latency_ms` y loguea con `camera_id` en contexto, igual que el resto de comandos.
6. **Tests:** `tests/camera/test_ptz_get_position.py` cubre: `ptz_get_position` retorna posición (+ preset si aplica) y deja la fila `completed` sin invocar ningún método de movimiento; ejecución concurrente con un movimiento activo no lo cancela; está exenta de rate-limit pero sí sujeta a frescura/pertenencia; cámara sin soporte de posición → `failed` con mensaje.

## Tasks / Subtasks

- [ ] **Task 1: Ruteo del comando** (AC: #1, #3)
  - [ ] En el ejecutor (Story 4.3), mapear `ptz_get_position` a `PTZController.get_position()` (solo lectura), sin pasar por la lógica de movimiento/cancelación
- [ ] **Task 2: Respuesta y preset** (AC: #2)
  - [ ] Escribir `status=completed`, `executed_at`, y `result_position` (+ preset activo si la cámara lo reporta) en `payload`
  - [ ] Cámara sin soporte → `failed` + `error_message`
- [ ] **Task 3: Exención de rate-limit** (AC: #4)
  - [ ] En el validador (Story 4.4), excluir `ptz_get_position` del rate-limit; mantener frescura + pertenencia
- [ ] **Task 4: Concurrencia segura** (AC: #3)
  - [ ] Asegurar que la consulta no cancele ni encole movimientos (no toca la lógica de cancelación de 4.2)
- [ ] **Task 5: Latencia y logging** (AC: #5)
  - [ ] Emitir `ptz_command_latency_ms` y loguear con `camera_id`
- [ ] **Task 6: Tests** (AC: #6)
  - [ ] `tests/camera/test_ptz_get_position.py` con los casos del AC

## Dev Notes

### Reuso máximo (no reimplementar)
- Esta story **reusa** `PTZController.get_position()` (definido en la Story 4.1, ya garantiza "no mueve"), el flujo de feedback de la Story 4.3 (update de `ptz_commands`) y el validador de la Story 4.4 (frescura/pertenencia). La única lógica nueva es: ruteo del `command_type`, exención de rate-limit y la garantía de concurrencia segura. [Source: epics.md#Story 4.1 / 4.3 / 4.4]

### Esquema real de `ptz_commands` (verificado)
- Se escribe `status` (`completed`/`failed`), `executed_at`, `error_message` y la posición en `payload (jsonb)` (no hay columna dedicada de posición). [Source: gtisatelites-brownfield-database.md#10 (columnas de ptz_commands)]
- Estados: `pending → processing → completed`/`failed`. [Source: architecture-GTI_Router.md#Communication Patterns]

### Reglas de la Story 4.6 (del épico)
- "Responde con posición (+ preset activo si aplica) **sin afectar** la cámara, **sin rate-limit** y **aún durante un movimiento**." [Source: epics.md#Story 4.6]

### Decisión clave de la Épica 4
- Sobre **`ptz_commands`** por **`camera_id`**, NO `router_commands`. [Source: gtisatelites-brownfield-database.md#8 · DB8]

### Patrones obligatorios (de 1.1 / arquitectura)
- **Errores tipados**; prohibido `Exception` genérico. **Tiempo** UTC ISO-8601 `Z`. **Métricas** con sufijo de unidad (`ptz_command_latency_ms`). **Logging** con `camera_id`. **Retry** `@with_retry` para el update a Supabase. [Source: architecture-GTI_Router.md#Format Patterns / #Naming Patterns / #Process Patterns / #Enforcement Guidelines]

### Anti-patrones a evitar
- ❌ que `get_position` mueva o cancele movimientos · ❌ aplicarle rate-limit · ❌ saltarse frescura/pertenencia · ❌ `Exception` genérico · ❌ usar `router_commands`. [Source: architecture-GTI_Router.md#Enforcement Guidelines · gtisatelites-brownfield-database.md#8 · epics.md#Story 4.6]

### Testing standards
- `pytest` + `pytest-asyncio`; mock de `PTZController` (con un movimiento simulado en curso) y Supabase; sin hardware. Tests en `tests/camera/`. [Source: architecture-GTI_Router.md#Infrastructure & Deployment (CI)]

### Project Structure Notes
- Sin módulos nuevos: extiende el ejecutor (Story 4.3) y el validador (Story 4.4) en `src/camera/`. Reusa `get_position()` de `src/camera/ptz_control.py`.

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 4 / Story 4.6]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Communication Patterns / #Enforcement Guidelines]
- [Source: project-planning-artifacts/gtisatelites-brownfield-database.md#10 (columnas de ptz_commands) / #8 (PTZ por camera_id · DB8)]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
