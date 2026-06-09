# Story 2.4: Buffer local y gestión de espacio

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router**,
I want **mantener segmentos en un buffer local cuando no puedo subirlos y gestionar el espacio en disco**,
so that **las desconexiones de red no causen pérdida de video (buffer ≥4h, FR5) sin nunca borrar segmentos no subidos**.

## Acceptance Criteria

1. **Buffer ≥4h:** `src/pipeline/buffer.py` gestiona el buffer local por cámara (segmentos `.ts` en FS) garantizando una capacidad mínima de **4 horas** de video durante desconexiones (FR5). La duración objetivo de retención es configurable (default ≥4h; rango 4–8h documentado).
2. **Monitoreo de espacio:** monitorea el espacio libre del filesystem del buffer (p. ej. vía `psutil`/`shutil.disk_usage`) y conoce el tamaño total ocupado por el buffer.
3. **FIFO solo de subidos:** cuando el espacio se agota / supera el umbral, aplica una política **FIFO** que elimina **únicamente segmentos cuyo estado en SQLite sea `uploaded`** (el más antiguo primero). Borrar un archivo también limpia/marca su fila en el índice de forma consistente.
4. **Nunca borra no-subidos:** los segmentos **no subidos** (`pending`/`uploading`/`failed`) **NUNCA** se eliminan, aunque eso implique llenar el disco — en ese caso se prioriza alertar y (si aplica) aplicar contrapresión, jamás perder video no subido.
5. **Alerta al 80%:** cuando el buffer/disco supera el **80%** de ocupación, marca una alerta que el health report (E3) podrá leer (flag/estado expuesto), logueando WARNING con `camera_id`.
6. **Coherencia con SQLite:** la decisión de qué borrar consulta el índice de la 2.2 (`storage/db.py`); el buffer **gestiona archivos**, el índice **gestiona estado** — fronteras separadas y consistentes.
7. **Tests:** `tests/pipeline/test_buffer.py` valida: FIFO borra solo `uploaded` y el más antiguo primero, jamás borra `pending`/`uploading`/`failed`, cálculo de capacidad/retención ≥4h, disparo de alerta al 80%, y consistencia archivo↔índice tras el borrado. Espacio/disco mockeado; sin hardware.

## Tasks / Subtasks

- [ ] **Task 1: Gestor de buffer por cámara** (AC: #1, #2)
  - [ ] `src/pipeline/buffer.py`: clase que conoce el dir de buffer por cámara, calcula tamaño ocupado y espacio libre (`shutil.disk_usage`/`psutil`)
  - [ ] Parámetros configurables vía `get_config()`: retención objetivo (≥4h), umbral de alerta (80%), umbral de limpieza
- [ ] **Task 2: Política FIFO solo de subidos** (AC: #3, #4, #6)
  - [ ] Al superar el umbral, consultar `storage/db.py` por segmentos `uploaded` ordenados por antigüedad y borrar de viejo→nuevo hasta liberar espacio
  - [ ] Guardas explícitas: jamás seleccionar `pending`/`uploading`/`failed` para borrado
  - [ ] Tras borrar el archivo, actualizar/limpiar la fila correspondiente en el índice
- [ ] **Task 3: Alerta y contrapresión** (AC: #4, #5)
  - [ ] Exponer flag/estado de "buffer >80%" para el health report; log WARNING con `camera_id`
  - [ ] Definir comportamiento si no hay nada `uploaded` que borrar y el disco está lleno (alertar; documentar contrapresión, no perder no-subidos)
- [ ] **Task 4: Tests** (AC: #7)
  - [ ] `tests/pipeline/test_buffer.py`: FIFO-solo-uploaded, nunca-borra-no-subidos, retención ≥4h, alerta 80%, coherencia archivo↔índice; disco mockeado

## Dev Notes

**La regla de oro de esta story: NUNCA se borra un segmento que no está confirmado como `uploaded` en SQLite. Antes que perder video no subido, se llena el disco y se alerta. El FIFO solo recicla lo ya seguro en S3.**

### Decisiones de arquitectura aplicables
- **Buffer por cámara en FS + índice en SQLite (D3):** segmentos en FS; estado en SQLite; fronteras separadas. [Source: architecture-GTI_Router.md#Data Architecture / Architectural Boundaries (Frontera de estado local)]
- **Política FIFO (solo subidos):** `pipeline/buffer.py` aplica FIFO eliminando solo segmentos ya subidos; los no subidos jamás se eliminan (FR5). [Source: architecture-GTI_Router.md#Complete Project Directory Structure (buffer.py); epics.md#Story 2.4]
- **Tolerancia a fallos:** "jamás perder segmentos no subidos" es un cross-cutting concern explícito de la arquitectura. [Source: architecture-GTI_Router.md#Cross-Cutting Concerns Identified (1)]
- **Alerta al 80% vía health:** el estado del buffer se reporta en el health report de E3. [Source: epics.md#Story 2.4]

### Patrones obligatorios (de la 1.1 / arquitectura)
- **Logging/métricas:** journald + `camera_id` en contexto; métricas con sufijo de unidad (`*_bytes`, `*_percent`). [Source: architecture-GTI_Router.md#Process Patterns / Naming Patterns]
- **Errores tipados:** **prohibido** `raise Exception(...)` genérico. [Source: architecture-GTI_Router.md#Format Patterns]
- **Config:** umbrales/retención solo vía `get_config()`. [Source: architecture-GTI_Router.md#Process Patterns]
- **Frontera de estado:** `storage/db.py` es la única fuente de estado; `buffer.py` solo toca archivos y consulta ese estado. [Source: architecture-GTI_Router.md#Architectural Boundaries]

### Notas de diseño
- `buffer.py` ya está listado en el árbol como "buffer por cámara + política FIFO (solo subidos)" — esta story lo implementa. [Source: architecture-GTI_Router.md#Complete Project Directory Structure]
- La estimación de "4h" puede derivarse de `segment_duration` (2–8s, de 1.4) × nº de segmentos × tamaño promedio, o como ventana temporal por `created_at`. Documentar el método elegido.
- Esta story **no** decide el orden de subida (FIFO de subida lo arma la cola/priorización 2.5); aquí FIFO es solo para **borrado** de lo ya subido.
- El snapshot last-frame (`pipeline/snapshot.py`) es de E6 — no tocar aquí.

### Anti-patrones a evitar
- ❌ Borrar segmentos `pending`/`uploading`/`failed` · ❌ borrar archivo sin reconciliar el índice · ❌ leer umbrales fuera de `get_config()` · ❌ `raise Exception` genérico · ❌ duplicar estado fuera de `storage/db.py`. [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Testing standards
- `pytest` + `pytest-asyncio`; mockear `disk_usage`/espacio para simular disco lleno; BD SQLite temporal con filas en distintos estados. CI en x86 sin hardware. [Source: architecture-GTI_Router.md#Development Experience / CI]

### Project Structure Notes
```
src/pipeline/
├── ffmpeg_hls.py    # HLSPipeline (Story 1.4)
├── buffer.py        # buffer por cámara + FIFO (solo subidos) + monitoreo de espacio  ← ESTA STORY
└── snapshot.py      # last-frame → Story 6.3 (no tocar)
src/storage/db.py    # índice consultado para saber qué está uploaded (de 2.2)
tests/pipeline/test_buffer.py   ← ESTA STORY
```
Variance: la priorización realtime/backlog 3:1 (orden de subida) es 2.5; el wiring E2E pipeline→cola→buffer es 2.6; el reporte de la alerta lo consume el HealthReporter en E3. [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 2 / Story 2.4]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Data Architecture / Architectural Boundaries]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Cross-Cutting Concerns Identified (tolerancia a fallos)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Complete Project Directory Structure (pipeline/buffer.py)]
- [Source: project-planning-artifacts/prd-GTI_Router-2026-01-22.md#FR5] (buffer local ≥4h)

### Notas de contexto del proyecto
- `psutil` ya fue fijado como dependencia en la Story 1.1; `shutil` es stdlib. No agregar dependencias nuevas.

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
