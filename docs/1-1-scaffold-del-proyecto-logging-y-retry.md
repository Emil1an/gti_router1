# Story 1.1: Scaffold del proyecto, logging y retry

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **desarrollador del equipo GTI**,
I want **un proyecto Python estructurado con logging y retry reutilizable desde el inicio**,
so that **se pueda desarrollar y diagnosticar el Router desde las primeras líneas, con patrones únicos que todos los agentes/stories siguientes reutilicen**.

## Acceptance Criteria

1. **Estructura del repo:** existe el repo `gti-router` con el árbol `src/` completo según la arquitectura (`config/`, `platform/`, `camera/sources/`, `pipeline/`, `upload/`, `storage/`, `health/`, `location/`, `utils/`), cada paquete con su `__init__.py` y docstring de módulo.
2. **Gestión de entorno:** `pyproject.toml` gestionado con `uv`, con dependencias **fijadas** de runtime (`aioboto3~=15.0`, `onvif-zeep`, `pydantic-settings~=2.14`, `PyYAML`, `psutil`, `pynmea2`, `systemd-python`) y de desarrollo (`pytest`, `pytest-asyncio`, `moto`, `ruff`); `uv.lock` versionado.
3. **`.gitignore`** cubre Python (`__pycache__`, `.venv`), secretos (`.env`, `*.key`) y artefactos de video (`*.ts`, `*.m3u8`, `*.jpg`).
4. **Logging:** `src/utils/logging.py` configura logging hacia journald con formato `{timestamp} [{level}] [{module}] {message}`, soporta `extra` JSON y permite incluir `camera_id` en el contexto por cámara. Niveles DEBUG/INFO/WARNING/ERROR documentados.
5. **Retry:** `src/utils/retry.py` expone el **único** decorator async `@with_retry` con backoff exponencial (1→60s) + jitter ±20% y nº máximo de reintentos configurable. Es la única fuente de retry del proyecto.
6. **Errores tipados:** `src/utils/errors.py` define la jerarquía base de excepciones por dominio (p. ej. `RouterError` y subclases placeholder) — **prohibido** `raise Exception(...)` genérico en el resto del código.
7. **Fixture de prueba:** existe `tests/fixtures/sample.mp4` (~10s, H.264) y la carpeta `tests/` espeja la estructura de `src/`.
8. **CI:** un workflow de GitHub Actions corre `ruff` (lint+format check) y `pytest` en x86 (sin hardware).
9. **README:** `README.md` con descripción, requisitos de hardware (RPi4 2GB Base / RPi5 Pro), y pasos de setup con `uv`.
10. **`main.py` placeholder:** `src/main.py` existe con `async def main()` vacío/orquestador mínimo (sin lógica de negocio) y un test humo que importa el paquete sin error.

## Tasks / Subtasks

- [ ] **Task 1: Inicializar el proyecto con uv** (AC: #2, #3, #9)
  - [ ] `uv init gti-router` y configurar `pyproject.toml` (Python `>=3.11`)
  - [ ] `uv add aioboto3~=15.0 onvif-zeep pydantic-settings~=2.14 PyYAML psutil pynmea2 systemd-python`
  - [ ] `uv add --dev pytest pytest-asyncio moto ruff`
  - [ ] Commitear `uv.lock`; crear `.gitignore` (Python, secretos, `*.ts`/`*.m3u8`/`*.jpg`)
  - [ ] Crear `ruff.toml` (o sección `[tool.ruff]`) con reglas de lint+format
  - [ ] Escribir `README.md` (descripción, HW RPi4/RPi5, setup con uv)
- [ ] **Task 2: Crear el árbol `src/`** (AC: #1, #10)
  - [ ] Crear paquetes `config/`, `platform/`, `camera/sources/`, `pipeline/`, `upload/`, `storage/`, `health/`, `location/`, `utils/` con `__init__.py` y docstring
  - [ ] Crear `src/main.py` con `async def main()` mínimo (solo orquestación, sin lógica)
- [ ] **Task 3: Implementar logging** (AC: #4)
  - [ ] `src/utils/logging.py`: setup hacia journald, formato `{timestamp} [{level}] [{module}] {message}`, soporte `extra` JSON y `camera_id` en contexto
  - [ ] Documentar niveles de log en docstring/README
  - [ ] Test unitario que verifica formato y que `camera_id` aparece en el registro
- [ ] **Task 4: Implementar `@with_retry`** (AC: #5)
  - [ ] `src/utils/retry.py`: decorator async con backoff exponencial 1→60s + jitter ±20%, `max_retries` configurable
  - [ ] Distinguir reintento de excepciones (parámetro de tipos a reintentar) sin reintentar permanentes
  - [ ] Tests: éxito tras N fallos, agotamiento de reintentos, respeto del backoff (con clock/sleep mockeado)
- [ ] **Task 5: Errores tipados** (AC: #6)
  - [ ] `src/utils/errors.py` con `RouterError` base y subclases placeholder (`ConfigError`, `RTSPError`, `S3UploadError`…)
- [ ] **Task 6: Tests y fixture** (AC: #7, #10)
  - [ ] Crear `tests/` espejando `src/`, `tests/conftest.py`, `tests/fixtures/`
  - [ ] Generar `tests/fixtures/sample.mp4` (~10s H.264) — ver Dev Notes para el comando ffmpeg
  - [ ] Test humo: importar el paquete y llamar `main()` sin efectos
- [ ] **Task 7: CI** (AC: #8)
  - [ ] `.github/workflows/ci.yml`: setup Python 3.11 + uv, `ruff check` + `ruff format --check`, `pytest` en `ubuntu-latest` (x86)

## Dev Notes

**Esta es la story fundacional: establece los patrones ÚNICOS que TODAS las stories siguientes reutilizan. No reinventar `@with_retry`, `get_config()` ni el logging en stories posteriores — esta los define de una vez.**

### Stack y versiones (verificadas, junio 2026 — NO cambiar sin razón)
- **Python 3.11** (default de Raspberry Pi OS Lite 64-bit *Bookworm*; venv obligatorio por **PEP 668**). [Source: architecture-GTI_Router.md#Selected Starter]
- **uv** para entorno + lockfile reproducible en toda la flota; `pip+venv` solo como fallback. `ruff` para lint+format.
- Deps fijadas: `aioboto3~=15.0` (última 15.5.0), `pydantic-settings~=2.14`, `onvif-zeep`, `PyYAML`, `psutil`, `pynmea2`, `systemd-python`. [Source: architecture-GTI_Router.md#Initialization Command]
- `pydantic-settings` se **usa** en la Story 1.2 (config); aquí solo se agrega como dependencia.
- `FFmpeg` (apt 5.1) NO se instala vía pip — es del sistema; se usará desde la Story 1.4. No agregarlo a `pyproject`.

### Comando de inicialización (de la arquitectura)
```bash
uv init gti-router && cd gti-router            # o python3 -m venv .venv (PEP 668 en RPi)
uv add aioboto3~=15.0 onvif-zeep pydantic-settings~=2.14 PyYAML psutil pynmea2 systemd-python
uv add --dev pytest pytest-asyncio moto ruff
# luego crear el árbol src/ a mano según la estructura de abajo
```
[Source: architecture-GTI_Router.md#Initialization Command (Story 1.1)]

### Patrones de naming y estructura (OBLIGATORIOS para todos los agentes)
- `snake_case` (funciones/variables/módulos/archivos), `PascalCase` (clases), `UPPER_SNAKE` (constantes).
- Corrutinas con prefijo verbal (`async def connect()`); los servicios exponen `async start()` / `async stop()`.
- Tests en `tests/` **espejando** `src/` (no co-locados); fixtures en `tests/fixtures/`.
- Una clase de servicio por módulo; utilidades transversales **solo** en `src/utils/`.
- `main.py` **solo orquesta** — sin lógica de negocio.
[Source: architecture-GTI_Router.md#Naming Patterns / Structure Patterns]

### Reglas de proceso que esta story materializa
- **Retry:** único `@with_retry` en `src/utils/retry.py` (backoff 1→60s + jitter ±20%). Ningún agente reimplementa retry. [Source: architecture-GTI_Router.md#Process Patterns]
- **Logging:** `logging`→journald, formato `{timestamp} [{level}] [{module}] {message}` + `extra` JSON; `camera_id` en contexto por cámara. [Source: architecture-GTI_Router.md#Process Patterns]
- **Errores:** excepciones tipadas por dominio; **prohibido** `raise Exception("...")` genérico. [Source: architecture-GTI_Router.md#Format Patterns]
- **Config (futuro 1.2):** acceso solo vía `get_config()`; prohibido `os.environ`/YAML directo fuera de `src/config/`. Dejar el hueco, no implementar aquí.

### Anti-patrones a evitar (de la arquitectura)
- ❌ `raise Exception("...")` genérico · ❌ retry ad-hoc con `time.sleep` · ❌ leer `os.environ` fuera de `src/config/` · ❌ poner lógica en `main.py`. [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Generar el fixture de video (AC #7)
```bash
ffmpeg -f lavfi -i testsrc=size=640x480:rate=25 -t 10 -c:v libx264 -pix_fmt yuv420p tests/fixtures/sample.mp4
```
(10s, H.264, para los tests de pipeline de la Story 1.4.)

### Testing standards
- `pytest` + `pytest-asyncio`; `moto` para mock de S3 (se usa desde la Story 2.x). Hardware real = checklist manual en RPi (no en CI).
- CI: GitHub Actions corre `ruff` + `pytest` en x86 con mocks. [Source: architecture-GTI_Router.md#Development Experience / CI]

### Project Structure Notes
Árbol objetivo (crear los paquetes vacíos con `__init__.py`; los archivos concretos los llenan stories posteriores):
```
gti-router/
├── pyproject.toml, uv.lock, ruff.toml, .gitignore, .env.example, README.md
├── .github/workflows/ci.yml
├── src/
│   ├── main.py                 # async main() — solo orquestación (1.5/3.7 lo completan)
│   ├── config/  (loader.py, schema.py → Story 1.2)
│   ├── platform/ (board.py → Story 5.5)
│   ├── camera/sources/ (base.py, rtsp_source.py, capture_card_source.py → 1.3/5.x)
│   ├── pipeline/ (ffmpeg_hls.py, buffer.py, snapshot.py → 1.4/2.4/6.3)
│   ├── upload/  (s3_client.py, queue.py → 2.x)
│   ├── storage/ (db.py SQLite → 2.2)
│   ├── health/  (registration, reporter, monitor, watchdog → 3.x)
│   ├── location/ (gps.py, orientation.py → 6.x)
│   └── utils/   (logging.py, retry.py, errors.py)   ← ESTA STORY
└── tests/ (conftest.py, fixtures/sample.mp4, + espejo de src/)
```
Variance: el `config/router.yaml.example` y `systemd/` se crean en 1.2/1.6 — no en esta story. [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 1 / Story 1.1]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Selected Starter / Initialization Command]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Implementation Patterns & Consistency Rules]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Complete Project Directory Structure]
- [Source: project-planning-artifacts/prd-GTI_Router-2026-01-22.md#Story 1.1] (criterios originales: README, log levels, docstrings, fixture)

### Notas de contexto del proyecto
- El repo `gti-router` es **nuevo** (greenfield) — se versiona aparte del monorepo bmad. No existe código previo que reutilizar; esta story crea la base.
- La Épica 0 (relinking de BD) corre en **paralelo** y NO bloquea esta story (es SQL/Supabase, no toca este repo Python).

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
