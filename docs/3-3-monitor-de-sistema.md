# Story 3.3: Monitor de sistema

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router**,
I want **monitorear continuamente CPU, RAM, disco y temperatura con umbrales configurables**,
so that **pueda reportar el estado y tomar acciones preventivas (alertas, throttling) antes de degradar la captura**.

## Acceptance Criteria

1. **`SystemMonitor`:** existe `src/health/monitor.py` con la clase de servicio `SystemMonitor` (`async start()` / `async stop()`) que muestrea periódicamente CPU, RAM, disco y temperatura usando `psutil` (y la fuente de temperatura del RPi cuando aplique).
2. **Umbrales configurables:** los umbrales de alerta (CPU, RAM, disco, temperatura) se leen de `get_config()` (bloque `health` del YAML) — nunca de `os.environ`/YAML directo fuera de `src/config/`.
3. **Flags de alerta:** al exceder un umbral, el monitor marca el flag de alerta correspondiente (expuesto en el estado de app para que `HealthReporter` 3.2 lo consuma).
4. **Temperatura crítica:** ante temperatura crítica (**>80°C**) loguea **WARNING** y marca un flag de `throttling` (NFR3 fija el techo en 75°C sostenido; >80°C es la condición crítica de la story).
5. **Lectura para el reporter:** el monitor expone su último muestreo (CPU/RAM/disco/temperatura + flags) de forma que el `HealthReporter` lo lea sin remuestrear psutil por su cuenta.
6. **Métricas con unidad:** las métricas se nombran en `snake_case` con sufijo de unidad (`cpu_percent`, `memory_percent`, `disk_percent`, `temperature_celsius`, …).
7. **No-bloqueante:** el muestreo no bloquea el event loop (offload de llamadas potencialmente bloqueantes de psutil cuando corresponda).
8. **Errores tipados:** los fallos de lectura usan excepciones de `src/utils/errors.py`; **prohibido** `raise Exception(...)` genérico; un fallo de muestreo no tumba el monitor (se loguea y continúa).
9. **Tests con mocks de psutil:** `tests/health/test_monitor.py` verifica que se marcan flags al exceder umbrales, que >80°C produce WARNING + flag de throttling, y la nomenclatura de métricas. Todo con `psutil` mockeado, sin hardware.

## Tasks / Subtasks

- [ ] **Task 1: Implementar `SystemMonitor`** (AC: #1, #2, #5, #7)
  - [ ] `src/health/monitor.py`: clase con `async start()`/`async stop()` y loop de muestreo periódico (intervalo configurable)
  - [ ] Leer CPU/RAM/disco/temperatura con `psutil` sin bloquear el event loop
  - [ ] Leer umbrales del bloque `health` vía `get_config()`
  - [ ] Exponer el último snapshot + flags al estado de app (consumo por `HealthReporter`)
- [ ] **Task 2: Lógica de umbrales y temperatura crítica** (AC: #3, #4)
  - [ ] Marcar flags de alerta por métrica al exceder umbral
  - [ ] Temperatura crítica >80°C → log WARNING + flag `throttling`
- [ ] **Task 3: Nomenclatura de métricas** (AC: #6)
  - [ ] Nombrar con sufijo de unidad (`*_percent`, `*_celsius`, `*_bytes`)
- [ ] **Task 4: Robustez** (AC: #8)
  - [ ] Capturar fallos de lectura con excepciones tipadas; no tumbar el loop
- [ ] **Task 5: Tests** (AC: #9)
  - [ ] `tests/health/test_monitor.py` con `psutil` mockeado: flags por umbral, crítica >80°C, naming

## Dev Notes

**Prerrequisito (Épica 0):** esta story no escribe en Supabase directamente (lo hace 3.2 vía `router_health`), pero el bloque que produce (métricas de sistema) alimenta el report de la **Story 3.2**, que sí depende de `router_health` (Épica 0, Story 0.4). El `SystemMonitor` en sí solo lee del SO. [Source: epics.md#Story 0.4]

### Contrato / responsabilidad
- El `SystemMonitor` muestrea cpu/ram/disco/temperatura y marca flags al exceder umbrales; ante temperatura crítica (>80°C) loguea WARNING y marca throttling. [Source: epics.md#Story 3.3]
- NFR3: temperatura de CPU **<75°C sostenido** es el objetivo; el monitor vigila y, en condición crítica >80°C, alerta/throttling. [Source: epics.md#NonFunctional Requirements (NFR3)]
- NFR1 (CPU passthrough <30%) y NFR2 (RAM por variante) son los presupuestos que estas métricas vigilan. [Source: architecture-GTI_Router.md#Requirements Overview]

### Relación con otras stories
- **3.2 (Health Reporter):** **consume** el snapshot del `SystemMonitor`; el reporter no remuestrea psutil. Coordinar la interfaz de lectura (estado de app compartido).
- **3.4 (auto-recuperación RTSP):** independiente — esa vigila el stream, esta vigila el host.

### Patrones obligatorios (heredados de 1.1)
- **Logging:** journald, formato `{timestamp} [{level}] [{module}] {message}`; niveles documentados (WARNING para temperatura crítica). [Source: architecture-GTI_Router.md#Process Patterns]
- **Errores tipados:** prohibido `Exception` genérico. [Source: architecture-GTI_Router.md#Format Patterns]
- **Config:** umbrales solo vía `get_config()`; prohibido leer YAML/`os.environ` fuera de `src/config/`. [Source: architecture-GTI_Router.md#Process Patterns]
- **Métricas:** `snake_case` + sufijo de unidad (`*_percent`, `*_celsius`, `*_bytes`). [Source: architecture-GTI_Router.md#Naming Patterns]
- **No bloquear el event loop** con llamadas potencialmente bloqueantes. [Source: architecture-GTI_Router.md#Enforcement Guidelines]
- **Servicio:** una clase por módulo con `async start()`/`async stop()`. [Source: architecture-GTI_Router.md#Structure Patterns]

### Dependencias de runtime
- `psutil` ya está fijado como dependencia en la Story 1.1 — **no** agregar versiones nuevas. [Source: stories/1-1-scaffold-del-proyecto-logging-y-retry.md (AC #2)]

### Testing standards
- `pytest` + `pytest-asyncio`; mockear `psutil` (y la fuente de temperatura) para simular umbrales excedidos. Hardware real = checklist manual en RPi. [Source: architecture-GTI_Router.md#Testing Framework / CI]

### Project Structure Notes
```
src/health/
├── registration.py   (Story 3.1)
├── reporter.py       (Story 3.2 — consume este monitor)
├── monitor.py        ← ESTA STORY (SystemMonitor: cpu/ram/disk/temp; umbrales)
└── watchdog.py       (Story 3.5)
tests/health/test_monitor.py   ← ESTA STORY
```
[Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 3 / Story 3.3]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Cross-Cutting Concerns (Observabilidad)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Implementation Patterns & Consistency Rules]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Requirements Overview (NFR1/NFR2/NFR3)]

### Notas de contexto del proyecto
- Reutilizar logging y errores tipados de 1.1; no reinventar el patrón de servicio. [Source: stories/1-1-scaffold-del-proyecto-logging-y-retry.md]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
