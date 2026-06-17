# Story 6.4: Contrato de exposición sin detección

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **arquitecto del ecosistema GTI**,
I want **que el Router marque sus imágenes (last-frame, feed DJI) con un origen/versión de contrato que indique claramente "SIN detección"**,
so that **GTI Satélites las diferencie de los frames CON detección producidos por el Gateway, y quede garantizado que el Router nunca ejecuta modelos**.

## Acceptance Criteria

1. **Marca de origen "sin detección":** toda imagen que el Router publica para Satélites (last-frame de la Story 6.3, feed DJI de la Story 5.3) se marca con un **origen explícito = Router** (no Gateway) y una **versión de contrato** (p. ej. `contract_version`), de forma que el consumidor sepa inequívocamente que **NO contiene detección**.
2. **Punto único de marcado:** el marcado del contrato se aplica de forma consistente y centralizada (no duplicado ad-hoc por cada productor), de modo que last-frame y futuros productores del Router lo hereden. Puede materializarse como metadato del registro en `cameras` y/o como atributo del payload publicado, según lo que Satélites consume.
3. **Diferenciación inequívoca:** el contrato deja claro que el origen `router` ⇒ **sin detección** y el origen `gateway` ⇒ **con detección**, sin ambigüedad ni solapamiento; el last-frame del Router nunca se confunde con un frame analizado.
4. **El Router NUNCA ejecuta modelos:** se documenta y se garantiza (sin código de inferencia en el repo) que **toda inferencia/detección es del Gateway**; el Router solo provee vista cruda. No existe dependencia ni invocación de modelos de detección en el Router.
5. **Versionado del contrato:** el contrato lleva una versión explícita (`contract_version`) para permitir evolución futura sin romper a Satélites; se documenta el significado del campo de origen y de la versión.
6. **Coherencia con datos existentes:** el marcado es compatible con las columnas reales (`cameras.last_frame_url`, `cameras.last_frame_at`) y no introduce semántica de detección sobre ellas.
7. **Tests:** tests verifican que las imágenes publicadas por el Router llevan origen `router` + `contract_version` y NO llevan campos/semántica de detección; y un test/guardia documental confirma la ausencia de ejecución de modelos en el Router.

## Tasks / Subtasks

- [ ] **Task 1: Definir el contrato de exposición** (AC: #1, #3, #5)
  - [ ] Definir el origen (`source = 'router'`) y `contract_version` como contrato versionado, documentado (origen ⇒ sin detección)
  - [ ] Ubicarlo en un punto reutilizable (utils/contrato compartido) para que todos los productores del Router lo hereden
- [ ] **Task 2: Aplicar el marcado en los productores del Router** (AC: #1, #2, #6)
  - [ ] Aplicar el origen/versión al last-frame (Story 6.3) y al feed DJI (Story 5.3) de forma centralizada
  - [ ] Asegurar compatibilidad con `cameras.last_frame_url`/`last_frame_at` sin añadir semántica de detección
- [ ] **Task 3: Garantía de "sin modelos"** (AC: #4)
  - [ ] Documentar que el Router no ejecuta inferencia; verificar ausencia de dependencias/código de detección en el repo
- [ ] **Task 4: Tests** (AC: #7)
  - [ ] Tests: imágenes publicadas con `source='router'` + `contract_version`, sin campos de detección; guardia de ausencia de modelos

## Dev Notes

**Esta story formaliza el INVARIANTE de contrato cross-sistema de la Épica 6 (`[ROUTER]`): el Router produce SIN detección; el Gateway produce CON detección; Satélites los diferencia. Es la pieza AR8.**

### Invariante de contrato (confirmado por el usuario — núcleo de esta story)
> **El Router solo produce `last-frame` SIN detección** (vista cruda, autónoma, sin depender del Gateway). Toda detección (fuego/humo) es responsabilidad exclusiva del Gateway. Satélites distingue y muestra ambos orígenes por separado. [Source: architecture-GTI_Router.md#Invariante de contrato confirmado por el usuario]

### Contrato cross-sistema (de la arquitectura)
- **Contrato cross-sistema versionado:** Router produce `last-frame` **sin detección**; Gateway produce frames **con detección**; ambos coexisten diferenciados en Satélites. [Source: architecture-GTI_Router.md#API & Communication Patterns]
- AR8: Router produce last-frame **SIN** detección; Gateway produce frames **CON** detección; Satélites los diferencia. [Source: epics.md#AR8]
- Gap analysis (menor) sugiere explícitamente: **versionado del contrato cross-sistema con un campo `contract_version`**. Esta story lo materializa. [Source: architecture-GTI_Router.md#Gap Analysis Results]

### Dependencia de la Épica 0 (BD) — ANÓTALO
- El marcado se apoya en columnas que ya existen / se crean en la Épica 0: **`cameras.last_frame_url`** (Story 0.5) y **`cameras.last_frame_at`** (preexistente). Esta story NO crea ni altera esquema; solo define semántica de origen/versión sobre lo publicado. [Source: gtisatelites-brownfield-database.md#8 / #10]

### Patrones OBLIGATORIOS (de la Story 1.1 / arquitectura)
- **Formato:** payloads JSON `snake_case`, tiempo UTC ISO-8601 `Z`. El campo de origen/versión sigue este formato. [Source: architecture-GTI_Router.md#Format Patterns]
- **Errores:** excepciones tipadas; **prohibido** `raise Exception("...")`. [Source: architecture-GTI_Router.md#Format Patterns]
- **Comunicación:** contrato versionado entre sistemas; estructura estable consumible por Satélites. [Source: architecture-GTI_Router.md#Communication Patterns]

### Anti-patrones a evitar
- ❌ Ejecutar/importar modelos de detección en el Router (toda inferencia es del Gateway) · ❌ marcar el last-frame con semántica de detección · ❌ duplicar el marcado de contrato ad-hoc por productor · ❌ `raise Exception("...")` genérico. [Source: architecture-GTI_Router.md#Enforcement Guidelines + Invariante de contrato]

### Relación con otras stories
- **Productores del marcado:** Story 6.3 (last-frame autónomo) y Story 5.3 (feed DJI vista en vivo, sin detección). [Source: epics.md#Story 6.3 / #Story 5.3]
- **Consumidor del marcado:** Épica 7 (Satélites) — Story 7.3 (visor last-frame diferenciado del frame con detección del Gateway). El last-frame se lee como "vista sin analizar" (rojo/naranja reservados a alerta/detección). [Source: epics.md#Story 7.3 / #UX-DR2]

### Testing standards
- `pytest`; verificación del payload/metadato de origen y versión; guardia documental/estática de ausencia de modelos. Sin hardware. [Source: architecture-GTI_Router.md#Testing Framework / CI]

### Project Structure Notes
- El contrato vive como utilidad/constante compartida (candidato: `src/utils/` para la definición de `source`/`contract_version`), aplicada por `pipeline/snapshot.py` y la fuente del feed DJI. No requiere nuevos módulos de negocio. [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 6 / Story 6.4]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Invariante de contrato confirmado por el usuario]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#API & Communication Patterns (contrato cross-sistema versionado)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Gap Analysis Results (contract_version)]
- [Source: prd-GTI_Router-2026-01-22.md#FR24] (exponer vista sin detección, diferenciada de la del Gateway)

### Notas de contexto del proyecto
- FR24 = exponer en Satélites la vista en vivo / last-frame **sin detecciones**, diferenciada de las imágenes con detección del Gateway. En la Épica 6 el Router **produce** sin detección; en la Épica 7 Satélites lo **muestra** diferenciado. [Source: epics.md#FR Coverage Map (FR24: E6 + E7)]
- Depende de la **Épica 0** solo para las columnas de last-frame (no toca esquema). El invariante "Router sin modelos / Gateway con detección" es la razón de ser de esta story.

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
