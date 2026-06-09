# Story 3.1: Registro de dispositivo en Supabase

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **administrador del sistema GTI**,
I want **que el Router se registre/actualice (upsert) en la tabla `routers` al iniciar**,
so that **sea visible en GTI Satélites, quede vinculado a su Gateway y exponga su firmware y última conexión**.

## Acceptance Criteria

1. **Upsert por `serial_number`:** existe `src/health/registration.py` con una clase de servicio `DeviceRegistration` (`async start()` / `async stop()`) que, al iniciar el Router, hace **upsert** en la tabla `routers` usando `serial_number` como clave de conflicto (la columna ya es `UNIQUE`, creada en Épica 0). Nunca inserta un router duplicado.
2. **Campos persistidos:** el upsert escribe al menos `serial_number`, `name`, `gateway_id`, `firmware_version` y `last_seen_at` (timestamptz UTC ISO-8601 con `Z`). Los valores provienen del bloque `device` de `get_config()`; ningún valor se lee de `os.environ`/YAML directo fuera de `src/config/`.
3. **`gateway_id` cacheado:** tras un registro exitoso, el `gateway_id` vinculado queda disponible en el estado de app para que stories posteriores (PTZ E4, health 3.2) lo reutilicen sin volver a consultarlo.
4. **Modo degradado no-bloqueante:** si Supabase no responde (timeout/red/5xx), el registro **no bloquea** el arranque del Router (captura/upload siguen su curso); la operación se reintenta con `@with_retry` y, si se agota, se reprograma en segundo plano sin abortar `main()`. El flag `supabase_connected` refleja el estado.
5. **Escritura con `service_role`:** todas las escrituras a `routers` usan el cliente Supabase con `service_role` (bypassa RLS), conforme a la Épica 0 (Story 0.7). La clave se lee solo de variables de entorno.
6. **`@with_retry` reutilizado:** la llamada a Supabase usa el único `@with_retry` de `src/utils/retry.py` (backoff 1→60s + jitter ±20%); errores permanentes (p. ej. 4xx de validación/constraint) **no** se reintentan y se loguean como ERROR tipado.
7. **Errores tipados:** los fallos usan excepciones de `src/utils/errors.py` (p. ej. `RouterError`/`SupabaseError`); **prohibido** `raise Exception(...)` genérico.
8. **Tests con mock de Supabase:** hay tests en `tests/health/test_registration.py` que verifican: upsert con los campos correctos, idempotencia por `serial_number`, no-bloqueo y reprogramación ante Supabase caído, y que un error permanente no se reintenta. Sin red real ni hardware.

## Tasks / Subtasks

- [ ] **Task 1: Cliente Supabase compartido (service_role)** (AC: #5, #6)
  - [ ] Crear/usar un helper de cliente Supabase en `src/health/` que lea `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` de variables de entorno (vía `get_config()` que las expone), nunca de YAML
  - [ ] Envolver toda llamada de red con `@with_retry` (no reimplementar retry)
- [ ] **Task 2: Implementar `DeviceRegistration`** (AC: #1, #2, #3)
  - [ ] `src/health/registration.py`: clase con `async start()`/`async stop()` siguiendo el patrón de servicio (1 clase por módulo)
  - [ ] Construir el payload de upsert (`serial_number`, `name`, `gateway_id`, `firmware_version`, `last_seen_at`) en `snake_case`, tiempo UTC ISO-8601 con `Z`
  - [ ] Ejecutar upsert sobre `routers` con `on_conflict=serial_number`
  - [ ] Exponer el `gateway_id` resultante en el estado de app para reutilización (PTZ/health)
- [ ] **Task 3: Modo degradado** (AC: #4)
  - [ ] Si el upsert no se completa, no abortar `main()`; reprogramar el registro en segundo plano y marcar `supabase_connected=false`
  - [ ] Al reconectar, reintentar el upsert y actualizar `last_seen_at`
- [ ] **Task 4: Errores tipados** (AC: #7)
  - [ ] Usar/añadir excepciones en `src/utils/errors.py` (`SupabaseError` bajo `RouterError`); distinguir transitorio vs permanente para el retry
- [ ] **Task 5: Tests** (AC: #8)
  - [ ] `tests/health/test_registration.py` con mock del cliente Supabase: upsert correcto, idempotencia, no-bloqueo en caída, no-retry en error permanente

## Dev Notes

**Prerrequisito (Épica 0):** esta story DEPENDE de la Épica 0. La constraint `UNIQUE (serial_number)` en `routers` se crea en la **Story 0.6**, y las columnas operativas `firmware_version`, `last_seen_at` (y `max_cameras`) se agregan en la **Story 0.4**. Sin la Épica 0 aplicada en la base, el upsert por `serial_number` no funciona. La conversión `routers.user_id text→uuid` (Story 0.2) y las FKs (0.3) también deben estar aplicadas. [Source: epics.md#Story 0.4 / Story 0.6] [Source: gtisatelites-brownfield-database.md#8. Implicación para las épicas/stories de GTI Router]

### Contrato de datos (verificado contra el esquema real)
- El registro hace **upsert en `routers` por `serial_number`** (UNIQUE), guardando `gateway_id`, `firmware_version`, `last_seen_at`. [Source: epics.md#Story 3.1]
- El onboarding QR codifica `serial_number`; el claim del usuario setea `routers.user_id` aparte (no es responsabilidad del Router). El Router **no** escribe `user_id`. [Source: gtisatelites-brownfield-database.md#8 (Onboarding QR)]
- `firmware_version`, `last_seen_at`, `max_cameras` son columnas **nuevas** de `routers` (Épica 0, Story 0.4) — no existían en el esquema brownfield. [Source: gtisatelites-brownfield-database.md#66 / #85] [Source: epics.md#Story 0.4]

### Patrones obligatorios (de la arquitectura — heredados de 1.1)
- **Supabase no-bloqueante y degradable:** toda llamada a Supabase es no-bloqueante y tolerante a fallo; modo degradado obligatorio. Nunca bloquear el event loop esperando Supabase. [Source: architecture-GTI_Router.md#Process Patterns / Enforcement Guidelines]
- **Retry único:** usar `@with_retry` de `src/utils/retry.py` para toda operación de red; no reimplementar retry. [Source: architecture-GTI_Router.md#Process Patterns]
- **Errores tipados:** prohibido `raise Exception("...")` genérico. [Source: architecture-GTI_Router.md#Format Patterns]
- **Config:** acceso solo vía `get_config()`; secretos solo por env vars (NFR9). [Source: architecture-GTI_Router.md#Process Patterns / Authentication & Security]
- **Naming:** `snake_case` en payloads JSON a Supabase (coincide con columnas); tiempo UTC ISO-8601 con `Z`. [Source: architecture-GTI_Router.md#Format Patterns]
- **Servicio:** una clase de servicio por módulo con `async start()`/`async stop()`. [Source: architecture-GTI_Router.md#Structure Patterns]

### Frontera cloud
`health/` (Supabase) es de los únicos módulos que hablan con el exterior; todo encapsulado tras `@with_retry` y modo degradable. El resto del código nunca llama a Supabase directo. [Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera cloud)]

### Seguridad
- El servicio Router escribe registro/health con **`service_role`** (bypassa RLS) sin bloquearse. [Source: epics.md#Story 0.7] [Source: architecture-GTI_Router.md#Authentication & Security]
- Credenciales (URL + service_role key) solo en variables de entorno, nunca en YAML (NFR9). [Source: architecture-GTI_Router.md#Authentication & Security]

### Testing standards
- `pytest` + `pytest-asyncio`; mock del cliente Supabase (sin red real). Hardware real = checklist manual en RPi. [Source: architecture-GTI_Router.md#Testing Framework / CI]

### Project Structure Notes
Archivo objetivo de esta story (creado vacío en 1.1, se llena aquí):
```
src/health/
├── registration.py   ← ESTA STORY (DeviceRegistration: upsert en routers; modo degradado)
├── reporter.py       (Story 3.2)
├── monitor.py        (Story 3.3)
└── watchdog.py       (Story 3.5)
tests/health/test_registration.py   ← ESTA STORY
```
[Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 3 / Story 3.1]
- [Source: _bmad-output/gti-router/epics.md#Story 0.4 / Story 0.6 / Story 0.7]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Implementation Patterns & Consistency Rules]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Authentication & Security]
- [Source: project-planning-artifacts/gtisatelites-brownfield-database.md#8. Implicación para las épicas/stories de GTI Router]

### Notas de contexto del proyecto
- Story fundacional 1.1 ya define `@with_retry`, logging y errores tipados — **reutilizarlos**, no reinventarlos. [Source: stories/1-1-scaffold-del-proyecto-logging-y-retry.md]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
