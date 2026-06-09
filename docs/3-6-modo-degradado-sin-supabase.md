# Story 3.6: Modo degradado sin Supabase

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router**,
I want **seguir operando aunque Supabase no esté disponible (registro/health encolados, sin bloquear)**,
so that **la captura y el upload de video continúen sin depender de un servicio externo**.

## Acceptance Criteria

1. **No-bloqueo:** cuando Supabase no está disponible, el intento de registro (3.1) o de reporte de health (3.2) **no bloquea** el Router — la captura y el upload a S3 siguen su curso normal. Jamás se bloquea el event loop esperando a Supabase.
2. **Cola local de health (1h FIFO):** los health reports que no se pudieron enviar se **encolan localmente** con cap temporal de **1h** y política **FIFO** (al exceder 1h se descarta lo más viejo). Es la misma cola que consume la Story 3.2 al reconectar (enviando en batch).
3. **Reintento periódico:** el Router reintenta la conexión a Supabase cada **60s** (configurable); al reconectar, drena la cola encolada y reanuda registro/health normales.
4. **Flag `supabase_connected`:** existe un flag observable `supabase_connected` (bool) expuesto en las métricas/estado de app, que `HealthReporter` incluye en el report y que refleja el estado real de conectividad.
5. **PTZ inactivo sin `gateway_id`:** si el registro no se completó (sin `gateway_id` vinculado), el control PTZ (E4) queda **inactivo**, y este comportamiento está **documentado** (log INFO claro al arrancar en modo degradado). El Router no intenta operar PTZ sin vínculo.
6. **Mecanismo compartido, no duplicado:** el modo degradado es un mecanismo transversal único reutilizado por `registration.py` y `reporter.py` (cola local + flag + reintento), no una implementación separada por módulo.
7. **Errores tipados:** los fallos de Supabase se capturan con excepciones de `src/utils/errors.py` (`SupabaseError`); **prohibido** `raise Exception(...)` genérico; los transitorios alimentan `@with_retry`, los permanentes se loguean sin reintentar.
8. **Tests:** `tests/health/test_degraded_mode.py` verifica: captura/upload continúan con Supabase caído (no-bloqueo), encolado FIFO con cap 1h, drenado al reconectar, transición del flag `supabase_connected`, y PTZ inactivo sin `gateway_id`. Sin red real.

## Tasks / Subtasks

- [ ] **Task 1: Mecanismo de cola local + flag compartido** (AC: #2, #4, #6)
  - [ ] Implementar (o consolidar desde 3.1/3.2) la cola local FIFO de health con cap temporal de **1h** y el flag `supabase_connected`
  - [ ] Exponer ambos al estado de app para que `registration.py`/`reporter.py` los reutilicen
- [ ] **Task 2: No-bloqueo y reintento periódico** (AC: #1, #3)
  - [ ] Garantizar que ningún path de Supabase bloquea captura/upload ni el event loop
  - [ ] Loop de reintento cada 60s (configurable); al reconectar, drenar la cola en batch y marcar `supabase_connected=true`
- [ ] **Task 3: PTZ inactivo sin gateway_id** (AC: #5)
  - [ ] Si no hay `gateway_id` (registro no completado), no activar PTZ; loguear INFO documentando el modo degradado
- [ ] **Task 4: Errores tipados** (AC: #7)
  - [ ] Capturar fallos con `SupabaseError`; transitorio → `@with_retry`, permanente → log sin reintento
- [ ] **Task 5: Tests** (AC: #8)
  - [ ] `tests/health/test_degraded_mode.py`: no-bloqueo, cola FIFO 1h, drenado, flag, PTZ inactivo

## Dev Notes

**Prerrequisito (Épica 0):** el modo degradado existe precisamente para tolerar la ausencia de Supabase, pero cuando Supabase **sí** responde, escribe en `routers` (registro, 3.1) y `router_health` (health, 3.2), tablas/columnas creadas en la **Épica 0 (Stories 0.4 y 0.6)**. El mecanismo degradado en sí es local y no requiere esquema. [Source: epics.md#Story 0.4 / Story 0.6]

### Contrato / responsabilidad (de la arquitectura — AR7)
- **Modo degradado obligatorio sin Supabase:** patrón único `@with_retry` + modo degradado obligatorio. Toda llamada a Supabase es no-bloqueante y tolerante a fallo. [Source: epics.md#Additional Requirements (AR7)] [Source: architecture-GTI_Router.md#Process Patterns / Cross-Cutting Concerns (Tolerancia a fallos / modo degradado)]
- Con Supabase no disponible: continúa sin bloquear (captura/upload siguen), encola health localmente (**máx 1h FIFO**) y reintenta cada 60s; expone `supabase_connected`; sin `gateway_id` el PTZ queda inactivo (documentado). [Source: epics.md#Story 3.6]
- **Jamás perder segmentos no subidos** ni bloquear el event loop esperando Supabase. [Source: architecture-GTI_Router.md#Cross-Cutting Concerns / Enforcement Guidelines]

### Relación con otras stories de la Épica 3 (clave: no duplicar)
- **3.1 (registro)** y **3.2 (health)** ya describen el comportamiento degradado parcial; **esta story consolida el mecanismo transversal** (cola local + flag + reintento) que ambas reutilizan. Implementar una sola vez y que 3.1/3.2 lo consuman. [Source: epics.md#Story 3.1 / Story 3.2]
- **E4 (PTZ):** depende del `gateway_id` del registro; sin él, PTZ inactivo. [Source: epics.md#Story 3.6]

### Patrones obligatorios (heredados de 1.1)
- **Supabase no-bloqueante y degradable** (regla de proceso central). [Source: architecture-GTI_Router.md#Process Patterns]
- **Retry único `@with_retry`** (backoff 1→60s + jitter); permanentes (403/404) no se reintentan. [Source: architecture-GTI_Router.md#Process Patterns / API & Communication Patterns]
- **Errores tipados**; **logging** a journald (INFO para el aviso de modo degradado). [Source: architecture-GTI_Router.md#Format / Process Patterns]
- **Métricas** `snake_case` (`supabase_connected`). [Source: architecture-GTI_Router.md#Naming Patterns]
- **Config:** intervalo de reintento y cap de cola vía `get_config()`. [Source: architecture-GTI_Router.md#Process Patterns]

### Frontera cloud
`health/` encapsula toda interacción con Supabase tras `@with_retry` + modo degradable; el resto del código nunca llama a Supabase directo, así que el modo degradado vive aquí. [Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera cloud)]

### Testing standards
- `pytest` + `pytest-asyncio`; simular Supabase caído (cliente mockeado que falla y luego responde); verificar continuidad de captura/upload, encolado y drenado. [Source: architecture-GTI_Router.md#Testing Framework / CI]

### Project Structure Notes
El mecanismo vive en `src/health/` (transversal a `registration.py` y `reporter.py`). Evitar duplicar la cola local; consolidarla como utilidad compartida del módulo `health`.
```
src/health/
├── registration.py   (3.1 — consume el mecanismo degradado)
├── reporter.py       (3.2 — consume la cola local 1h)
├── monitor.py        (3.3)
└── watchdog.py       (3.5)
tests/health/test_degraded_mode.py   ← ESTA STORY
```
[Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 3 / Story 3.6]
- [Source: _bmad-output/gti-router/epics.md#Additional Requirements (AR7)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Cross-Cutting Concerns Identified]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Implementation Patterns & Consistency Rules]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Architectural Boundaries (Frontera cloud)]

### Notas de contexto del proyecto
- Reutilizar `@with_retry`, logging y errores de 1.1; consolidar (no duplicar) la cola/flag de 3.1/3.2. [Source: stories/1-1-scaffold-del-proyecto-logging-y-retry.md]

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
