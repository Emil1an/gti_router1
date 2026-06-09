# Story 1.2: Sistema de configuración YAML validada

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **técnico de instalación**,
I want **configurar el Router con un `router.yaml` validado y sin terminal**,
so that **se especifiquen cámaras y parámetros (HLS, AWS, Supabase, device, health, licensing) sin tocar código, con fail-fast claro ante errores**.

## Acceptance Criteria

1. **Modelos pydantic:** `src/config/schema.py` define los modelos `pydantic-settings~=2.14` del `router.yaml`: `RouterConfig` (raíz) con `cameras: list[CameraConfig]`, y bloques `hls`, `aws`, `supabase`, `device`, `health`, `licensing`. `CameraConfig` incluye `camera_id`, `input_type` (`rtsp_ip` | `capture_card`), `orientation` (azimut/tilt/fov/altura) y `gps` opcional.
2. **`get_config()` único:** `src/config/loader.py` expone `get_config() -> RouterConfig` como **única** vía de acceso a configuración del proyecto. Carga el YAML, lo valida con pydantic-settings y cachea el resultado (singleton). Ningún otro módulo lee YAML ni `os.environ` directamente.
3. **Expansión de `${ENV}`:** los valores del YAML que usen la sintaxis `${VAR_NAME}` se expanden desde variables de entorno antes de validar (los secretos AWS/Supabase viven en env, NUNCA en el YAML — NFR9).
4. **Fail-fast:** si el YAML es inválido (campo faltante, tipo incorrecto, `input_type` desconocido, rango fuera de límites, `${ENV}` no resuelta para un secreto requerido), `get_config()` lanza `ConfigValidationError` (tipada, de `src/utils/errors.py`) con un mensaje que indica el campo y el problema. No arranca con config inválida.
5. **Copia boot→etc en primer arranque:** si existe `/boot/router.yaml` y no existe `/etc/gti-router/router.yaml`, el loader copia el primero al segundo con permisos seguros (`0600`, propietario root) en el primer arranque. La ruta de config es resoluble vía orden de precedencia documentado (`/etc/gti-router/router.yaml` → `/boot/router.yaml` → ruta de `${ROUTER_CONFIG}` para desarrollo).
6. **Validación de cámaras:** cada cámara valida que `input_type: rtsp_ip` tenga `rtsp_url` y que `input_type: capture_card` tenga `device` (p. ej. `/dev/video0`); `orientation` valida rangos (azimut 0–360, tilt y fov_h plausibles). `camera_id` único por router.
7. **`router.yaml.example`:** existe `config/router.yaml.example` documentado con todos los bloques (lista `cameras` con `input_type`/`orientation`/`gps`, `hls`, `aws`, `supabase`, `device`, `health`, `licensing`), usando `${ENV}` para los secretos.
8. **Tests:** tests unitarios para config válida, cada modo de fallo (campo faltante, tipo malo, `input_type` inválido, rango de orientación fuera de límites, secreto `${ENV}` ausente), expansión de `${ENV}` y la copia boot→etc (con FS mockeado/tmp).

## Tasks / Subtasks

- [ ] **Task 1: Definir modelos pydantic** (AC: #1, #6)
  - [ ] `src/config/schema.py`: `RouterConfig`, `CameraConfig`, `Orientation`, `Gps`, `HlsConfig`, `AwsConfig`, `SupabaseConfig`, `DeviceConfig`, `HealthConfig`, `LicensingConfig` con `pydantic-settings~=2.14`
  - [ ] `input_type` como `Literal["rtsp_ip", "capture_card"]`; validador que exige `rtsp_url` para `rtsp_ip` y `device` para `capture_card`
  - [ ] Validar rangos de `orientation` (azimut 0–360, tilt/fov_h plausibles) y unicidad de `camera_id`
  - [ ] `hls.segment_duration` con default 4 y rango 2–8 (FR2)
- [ ] **Task 2: Implementar `get_config()`** (AC: #2, #3, #4)
  - [ ] `src/config/loader.py`: `get_config()` singleton (cachea `RouterConfig`); carga YAML con `PyYAML`
  - [ ] Expansión de `${ENV}` sobre los valores string antes de validar
  - [ ] Mapear errores de validación de pydantic a `ConfigValidationError` con campo + causa; fail-fast
- [ ] **Task 3: Resolución de ruta y copia boot→etc** (AC: #5)
  - [ ] Orden de precedencia `/etc/gti-router/router.yaml` → `/boot/router.yaml` → `${ROUTER_CONFIG}`
  - [ ] En primer arranque, copiar `/boot/router.yaml`→`/etc/gti-router/router.yaml` con permisos `0600` root
- [ ] **Task 4: `router.yaml.example`** (AC: #7)
  - [ ] Crear `config/router.yaml.example` con todos los bloques documentados y `${ENV}` para secretos
- [ ] **Task 5: Tests** (AC: #8)
  - [ ] `tests/config/test_loader.py` y `tests/config/test_schema.py`: caso válido + cada modo de fallo + expansión `${ENV}` + copia boot→etc (con `tmp_path`)

## Dev Notes

**Esta story implementa el patrón ÚNICO de configuración que la Story 1.1 dejó como hueco. A partir de aquí, TODO acceso a config es vía `get_config()`; ningún agente lee YAML ni `os.environ` fuera de `src/config/`.** [Source: architecture-GTI_Router.md#Process Patterns]

### Stack y versiones
- **`pydantic-settings~=2.14`** para validar el `router.yaml`, con **fail-fast** al inicio. Ya está como dependencia desde la Story 1.1 — aquí se **usa** por primera vez. [Source: architecture-GTI_Router.md#Data Architecture (D4)]
- **`PyYAML`** para parsear el YAML (ya en deps de 1.1).
- Compatible con Python 3.11–3.14. [Source: architecture-GTI_Router.md#Coherence Validation]

### Reglas de proceso que esta story materializa
- **Config:** acceso solo vía `get_config()`; **prohibido** `os.environ`/YAML directo fuera de `src/config/`. [Source: architecture-GTI_Router.md#Process Patterns]
- **Frontera de configuración:** `src/config/` es el único módulo que lee YAML/env; todo lo demás recibe objetos tipados. [Source: architecture-GTI_Router.md#Architectural Boundaries]
- **Errores:** usar la jerarquía tipada de `src/utils/errors.py` (`ConfigValidationError`); **prohibido** `raise Exception(...)` genérico. [Source: architecture-GTI_Router.md#Format Patterns]
- **Secretos:** credenciales AWS/Supabase **solo** en variables de entorno (NFR9), nunca en el YAML — por eso la expansión `${ENV}`. [Source: architecture-GTI_Router.md#Authentication & Security]

### Patrones reutilizados de la Story 1.1 (NO redefinir)
- `@with_retry`, logging journald + `camera_id`, errores tipados, naming `snake_case` ya existen. Esta story solo añade `ConfigValidationError` si no estuviera ya como placeholder.
- No reimplementar logging ni retry; importarlos de `src/utils/`.

### Estructura del `router.yaml` (de la arquitectura)
Bloques esperados: lista `cameras` (cada una con `input_type` + `orientation` + `gps`), `hls`, `aws`, `supabase`, `device`, `health`, `licensing`. [Source: architecture-GTI_Router.md#Complete Project Directory Structure (config/router.yaml.example)]

### Anti-patrones a evitar
- ❌ leer `os.environ` o YAML fuera de `src/config/` · ❌ secretos en el YAML · ❌ `raise Exception(...)` genérico · ❌ arrancar con config inválida (debe ser fail-fast). [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Testing standards
- `pytest` + `pytest-asyncio`; usar `tmp_path`/`monkeypatch` para FS y env vars. Sin hardware. [Source: architecture-GTI_Router.md#Testing Framework]

### Project Structure Notes
Archivos de esta story (los paquetes vacíos ya los creó la 1.1):
```
src/config/loader.py     # get_config() + copia boot→etc + expansión ${ENV} + fail-fast
src/config/schema.py     # modelos pydantic (RouterConfig, CameraConfig, Orientation, Gps, ...)
config/router.yaml.example
tests/config/test_loader.py, test_schema.py
```
[Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 1 / Story 1.2]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Core Architectural Decisions (D4)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Implementation Patterns & Consistency Rules]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Architectural Boundaries (Frontera de configuración)]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
