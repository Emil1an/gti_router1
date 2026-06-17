# Story 5.5: Board-detection y portabilidad RPi4/RPi5

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **desarrollador del equipo GTI**,
I want **que el mismo código detecte el board (RPi4/RPi5) y elija el pipeline adecuado**,
so that **un solo binario corra tanto en Base (RPi4) como en Pro (RPi5) sin ramas manuales por dispositivo**.

## Acceptance Criteria

1. **Detección de board:** `src/platform/board.py` lee `/proc/device-tree/model` al arranque e identifica el board (RPi4, RPi5, y `unknown`/no soportado como fallback explícito). Expone el resultado mediante un tipo claro (enum/dataclass, p. ej. `Board.RPI4` / `Board.RPI5`) y una función de acceso (`detect_board()` o equivalente).
2. **Lectura robusta:** la lectura de `/proc/device-tree/model` tolera el byte nulo final y variaciones del string (matching por substring case-insensitive, p. ej. "Raspberry Pi 4", "Raspberry Pi 5"); si el archivo no existe o el modelo no se reconoce, retorna `unknown` con WARNING (no crashea), permitiendo desarrollo en x86.
3. **`EncoderSelector` consume el board:** el `EncoderSelector` (Story 5.2) usa el board detectado para elegir encoder y límites: **RPi4 → `h264_v4l2m2m` (HW)**, **RPi5 → `libx264` (SW)**; nunca HEVC-SW. La decisión de codec/board vive **solo** aquí (`platform/board.py` + `encoder.py`), no dispersa por el código.
4. **Límites por board:** el board determina los topes por hardware (streams simultáneos máx, NFR12: RPi4 2 IP +1 capturadora; RPi5 3 IP +1 capturadora) que la Story 5.6 aplica vía `routers.max_cameras`. Esta story expone el board; 5.6 deriva/valida el límite físico.
5. **Sin acoplar al hardware en dev:** en x86/CI (sin `/proc/device-tree/model` de RPi), el board resuelve a `unknown`/inyectable, permitiendo correr `main.py` y los tests sin Raspberry.
6. **Errores tipados:** condiciones inesperadas (permiso, formato corrupto) se reportan con excepción tipada o WARNING degradable (no `Exception` genérico); la detección **nunca** debe tumbar el arranque por sí sola (cae a `unknown`).
7. **Tests que simulan ambos boards:** tests unitarios que mockean el contenido de `/proc/device-tree/model` para RPi4, RPi5, archivo ausente y modelo desconocido, verificando la detección y que el `EncoderSelector` elige el encoder correcto por board. Todo en x86 en CI.

## Tasks / Subtasks

- [ ] **Task 1: Implementar `platform/board.py`** (AC: #1, #2, #6)
  - [ ] Leer `/proc/device-tree/model`, normalizar (quitar `\x00`, lower, substring match)
  - [ ] Tipo `Board` (RPI4/RPI5/UNKNOWN) + `detect_board()`; archivo ausente/modelo desconocido → `UNKNOWN` + WARNING
  - [ ] Cachear el resultado (se lee una vez al arranque)
- [ ] **Task 2: Conectar con `EncoderSelector`** (AC: #3, #4)
  - [ ] `EncoderSelector` (5.2) recibe `Board` y elige encoder/límites (RPi4 HW / RPi5 SW; HEVC-SW prohibido)
  - [ ] Exponer los topes por board (NFR12) para que 5.6 los use como límite físico
- [ ] **Task 3: Portabilidad en dev/CI** (AC: #5)
  - [ ] Garantizar que en x86 (sin /proc RPi) el board resuelve a `UNKNOWN`/inyectable sin romper `main.py`
- [ ] **Task 4: Tests** (AC: #7)
  - [ ] `tests/platform/`: mock de `/proc/device-tree/model` para RPi4, RPi5, ausente, desconocido
  - [ ] Verificar selección de encoder por board vía `EncoderSelector`

## Dev Notes

**`platform/board.py` es la única fuente de verdad del board. Junto con `EncoderSelector`, encapsula toda la portabilidad RPi4/RPi5: el resto del código nunca consulta el modelo de Raspberry directamente. Esta story habilita la 5.2 (encoder) y la 5.6 (límites físicos).**

### Detección de board (de la arquitectura)
- `platform/board.py` → detección RPi4/RPi5 (`/proc/device-tree/model`).
- `EncoderSelector` por **detección de board**: RPi4 → `h264_v4l2m2m` (HW); RPi5 → `libx264` SW; HEVC-SW evitado.
[Source: architecture-GTI_Router.md#Complete Project Directory Structure / Video Source & Encoder Strategy (D1)]

### Portabilidad de hardware como cross-cutting concern
> **Portabilidad de hardware RPi4/RPi5** (abstracción de encoder con board-detection y *fallback*). Código único con detección de board.
[Source: architecture-GTI_Router.md#Cross-Cutting Concerns Identified / Technical Constraints & Dependencies]

### Constraint de hardware (por qué importa el board)
- RPi4 2GB (Base): encoder HW H.264 (`h264_v4l2m2m`). RPi5 (Pro): **SIN** encoder HW H.264 → software (`libx264`). La elección depende del board; HEVC-SW se descarta por CPU.
- NFR12 (límites por hardware/licencia): RPi4 → 2 IP +1 capturadora; RPi5 → 3 IP +1 capturadora; configurable; validar en piloto. El board define el tope físico que aplica la Story 5.6.
[Source: architecture-GTI_Router.md#Technical Constraints & Dependencies / Starter Template Evaluation]
[Source: _bmad-output/gti-router/epics.md#NFR12 / Story 5.6]

### Frontera de codec/board
> El `EncoderSelector` es el **único** punto que toca decisiones de codec/board. `platform/board.py` provee el board; nadie más lee `/proc/device-tree/model`.
[Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera de fuente de video)]

### Relación con otras stories
- **5.2** (`EncoderSelector`) consume el board que esta story expone (esta story puede ir antes o inyectarse en 5.2 para no acoplar el orden).
- **5.6** deriva/valida el límite físico de cámaras a partir del board (NFR12) y lo cruza con `routers.max_cameras`.
[Source: _bmad-output/gti-router/epics.md#Story 5.2 / 5.6]

### Patrones obligatorios
- `snake_case` (módulos/funciones), `PascalCase`/enum para `Board`, `UPPER_SNAKE` para constantes.
- Errores tipados; **prohibido** `Exception` genérico. La detección degrada a `unknown` (no tumba el arranque).
[Source: architecture-GTI_Router.md#Naming Patterns / Format Patterns / Enforcement Guidelines]

### Testing standards
- `pytest`; mockear el contenido de `/proc/device-tree/model` (no requiere hardware). CI en x86. Hardware real = checklist manual en RPi4 y RPi5.
[Source: architecture-GTI_Router.md#Development Experience / CI]

### Anti-patrones a evitar
- ❌ leer `/proc/device-tree/model` fuera de `platform/board.py` · ❌ ramas `if RPi4/RPi5` dispersas por el código · ❌ que una detección fallida tumbe el arranque · ❌ `Exception` genérico.
[Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Project Structure Notes
```
src/platform/
├── __init__.py
└── board.py          # detección RPi4/RPi5 (/proc/device-tree/model)   ← ESTA STORY
src/camera/encoder.py # EncoderSelector consume Board (5.2)
src/licensing.py      # límite por hardware deriva del board (5.6)
tests/platform/       # espeja src/platform
```
[Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 5 / Story 5.5]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Video Source & Encoder Strategy (D1 / RT1)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Cross-Cutting Concerns Identified]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Architectural Boundaries / Technical Constraints & Dependencies]
- [Source: prd-GTI_Router-2026-01-22.md#NFR12] (RPi4: 2 IP +1 capturadora; RPi5: 3 IP +1 capturadora)

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
