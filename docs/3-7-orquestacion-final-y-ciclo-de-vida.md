# Story 3.7: Orquestación final y ciclo de vida

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **operador del sistema**,
I want **inicio y apagado ordenados de todos los componentes del Router**,
so that **el dispositivo opere de forma predecible y no pierda datos durante el shutdown (24/7)**.

## Acceptance Criteria

1. **Init de 12 pasos:** `src/main.py` (`async def main()` — solo orquestación) ejecuta la secuencia de inicialización en **12 pasos** en orden, con **fail-fast** en config y cámara (exit codes definidos) y **modo degradado** en Supabase (no aborta si Supabase no responde).
2. **Componentes con `start()`/`stop()`:** cada componente (config/log, registro 3.1, health reporter 3.2, monitor 3.3, watchdog 3.5, modo degradado 3.6, pipeline/captura E1, upload E2) expone `async start()` / `async stop()` y el orquestador los arranca/cierra en orden; ninguno contiene lógica de negocio en `main.py`.
3. **Shutdown de 6 pasos:** ante `SIGTERM`/`SIGINT`, ejecuta un shutdown ordenado en **6 pasos** con timeout configurable (**default 30s**), respetando `asyncio.CancelledError` y dando tiempo a uploads en vuelo.
4. **Health report final:** durante el shutdown emite un **health report final** (estado de cierre) antes de salir, en lo posible (best-effort si Supabase no responde).
5. **Exit codes:** retorna **exit 0 solo si el shutdown fue limpio**; usa los exit codes definidos (0 ok, 1 config, 2 cámara, 3 pipeline) heredados de la Story 1.5.
6. **READY=1 / sd_notify:** al completar el init de 12 pasos emite `sd_notify("READY=1")` (coordinado con el Watchdog 3.5); el heartbeat del watchdog corre durante toda la operación.
7. **Degradación correcta por capa:** config inválida → fail-fast (exit 1); cámara no conecta → fail-fast (exit 2) salvo política de reintento de la cámara (3.4); Supabase caído → continúa en modo degradado (3.6), no aborta.
8. **No pérdida de datos:** el shutdown persiste la cola de upload (SQLite) y no descarta segmentos no subidos; el buffer queda íntegro para el próximo arranque.
9. **Tests:** `tests/test_main.py` verifica el orden del init (12 pasos), el orden del shutdown (6 pasos) con timeout, los exit codes por escenario (config/cámara/pipeline ok/error), el report final y READY=1. Con componentes mockeados (`start`/`stop`), sin hardware.

## Tasks / Subtasks

- [ ] **Task 1: Secuencia de init (12 pasos)** (AC: #1, #2, #6, #7)
  - [ ] En `src/main.py`, orquestar el arranque en 12 pasos: config→log→registro→monitor→pipeline/captura→upload→health reporter→watchdog→… (sin lógica de negocio en `main.py`)
  - [ ] Fail-fast en config (exit 1) y cámara (exit 2); modo degradado en Supabase (no aborta)
  - [ ] Emitir `sd_notify("READY=1")` al terminar el init (coordinar con 3.5)
- [ ] **Task 2: Manejo de señales y shutdown (6 pasos)** (AC: #3, #8)
  - [ ] Capturar `SIGTERM`/`SIGINT` y disparar shutdown ordenado en 6 pasos con timeout configurable (default 30s)
  - [ ] Cancelar tasks respetando `asyncio.CancelledError`; esperar uploads en vuelo (máx timeout) y persistir la cola en SQLite
- [ ] **Task 3: Health report final** (AC: #4)
  - [ ] Emitir un report final de cierre (best-effort si Supabase no responde)
- [ ] **Task 4: Exit codes** (AC: #5)
  - [ ] Retornar exit 0 solo en shutdown limpio; 1 config, 2 cámara, 3 pipeline
- [ ] **Task 5: Tests** (AC: #9)
  - [ ] `tests/test_main.py` con componentes mockeados: orden init/shutdown, timeout, exit codes, report final, READY=1

## Dev Notes

**Prerrequisito (Épica 0):** el report final y el registro orquestados aquí escriben en `routers`/`router_health` (Épica 0, Stories 0.4/0.6) cuando Supabase responde; si no, el modo degradado (3.6) los encola. El orquestador en sí no depende del esquema, pero coordina componentes que sí. [Source: epics.md#Story 0.4 / Story 0.6]

**Esta es la story de cierre de la Épica 3 e integra E1–E3:** completa `main.py` (esbozado en 1.5) integrando captura (E1), upload (E2) y los componentes de health/resiliencia (3.1–3.6). Es el par final de la orquestación iniciada en la **Story 1.5**. [Source: epics.md#Story 1.5 / Story 3.7]

### Contrato / responsabilidad (de la arquitectura)
- **Init 12 pasos / shutdown 6 pasos:** arquitectura de servicio = monolito asyncio con workers; colas con límites y persistencia; **init 12 pasos / shutdown 6 pasos**. [Source: architecture-GTI_Router.md#Technical Constraints & Dependencies] [Source: architecture-GTI_Router.md#Complete Project Directory Structure (main.py)]
- `main.py` **solo orquesta** — sin lógica de negocio. [Source: architecture-GTI_Router.md#Structure Patterns]
- **Shutdown:** workers respetan `asyncio.CancelledError` y limpian en `stop()`. [Source: architecture-GTI_Router.md#Process Patterns]
- Secuencia de implementación de la arquitectura: config → VideoSource/encoder → pipeline+buffer → upload → health/registro → … (orden de arranque coherente). [Source: architecture-GTI_Router.md#Decision Impact Analysis]
- El graceful shutdown espera uploads (máx 30s) y persiste la cola en SQLite. [Source: epics.md#Story 2.6]

### Exit codes y señales (de la Story 1.5)
- `async main()` maneja `SIGTERM`/`SIGINT` graceful y usa exit codes (0 ok, 1 config, 2 cámara, 3 pipeline); inicializa en secuencia (config→log→cámara→pipeline) en el event loop. Esta story **extiende** esa orquestación a los 12 pasos completos con health/resiliencia. [Source: epics.md#Story 1.5]

### Degradación por capa
- **Config** inválida → fail-fast (`pydantic-settings`, exit 1). [Source: architecture-GTI_Router.md#Data Architecture (D4)]
- **Cámara** no disponible → fail-fast (exit 2) o política de reintento de la cámara (3.4) según diseño. [Source: epics.md#Story 3.4]
- **Supabase** caído → modo degradado (3.6), nunca aborta. [Source: epics.md#Story 3.6] [Source: architecture-GTI_Router.md#Cross-Cutting Concerns]

### Coordinación con el Watchdog (3.5)
- El `READY=1` se emite al final del init (un solo punto de emisión, coordinado con 3.5); el heartbeat `sd_notify` (15s) corre durante toda la operación bajo `WatchdogSec=30`. [Source: epics.md#Story 3.5] [Source: architecture-GTI_Router.md#Infrastructure & Deployment]

### Patrones obligatorios (heredados de 1.1)
- **Servicios** con `async start()`/`async stop()`; **una clase por módulo**; `main.py` solo orquesta. [Source: architecture-GTI_Router.md#Structure Patterns]
- **Errores tipados** (prohibido `Exception` genérico); **logging** a journald. [Source: architecture-GTI_Router.md#Format / Process Patterns]
- **Config** vía `get_config()`; timeout de shutdown configurable. [Source: architecture-GTI_Router.md#Process Patterns]

### Testing standards
- `pytest` + `pytest-asyncio`; mockear cada componente (`start`/`stop`) y las señales; verificar orden, timeout y exit codes sin hardware. [Source: architecture-GTI_Router.md#Testing Framework / CI]

### Project Structure Notes
```
src/main.py   ← ESTA STORY (completa la orquestación: init 12 pasos / shutdown 6 pasos)
              # esbozada en 1.1 (placeholder) y 1.5 (orquestación base); aquí integra E1–E3
tests/test_main.py   ← ESTA STORY
```
Variance: `main.py` se creó como placeholder vacío en 1.1 y como orquestador base en 1.5; esta story lo completa con health/resiliencia. [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 3 / Story 3.7]
- [Source: _bmad-output/gti-router/epics.md#Story 1.5 / Story 2.6]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Technical Constraints & Dependencies (init 12 / shutdown 6)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Implementation Patterns & Consistency Rules]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Infrastructure & Deployment]

### Notas de contexto del proyecto
- Reutilizar `@with_retry`, logging, errores y exit codes de 1.1/1.5; `main.py` integra (no reimplementa) los componentes de 3.1–3.6. [Source: stories/1-1-scaffold-del-proyecto-logging-y-retry.md]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
