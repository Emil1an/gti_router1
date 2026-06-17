# Story 6.2: Orientación por cámara

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **técnico de instalación**,
I want **configurar la orientación de cada cámara (azimut, tilt, FOV horizontal y altura de montaje) en `router.yaml` y que el Router la persista en `cameras`**,
so that **Satélites pueda construir el frustum de visión de cada cámara en el mapa 3D**.

## Acceptance Criteria

1. **Lectura del bloque `orientation`:** `src/location/orientation.py` toma el bloque `orientation` de cada cámara desde la config validada (`get_config()`), con los campos: azimut (mapea a `cameras.heading`), `tilt`, `fov_h` (FOV horizontal) y `mount_height_m` (altura de montaje en metros).
2. **Validación de rangos:** valida con error tipado claro si los valores son inválidos: **azimut 0–360°**, **tilt** en rango plausible (p. ej. −90° a +90°), **`fov_h`** plausible (>0 y ≤ ~180°), **`mount_height_m`** > 0. La validación ocurre fail-fast (idealmente reusando el schema `pydantic-settings` de la Story 1.2) y se reporta con `ConfigValidationError`/`OrientationError`.
3. **Persistencia en `cameras` (columnas dedicadas):** cuando el Router registra/actualiza la cámara, persiste vía Supabase (`service_role`, no-bloqueante, `@with_retry`):
   - azimut → **`cameras.heading`**
   - tilt → **`cameras.tilt`**
   - FOV horizontal → **`cameras.fov_h`**
   - altura de montaje → **`cameras.mount_height_m`**
4. **Idempotencia:** re-aplicar la misma orientación no genera cambios espurios; un cambio en `router.yaml` se refleja en `cameras` en el siguiente registro/arranque (update por `camera_id`).
5. **Modo degradado:** si Supabase no responde, la persistencia de orientación no bloquea ni crashea (se difiere/reintenta vía `@with_retry`); el Router sigue operando.
6. **Errores tipados:** los fallos usan excepciones tipadas bajo `RouterError`; **prohibido** `raise Exception(...)` genérico.
7. **Tests sin hardware:** tests cubren rangos válidos e inválidos (azimut fuera de 0–360, tilt/FOV/altura inválidos), el mapeo azimut→`heading`, y la persistencia con mock de Supabase.

## Tasks / Subtasks

- [ ] **Task 1: Modelo y validación de orientación** (AC: #1, #2, #6)
  - [ ] Definir/usar el modelo `Orientation` (azimut, tilt, fov_h, mount_height_m) — preferentemente el `pydantic` de `src/config/schema.py` (Story 1.2) para fail-fast
  - [ ] Validar rangos: azimut 0–360, tilt plausible, fov_h plausible, mount_height_m > 0
  - [ ] Excepciones tipadas (`OrientationError`/`ConfigValidationError`) en `src/utils/errors.py`
- [ ] **Task 2: Persistencia en `cameras`** (AC: #3, #4, #5)
  - [ ] `src/location/orientation.py`: update de `cameras` mapeando azimut→`heading`, `tilt`, `fov_h`, `mount_height_m` por `camera_id` (Supabase `service_role`, `@with_retry`, no-bloqueante)
  - [ ] Idempotencia y modo degradado si Supabase no responde
- [ ] **Task 3: Integración con registro de cámara** (AC: #3, #4)
  - [ ] Engancharse al registro/actualización de cámara (junto a `DeviceRegistration`, Story 3.1) para escribir la orientación al arranque
- [ ] **Task 4: Tests** (AC: #7)
  - [ ] `tests/location/test_orientation.py`: rangos válidos/inválidos, mapeo azimut→`heading`, persistencia con mock de Supabase, modo degradado

## Dev Notes

**Esta story pertenece a la Épica 6 (`[ROUTER]`). El Router APORTA la orientación; el frustum 3D se dibuja en GTI Satélites (Épica 8, Story 8.2). Aplica a cualquier cámara (Base o Pro).**

### Dependencia de la Épica 0 (BD) — ANÓTALO
- Las columnas **`cameras.heading`** (azimut) ya existían; **`cameras.tilt`, `cameras.fov_h`, `cameras.mount_height_m`** se crean en la **Story 0.5** (Épica 0) como **columnas dedicadas** (no jsonb). No se crean aquí. [Source: gtisatelites-brownfield-database.md#10 (Orientación cámara: columnas dedicadas) / epics.md#Story 0.5]
- Verificado contra `information_schema`: `cameras` ya tenía `heading` + `last_frame_at`; faltaban tilt/FOV/altura (los agrega la Épica 0). [Source: gtisatelites-brownfield-database.md#8 / línea "Orientación 3D"]
- El servicio Router escribe con `service_role`. [Source: epics.md#DB10]

### Patrones OBLIGATORIOS (de la Story 1.1 / arquitectura)
- **Config:** la orientación viene del `router.yaml` (bloque `orientation` por cámara) y solo se lee vía `get_config()`; el schema `Orientation` vive en `src/config/schema.py`. Prohibido leer YAML fuera de `src/config/`. [Source: architecture-GTI_Router.md#Process Patterns / Directory Structure (schema.py: Orientation)]
- **Retry:** escrituras a Supabase bajo el único `@with_retry`. [Source: architecture-GTI_Router.md#Process Patterns]
- **Supabase no-bloqueante y degradable.** [Source: architecture-GTI_Router.md#Enforcement Guidelines]
- **Logging:** incluir `camera_id` en el contexto (operación por cámara). [Source: architecture-GTI_Router.md#Process Patterns]
- **Errores:** excepciones tipadas; **prohibido** `raise Exception("...")`. [Source: architecture-GTI_Router.md#Format Patterns]
- **Formato:** JSON `snake_case` que coincide con columnas; tiempo UTC ISO-8601 `Z`. [Source: architecture-GTI_Router.md#Format Patterns]

### Anti-patrones a evitar
- ❌ `raise Exception("...")` genérico · ❌ retry ad-hoc con `time.sleep` · ❌ leer YAML/`os.environ` fuera de `src/config/` · ❌ bloquear el event loop esperando Supabase · ❌ persistir orientación sin validar rangos. [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Modelo de datos (verificado — columnas dedicadas, NO jsonb)
- `cameras.heading` (azimut) · `cameras.tilt` (`float8`) · `cameras.fov_h` (`float8`) · `cameras.mount_height_m` (`float8`). [Source: gtisatelites-brownfield-database.md#10 / epics.md#Story 0.5]
- Decisión explícita de la BD: **columnas dedicadas** `tilt`/`fov_h`/`mount_height_m` junto a `heading` (no una columna `orientation jsonb`). [Source: gtisatelites-brownfield-database.md#10]

### Testing standards
- `pytest` + `pytest-asyncio`; mock de Supabase para la persistencia. Sin hardware. [Source: architecture-GTI_Router.md#Testing Framework / CI]

### Project Structure Notes
- Archivo objetivo: `src/location/orientation.py` (creado vacío en la Story 1.1). Tests en `tests/location/`. El schema `Orientation` se define en `src/config/schema.py` (Story 1.2). [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 6 / Story 6.2]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Complete Project Directory Structure (location/orientation.py; schema.py: Orientation)]
- [Source: project-planning-artifacts/gtisatelites-brownfield-database.md#8 / #10 (cameras: heading/tilt/fov_h/mount_height_m)]
- [Source: prd-GTI_Router-2026-01-22.md#FR22] (orientación azimut/tilt/FOV/altura para el frustum 3D)

### Notas de contexto del proyecto
- FR22 = configurar manualmente la orientación de cada cámara y persistirla en Supabase para el frustum 3D. El frustum se dibuja en la Épica 8 (Satélites, Story 8.2) a partir de estas columnas. [Source: epics.md#FR Coverage Map (FR22: E6 + E8)]
- Depende de la **Épica 0**: las columnas `tilt`/`fov_h`/`mount_height_m` se agregan allí (Story 0.5). Esta story NO toca el esquema.

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
