# Story 4.4: Validación de permisos y seguridad

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **administrador del sistema GTI**,
I want **que solo comandos PTZ autorizados, frescos y dentro de límites de tasa se ejecuten**,
so that **la cámara no sea controlada por comandos viejos, ajenos a este router o abusivos**.

## Acceptance Criteria

1. **Punto de validación único:** existe un validador (p. ej. `PTZCommandValidator` o función de servicio en `src/camera/`) que el `CommandReceiver` (Story 4.2) invoca **antes** de marcar el comando `processing`; todo comando pasa por aquí. Es la única puerta de seguridad PTZ del proyecto.
2. **Frescura (anti-replay):** descarta comandos cuyo `issued_at` sea **mayor a 30s** respecto al reloj actual (UTC); también descarta los ya expirados (`expires_at` en el pasado, si está presente). El umbral de 30s es configurable.
3. **Pertenencia de la cámara:** valida que `camera_id` del comando **pertenezca a una cámara de este router** (esté en el conjunto de `camera_id` configuradas/registradas del router); si no, rechaza. Esta es la barrera contra comandos dirigidos a cámaras ajenas.
4. **Rate-limit:** aplica **rate-limit de 60 comandos/min** (ventana deslizante o token bucket), **excepto `ptz_stop`** que NUNCA se limita (seguridad: siempre se puede detener la cámara). El límite es configurable. La consulta de posición (`ptz_get_position`, Story 4.6) tampoco cuenta para el rate-limit.
4. **Rechazo limpio:** un comando rechazado **no se ejecuta** y se marca `failed` en `ptz_commands` con `error_message` describiendo la razón (`stale`, `expired`, `foreign_camera`, `rate_limited`, `unknown_command_type`); no se deja en `processing` ni se silencia.
5. **Registro y métrica de rechazos:** cada rechazo se loguea (con `camera_id` y razón) y se emite la métrica `ptz_commands_rejected` (deseable: con etiqueta/contador por razón).
6. **Errores tipados:** las violaciones se expresan con excepciones tipadas de dominio (p. ej. `PTZValidationError` subclase de `RouterError`) o un resultado de validación explícito; prohibido `Exception` genérico.
7. **Tests:** `tests/camera/test_ptz_validation.py` cubre: comando con `issued_at` > 30s → rechazado `stale`; `expires_at` pasado → `expired`; `camera_id` ajeno al router → `foreign_camera`; superar 60/min → `rate_limited`; `ptz_stop` NUNCA limitado aunque se supere el ritmo; `ptz_get_position` no cuenta para el rate-limit; cada rechazo emite `ptz_commands_rejected` y deja la fila `failed` con la razón.

## Tasks / Subtasks

- [ ] **Task 1: Validador de comandos** (AC: #1, #6)
  - [ ] Crear el validador en `src/camera/` que recibe el comando y el contexto del router (set de `camera_id`, config de umbrales)
  - [ ] Definir resultado de validación / excepción tipada `PTZValidationError(RouterError)` con razón
- [ ] **Task 2: Frescura y expiración** (AC: #2)
  - [ ] Descartar `issued_at` > 30s (configurable) respecto a UTC; descartar `expires_at` pasado
- [ ] **Task 3: Pertenencia de cámara** (AC: #3)
  - [ ] Validar `camera_id ∈ camera_id del router`; si no, rechazo `foreign_camera`
- [ ] **Task 4: Rate-limit 60/min** (AC: #4)
  - [ ] Implementar ventana deslizante / token bucket por router (o por cámara, documentar); **excluir `ptz_stop`** y `ptz_get_position`
- [ ] **Task 5: Rechazo, registro y métrica** (AC: #4 (rechazo limpio), #5)
  - [ ] Comando rechazado → `failed` + `error_message` con la razón; no ejecutar ni dejar en `processing`
  - [ ] Log con `camera_id` + razón; emitir `ptz_commands_rejected`
- [ ] **Task 6: Integración con el receiver** (AC: #1)
  - [ ] Llamar al validador en `CommandReceiver` **antes** del claim `pending → processing`
- [ ] **Task 7: Tests** (AC: #7)
  - [ ] `tests/camera/test_ptz_validation.py` con los casos del AC

## Dev Notes

### Reglas de seguridad PTZ (verificadas — esta story las materializa)
- Descartar comandos con timestamp de emisión **> 30s**, **rate-limit 60/min** (excepto `ptz_stop`), y validar pertenencia. [Source: architecture-GTI_Router.md#Authentication & Security ("descartar created_at >30s, rate-limit 60/min (excepto ptz_stop), validar … binding")]
- **Ajuste por brownfield:** la arquitectura dice "validar `router_id` y binding al gateway", pero PTZ va por `ptz_commands` **por `camera_id`** — por tanto la validación de pertenencia es **`camera_id` ∈ cámaras del router** (no `router_id`). [Source: gtisatelites-brownfield-database.md#8 / #13 (la BD verificada reemplaza suposiciones de la arquitectura) · DB8]
- El campo de tiempo real es **`issued_at`** (no `created_at`); existe además `expires_at`. [Source: gtisatelites-brownfield-database.md#10 (columnas de ptz_commands)]

### Esquema real de `ptz_commands` relevante
Columnas usadas aquí: `camera_id` (pertenencia), `command_type` (excepción de `ptz_stop`/`ptz_get_position`), `issued_at` (frescura), `expires_at` (expiración), `status`/`error_message` (rechazo → `failed` + razón). [Source: gtisatelites-brownfield-database.md#10]

### Patrones obligatorios (de 1.1 / arquitectura)
- **Errores tipados** por dominio; prohibido `Exception` genérico. [Source: architecture-GTI_Router.md#Format Patterns]
- **Logging** journald con `camera_id` en contexto; **métricas** con nombre `snake_case` (`ptz_commands_rejected`). [Source: architecture-GTI_Router.md#Process Patterns / #Naming Patterns]
- **Config** solo vía `get_config()` (umbral 30s y 60/min configurables; nunca leer env/YAML directo aquí). [Source: architecture-GTI_Router.md#Process Patterns]
- **Tiempo** UTC ISO-8601; comparar `issued_at`/`expires_at` en UTC. [Source: architecture-GTI_Router.md#Format Patterns]

### Anti-patrones a evitar
- ❌ rate-limitar `ptz_stop` (jamás — debe poder detener siempre) · ❌ ejecutar un comando rechazado · ❌ dejar el rechazado en `processing` · ❌ leer config fuera de `get_config()` · ❌ `Exception` genérico · ❌ validar contra `router_commands`/`router_id`. [Source: architecture-GTI_Router.md#Enforcement Guidelines · gtisatelites-brownfield-database.md#8]

### Integración
- Se inserta en el flujo de la Story 4.2 **antes** del claim `processing`; los rechazos cierran la fila en `failed` (consistente con el feedback de la Story 4.3). [Source: epics.md#Story 4.2 / 4.3]
- `ptz_get_position` (Story 4.6) está exento de rate-limit y se permite incluso durante un movimiento. [Source: epics.md#Story 4.6]

### Testing standards
- `pytest` + `pytest-asyncio`; reloj mockeado para los casos de frescura/expiración; sin red ni hardware. Tests en `tests/camera/`. [Source: architecture-GTI_Router.md#Infrastructure & Deployment (CI)]

### Project Structure Notes
- El validador vive en `src/camera/` (junto a `command_receiver.py`). No duplicar lógica de tiempo/rate-limit en otros módulos.

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 4 / Story 4.4]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Authentication & Security]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Enforcement Guidelines]
- [Source: project-planning-artifacts/gtisatelites-brownfield-database.md#10 (columnas de ptz_commands) / #8 (PTZ por camera_id · DB8)]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
