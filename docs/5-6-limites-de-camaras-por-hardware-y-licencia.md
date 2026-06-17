# Story 5.6: Límites de cámaras por hardware y licencia

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **administrador del sistema GTI**,
I want **aplicar el tope de cámaras por hardware del Router**,
so that **se garantice calidad plena por stream (principio calidad sobre cantidad), reduciendo el nº de cámaras antes que la calidad**.

## Acceptance Criteria

1. **Tope físico desde `routers.max_cameras`:** `src/licensing.py` lee el tope de hardware `routers.max_cameras` (columna de la Story 0.4) y lo aplica como límite máximo de cámaras activas en el nodo. El valor refleja el board (NFR12: RPi4 2 IP +1 capturadora; RPi5 3 IP +1 capturadora, derivado de `platform/board.py` / Story 5.5).
2. **Validación al arranque:** si la lista `cameras` de `router.yaml` excede `max_cameras`, el Router **rechaza** el exceso con un error claro y lo **registra** (log + métrica). Define el comportamiento: fail-fast al arranque (no arrancar con más cámaras de las permitidas) **o** arrancar solo las primeras N permitidas y registrar las rechazadas — documentar la decisión elegida de forma consistente.
3. **Validación al agregar cámara:** si en runtime se intenta agregar una cámara que excedería `max_cameras`, se rechaza con error claro y se registra, sin afectar las cámaras ya activas.
4. **Calidad sobre cantidad explícito:** el módulo materializa el principio: el límite existe para **garantizar calidad plena por stream**; ante falta de recursos se reduce el **número de cámaras**, nunca la resolución/bitrate por stream. Esto se documenta en el módulo.
5. **Solo tope físico (NO cuota de suscripción):** esta story aplica **únicamente** el tope de hardware (`max_cameras`). La **cuota por suscripción pagada** (`device_subscriptions.camera_quota`) y el límite efectivo `LEAST(camera_quota, max_cameras)` son de la **Épica 10** (Story 10.5) — aquí **no** se implementan ni se consultan tablas de facturación.
6. **Errores tipados:** exceder el tope lanza excepción tipada bajo `RouterError` (p. ej. `CameraLimitError`); **prohibido** `Exception` genérico. El rechazo se loguea con `camera_id` y se expone como métrica.
7. **Tests (sin hardware):** tests que verifican: dentro del tope ⇒ todas activas; exceder al arranque ⇒ rechazo + registro según la política elegida; exceder al agregar en runtime ⇒ rechazo sin afectar las activas; el tope físico se respeta independiente de cualquier cuota (que no se evalúa aquí). Todo en x86 en CI.

## Tasks / Subtasks

- [ ] **Task 1: Leer y aplicar `max_cameras`** (AC: #1, #4)
  - [ ] `src/licensing.py`: obtener `routers.max_cameras` (tope hardware) — derivable del board (5.5) y/o config/registro
  - [ ] Documentar el principio calidad sobre cantidad en el módulo
- [ ] **Task 2: Validación al arranque** (AC: #2, #6)
  - [ ] Comparar nº de `cameras` configuradas contra `max_cameras`; rechazar exceso con error claro
  - [ ] Decidir y documentar política (fail-fast vs. arrancar las primeras N + registrar rechazos)
  - [ ] `CameraLimitError` en `src/utils/errors.py`; loguear con `camera_id`; métrica de rechazo
- [ ] **Task 3: Validación al agregar cámara** (AC: #3)
  - [ ] En runtime, rechazar cámara que exceda el tope sin afectar las activas
- [ ] **Task 4: Aislar de la cuota de suscripción** (AC: #5)
  - [ ] Asegurar que NO se consulta `camera_quota` ni tablas de facturación (eso es E10/10.5)
  - [ ] Dejar el hueco documentado para el límite efectivo `LEAST(camera_quota, max_cameras)` de E10
- [ ] **Task 5: Tests** (AC: #7)
  - [ ] `tests/`: dentro/fuera del tope al arranque y en runtime; rechazos registrados; tope físico independiente de cuota

## Dev Notes

**Esta story aplica SOLO el tope físico de hardware (`max_cameras`). La cuota por suscripción pagada (`camera_quota`) y el enforcement de cobro son de la Épica 10 [EXTRA] — no bloquean el MVP del Router. Materializa el principio calidad sobre cantidad: el límite existe para que cada stream conserve calidad plena.**

### Separación de límites (regla de la épica)
> Los **límites de cámara por hardware** (`max_cameras`) quedan en `[FOUNDATION]`; los **límites por suscripción pagada** (`camera_quota`, cobro) quedan en `[EXTRA]`.
- Esta story (E5) aplica el **tope físico**. La Story 10.5 (E10) aplica el límite efectivo `LEAST(camera_quota, max_cameras)` vía la vista `device_camera_limit` y `audit_logs`.
[Source: _bmad-output/gti-router/epics.md#Convención de clasificación de épicas / Story 5.6 / Story 10.5 / DB6]

### Principio calidad sobre cantidad (de la arquitectura)
> Si el hardware o el ancho de banda no soportan N cámaras a calidad plena, se reduce el **número de cámaras** (hasta 1 si es necesario), **nunca** la calidad por stream. El límite de cámaras por hardware/licencia (NFR12) existe precisamente para garantizar calidad plena por stream.
> Anti-patrón: ❌ degradar resolución/bitrate para sumar cámaras.
[Source: architecture-GTI_Router.md#Principio arquitectónico: Calidad sobre cantidad / Enforcement Guidelines]

### `max_cameras` y NFR12
- `routers.max_cameras` (int, tope de hardware) lo agrega la Story 0.4. El valor refleja el board (Story 5.5): NFR12 → RPi4 2 IP +1 capturadora; RPi5 3 IP +1 capturadora; configurable; validar en piloto.
- Gobernanza de recursos (NFR11/NFR12) bajo calidad sobre cantidad: degradar nº de cámaras, no la calidad.
[Source: _bmad-output/gti-router/epics.md#Story 0.4 / NFR12]
[Source: architecture-GTI_Router.md#Cross-Cutting Concerns (Gobernanza de recursos) / Technical Constraints]

### Módulo `licensing.py` (de la arquitectura)
- `src/licensing.py` → límite de cámaras por hardware + licencia (lee `routers.max_cameras`). Mapeado a E5 en el árbol del proyecto.
[Source: architecture-GTI_Router.md#Complete Project Directory Structure / Requirements to Structure Mapping (E5)]

### Patrones obligatorios
- Config solo vía `get_config()`; `max_cameras` viene del registro/config, no de lectura YAML directa fuera de `src/config/`.
- Errores tipados; loguear rechazos con `camera_id`; métricas con sufijo de unidad.
[Source: architecture-GTI_Router.md#Process Patterns / Enforcement Guidelines]

### Testing standards
- `pytest`; sin hardware. Validar el tope con listas de cámaras simuladas. Hardware real (NFR12 en piloto) = checklist manual en RPi.
[Source: architecture-GTI_Router.md#Development Experience / CI]

### Anti-patrones a evitar
- ❌ aplicar aquí la cuota de suscripción (`camera_quota`) o consultar facturación (eso es E10) · ❌ degradar calidad para sumar cámaras · ❌ `Exception` genérico · ❌ leer YAML/env fuera de `src/config/`.
[Source: architecture-GTI_Router.md#Enforcement Guidelines / _bmad-output/gti-router/epics.md#Story 5.6]

### Project Structure Notes
```
src/licensing.py        # límite de cámaras por hardware (lee routers.max_cameras)   ← ESTA STORY
src/platform/board.py   # board → tope físico NFR12 (5.5)
# E10 (Story 10.5): vista device_camera_limit = LEAST(camera_quota, max_cameras) — NO aquí
```
[Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 5 / Story 5.6]
- [Source: _bmad-output/gti-router/epics.md#Convención de clasificación de épicas / Story 0.4 / Story 10.5 / DB6]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Principio arquitectónico: Calidad sobre cantidad]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Complete Project Directory Structure / Requirements to Structure Mapping]
- [Source: prd-GTI_Router-2026-01-22.md#NFR12 / FR19]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
