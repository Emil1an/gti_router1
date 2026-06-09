# Story 3.5: Watchdog systemd

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **operador del sistema**,
I want **que systemd reinicie el servicio Router si se cuelga, mediante un heartbeat `sd_notify`**,
so that **el Router se recupere de crashes/colgadas sin intervención manual (24/7)**.

## Acceptance Criteria

1. **Heartbeat `sd_notify`:** existe `src/health/watchdog.py` con la clase de servicio `Watchdog` (`async start()` / `async stop()`) que envía `sd_notify("WATCHDOG=1")` cada **15s** mientras el event loop está sano, usando `systemd-python` (`sd_notify`).
2. **Unit con `WatchdogSec=30`:** el `systemd/gti-router.service` tiene `Type=notify` y `WatchdogSec=30` (el intervalo de heartbeat 15s = mitad de `WatchdogSec`, margen estándar).
3. **Reinicio ante cuelgue:** si el heartbeat se detiene (event loop colgado/bloqueado), systemd reinicia el servicio vía `Restart=on-failure` con límites `StartLimitIntervalSec`/`StartLimitBurst` configurados para evitar bucles de reinicio.
4. **Señal de READY:** el watchdog (o el orquestador) emite `sd_notify("READY=1")` cuando la inicialización termina, de acuerdo con `Type=notify` (coordinado con la Story 3.7 / 1.6).
5. **Heartbeat ligado a la salud del loop:** el heartbeat solo se envía si el event loop responde (la corrutina del watchdog efectivamente se ejecuta); si el loop está bloqueado, el `WATCHDOG=1` no llega y systemd actúa. No se envía un heartbeat "ciego" desde un hilo separado que enmascare un loop colgado.
6. **No falla fuera de systemd:** en desarrollo/CI (sin systemd / sin `NOTIFY_SOCKET`), `sd_notify` es no-op y no rompe la ejecución ni los tests.
7. **Errores tipados:** prohibido `raise Exception(...)` genérico; un fallo de `sd_notify` se loguea sin tumbar el Router.
8. **Tests:** `tests/health/test_watchdog.py` verifica el periodo de 15s (clock mockeado), el envío de `WATCHDOG=1`, el comportamiento no-op sin `NOTIFY_SOCKET`, y la emisión de `READY=1`. Con `systemd.daemon`/`sd_notify` mockeado.

## Tasks / Subtasks

- [ ] **Task 1: Implementar `Watchdog`** (AC: #1, #5, #6, #7)
  - [ ] `src/health/watchdog.py`: clase con `async start()`/`async stop()` y loop de 15s que llama `sd_notify("WATCHDOG=1")` vía `systemd-python`
  - [ ] El heartbeat corre como corrutina en el event loop (no en hilo aparte) para reflejar la salud real del loop
  - [ ] No-op si no hay `NOTIFY_SOCKET` (desarrollo/CI)
  - [ ] Capturar fallos de `sd_notify` con excepción tipada, loguear y continuar
- [ ] **Task 2: Señal READY** (AC: #4)
  - [ ] Emitir `sd_notify("READY=1")` al completar el init (coordinar con 3.7/1.6 para no duplicar)
- [ ] **Task 3: Configurar la unit systemd** (AC: #2, #3)
  - [ ] En `systemd/gti-router.service`: `Type=notify`, `WatchdogSec=30`, `Restart=on-failure`, `StartLimitIntervalSec`/`StartLimitBurst`
  - [ ] (La unit base la crea 1.6; aquí se añaden/confirman `WatchdogSec` y los límites de reinicio)
- [ ] **Task 4: Tests** (AC: #8)
  - [ ] `tests/health/test_watchdog.py` con `sd_notify` mockeado y clock: periodo, WATCHDOG=1, no-op sin socket, READY=1

## Dev Notes

**Prerrequisito (Épica 0):** ninguno directo — el watchdog no toca Supabase ni `router_health`. Es resiliencia local de proceso. (La Épica 0 sigue siendo prerrequisito global de la Épica 3 por el registro/health, pero esta story no depende del esquema.)

**Depende de la Épica 1 (Story 1.6 / 1.5):** la unit `gti-router.service` con `Type=notify` se crea en la **Story 1.6**; aquí se añade/confirma `WatchdogSec=30` y la política de reinicio. La señal `READY=1` se coordina con la orquestación (`main.py`, Story 1.5 → completada en 3.7). [Source: epics.md#Story 1.6 / Story 1.5]

### Contrato / responsabilidad
- `gti-router.service` con `WatchdogSec=30`; el Router envía heartbeat `sd_notify` cada **15s** vía `health/watchdog.py`; si el heartbeat se detiene, systemd reinicia (`Restart=on-failure`, límites `StartLimit*`). [Source: epics.md#Story 3.5]
- systemd `Type=notify` + `sd_notify` (watchdog), `Restart=on-failure`, `MemoryMax`/`CPUQuota` por variante, `OOMPolicy=kill`. [Source: architecture-GTI_Router.md#Infrastructure & Deployment]
- AR6: systemd `Type=notify` + `sd_notify` watchdog; límites por variante. [Source: epics.md#Additional Requirements (AR6)]

### Por qué el heartbeat va en el event loop
El watchdog detecta un **event loop colgado**: si el heartbeat corriera en un hilo independiente, seguiría enviando `WATCHDOG=1` aunque el loop principal estuviera bloqueado, enmascarando el fallo. Debe ejecutarse como corrutina del mismo loop que se quiere vigilar. [Source: architecture-GTI_Router.md#Infrastructure & Deployment]

### Patrones obligatorios (heredados de 1.1)
- **`systemd-python`** ya está fijado como dependencia en la Story 1.1 — no agregar otra. [Source: stories/1-1-scaffold-del-proyecto-logging-y-retry.md (AC #2)]
- **Logging** a journald; **errores tipados** (prohibido `Exception` genérico). [Source: architecture-GTI_Router.md#Process / Format Patterns]
- **Servicio:** una clase por módulo con `async start()`/`async stop()`; **shutdown** respeta `asyncio.CancelledError`. [Source: architecture-GTI_Router.md#Structure / Process Patterns]

### Relación con 3.7 (orquestación)
El orquestador (3.7) arranca/cierra el `Watchdog` como un componente más del ciclo de vida; el `READY=1` se emite al final del init de 12 pasos. Coordinar para que solo un punto emita `READY=1`. [Source: epics.md#Story 3.7]

### Testing standards
- `pytest` + `pytest-asyncio`; mockear `systemd.daemon.notify`/`sd_notify`; sin systemd real en CI. Validación en RPi = checklist manual. [Source: architecture-GTI_Router.md#Testing Framework / CI]

### Project Structure Notes
```
src/health/
├── registration.py   (Story 3.1)
├── reporter.py       (Story 3.2)
├── monitor.py        (Story 3.3)
└── watchdog.py       ← ESTA STORY (sd_notify heartbeat 15s)
systemd/gti-router.service   (1.6 — aquí se añade WatchdogSec=30 + StartLimit*)
tests/health/test_watchdog.py   ← ESTA STORY
```
[Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 3 / Story 3.5]
- [Source: _bmad-output/gti-router/epics.md#Story 1.6 (systemd) / Additional Requirements (AR6)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Infrastructure & Deployment]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Implementation Patterns & Consistency Rules]

### Notas de contexto del proyecto
- Reutilizar logging y errores de 1.1; la unit systemd base viene de 1.6 — esta story solo añade el watchdog y la política de reinicio. [Source: stories/1-1-scaffold-del-proyecto-logging-y-retry.md]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
