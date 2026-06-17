# Story 6.1: Captura y persistencia de GPS

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **administrador del sistema GTI**,
I want **que el Router capture su GPS (vía gpsd/pynmea2, solo Pro) y lo persista en `routers.location`**,
so that **el dispositivo se posicione automáticamente en el mapa 3D de Satélites, conservando la última coordenada conocida cuando no haya fix**.

## Acceptance Criteria

1. **Lectura GPS (solo Pro):** `src/location/gps.py` expone un servicio (`GpsReader` o equivalente) con `async start()`/`async stop()` que se conecta a **gpsd** y parsea sentencias NMEA con **pynmea2**, obteniendo `{lat, lon}` (y opcionalmente `altitude`, `fix_quality`, `satellites`, `hdop`). En hardware **Base** (sin GPS) el servicio NO arranca o queda inerte sin lanzar errores.
2. **Persistencia en `routers.location` (jsonb):** cuando hay fix, persiste las coordenadas en la columna **`routers.location` (jsonb)** vía Supabase (`service_role`), sin bloquear el event loop y tras `@with_retry`. El payload es JSON `snake_case` (p. ej. `{lat, lon, altitude, fix_quality, updated_at}`) con tiempo UTC ISO-8601 `Z`.
3. **Inclusión en el health report:** la coordenada vigente se expone al `HealthReporter` (campo `gps` del `router_health`) para que viaje en el reporte de salud (cada 60s, Story 3.2). El Router expone la última coord conocida como estado consultable de la app.
4. **Última coordenada conocida:** si no hay fix (sin señal, gpsd caído, sentencia inválida) **conserva en memoria la última coordenada válida** y NO la sobrescribe con `null`. Solo actualiza `routers.location` ante una coordenada nueva válida; las lecturas inválidas se descartan con log WARNING.
5. **Dato sensible (RLS / NFR14):** el código documenta y respeta que la coordenada GPS es **dato sensible**: se escribe con `service_role` (que bypassa RLS) y nunca se loguea a nivel INFO la coordenada exacta en claro; la protección de lectura la aplica la RLS de la Épica 0 (`routers.location` solo visible a usuarios autorizados).
6. **Modo degradado:** si Supabase no está disponible, la persistencia NO bloquea ni crashea (se difiere/reintenta vía `@with_retry`); la captura GPS local sigue funcionando y la última coord se mantiene para el siguiente health report.
7. **Errores tipados:** los fallos de GPS usan excepciones tipadas (`GpsError` y subclases bajo `RouterError`); **prohibido** `raise Exception(...)` genérico.
8. **Tests sin hardware:** tests con mock de gpsd/NMEA (fixture `tests/fixtures/mock_gps.py`) cubren: fix válido → persiste; sin fix → conserva última conocida; sentencia inválida → descartada; board Base → servicio inerte.

## Tasks / Subtasks

- [ ] **Task 1: Servicio de lectura GPS** (AC: #1, #7)
  - [ ] Implementar `src/location/gps.py` con `async start()`/`async stop()`, conexión a **gpsd** y parseo NMEA con **pynmea2**
  - [ ] Solo activo en hardware **Pro** (consultar `platform/board.py`); en Base queda inerte sin error
  - [ ] Definir excepciones tipadas `GpsError` (y subclases) en `src/utils/errors.py`
- [ ] **Task 2: Estado "última coordenada conocida"** (AC: #4)
  - [ ] Mantener en memoria la última `{lat, lon, ...}` válida; descartar lecturas inválidas con log WARNING
  - [ ] Exponer un getter consultable para el health report (no sobrescribir con `null`)
- [ ] **Task 3: Persistencia en `routers.location` (jsonb)** (AC: #2, #5, #6)
  - [ ] Update de `routers.location` (jsonb) vía Supabase con `service_role`, no-bloqueante y bajo `@with_retry`
  - [ ] Payload `snake_case` con `updated_at` UTC ISO-8601 `Z`; modo degradado si Supabase no responde
  - [ ] No loguear la coordenada exacta en INFO (dato sensible)
- [ ] **Task 4: Integración con el health report** (AC: #3)
  - [ ] Exponer la coord vigente al `HealthReporter` para el campo `gps` de `router_health` (Story 3.2)
- [ ] **Task 5: Tests** (AC: #8)
  - [ ] `tests/location/test_gps.py` con `tests/fixtures/mock_gps.py`: fix válido, sin fix (última conocida), sentencia inválida, board Base inerte, modo degradado de Supabase

## Dev Notes

**Esta story pertenece a la Épica 6 (`[ROUTER]`) — GPS, Orientación y Last-Frame. El Router APORTA los datos del 3D; el render vive en GTI Satélites (Épica 8). El GPS es SOLO Pro.**

### Dependencia de la Épica 0 (BD) — ANÓTALO
- La columna **`routers.location` (jsonb) ya existe** en el esquema (verificado contra `information_schema`). No se crea aquí. [Source: gtisatelites-brownfield-database.md#10 (Decisiones finales) / §8]
- La **RLS de GPS sensible** sobre `routers.location` se define en la **Story 0.7** (Épica 0). Esta story solo escribe con `service_role` (bypassa RLS); la protección de lectura es responsabilidad de la Épica 0. [Source: epics.md#Story 0.7]
- El servicio Router escribe con `service_role`. [Source: gtisatelites-brownfield-database.md#5 / epics.md#DB10]

### Stack y dependencias (ya fijadas en Story 1.1)
- **`pynmea2`** y **`gpsd`** (gpsd es del sistema, no pip): lectura de coordenadas. `pynmea2` ya está en `pyproject.toml` desde la Story 1.1. [Source: architecture-GTI_Router.md#Initialization Command / Technical Constraints]
- GPS **solo en variante Pro (RPi5)**; detección de board en `platform/board.py` (Story 5.5). [Source: architecture-GTI_Router.md#Directory Structure (location/gps.py — solo Pro)]

### Patrones OBLIGATORIOS (de la Story 1.1 / arquitectura)
- **Retry:** toda escritura a Supabase pasa por el único `@with_retry` (backoff 1→60s + jitter ±20%). No reimplementar retry. [Source: architecture-GTI_Router.md#Process Patterns]
- **Config:** acceso solo vía `get_config()`; prohibido `os.environ`/YAML directo fuera de `src/config/`. El bloque `gps` viene del `router.yaml` (Story 1.2). [Source: architecture-GTI_Router.md#Process Patterns]
- **Supabase no-bloqueante y degradable:** nunca bloquear el event loop esperando Supabase; modo degradado obligatorio. [Source: architecture-GTI_Router.md#Enforcement Guidelines]
- **Logging:** `logging`→journald; `camera_id` solo aplica a operaciones por cámara (GPS es a nivel router). NO loguear coordenada exacta en INFO. [Source: architecture-GTI_Router.md#Process Patterns + NFR14]
- **Errores:** excepciones tipadas por dominio; **prohibido** `raise Exception("...")` genérico. [Source: architecture-GTI_Router.md#Format Patterns]
- **Formato de payloads:** JSON `snake_case`, tiempo UTC ISO-8601 con `Z`. [Source: architecture-GTI_Router.md#Format Patterns]

### Anti-patrones a evitar
- ❌ `raise Exception("...")` genérico · ❌ retry ad-hoc con `time.sleep` · ❌ bloquear el event loop esperando Supabase · ❌ sobrescribir la última coord conocida con `null` · ❌ loguear la coordenada exacta en claro a nivel INFO. [Source: architecture-GTI_Router.md#Enforcement Guidelines + NFR14]

### Modelo de datos (verificado)
- **`routers.location`**: `jsonb` (ya existe). Aquí se persiste `{lat, lon, ...}`. [Source: gtisatelites-brownfield-database.md#8 / #10]
- El bloque `gps` del **`router_health`** transporta la coord en cada reporte de salud (Story 3.2). [Source: epics.md#Story 0.4 (router_health.gps jsonb) / #Story 3.2]

### Testing standards
- `pytest` + `pytest-asyncio`; mock de gpsd/NMEA vía `tests/fixtures/mock_gps.py`. Hardware GPS real = checklist manual en RPi5 (no en CI). [Source: architecture-GTI_Router.md#Testing Framework / CI]
- CI: GitHub Actions corre `pytest` en x86 con mocks (incluye GPS). [Source: architecture-GTI_Router.md#Development Experience / CI]

### Project Structure Notes
- Archivo objetivo: `src/location/gps.py` (creado vacío con `__init__.py` en la Story 1.1; se llena aquí). Tests en `tests/location/`. [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 6 / Story 6.1]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Infrastructure & Deployment / Authentication & Security (Privacidad GPS RLS)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Complete Project Directory Structure (location/gps.py — solo Pro)]
- [Source: project-planning-artifacts/gtisatelites-brownfield-database.md#8 / #10 (routers.location jsonb, RLS GPS)]
- [Source: prd-GTI_Router-2026-01-22.md#FR20, NFR14] (captura GPS Pro, RLS de coordenadas)

### Notas de contexto del proyecto
- FR20 ([Pro]) = capturar coordenadas GPS y persistirlas en `routers` para el mapa 3D. NFR14 = GPS protegido por RLS. La posición 3D en sí (markers) la consume la Épica 8 en Satélites. [Source: epics.md#FR Coverage Map (FR20: E6 + E8)]
- Depende de la **Épica 0**: la columna `routers.location` y la RLS de GPS ya existen/se definen allí. Esta story NO toca el esquema.

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
