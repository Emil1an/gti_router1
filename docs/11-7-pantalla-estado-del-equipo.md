# Story 11.7: Pantalla "Estado del equipo"

Status: done

## Story

As a **técnico en sitio**,
I want **ver el estado del Router en la pantalla táctil**,
so that **confirme de un vistazo que el equipo está sano y operando**.

## Acceptance Criteria

1. Muestra: CPU, RAM, temperatura, disco, uptime; estado de **conectividad** (Supabase/S3/RTSP) y contadores de la **cola de upload** (pendientes/subidos/error).
2. Muestra el **estado por cámara** (conectada/streaming, último segmento, error) y GPS si aplica.
3. Se **actualiza periódicamente** (polling a `/api/health` y `/api/cameras`, cada pocos segundos).
4. **Diseño táctil:** targets grandes (≥44px), sin hovers, legible a distancia en la pantalla del equipo.

## Tasks / Subtasks

- [ ] **Task 1: Vista de salud** (AC: #1, #3)
  - [ ] Reusar `DeviceCard`/UI kit; consumir `getHealth()` con refresco
- [ ] **Task 2: Estado por cámara** (AC: #2)
  - [ ] Lista con badge de estado por cámara
- [ ] **Task 3: Ergonomía táctil** (AC: #4)

## Dev Notes

- Depende de 11.1 (API) y 11.5 (capa de datos).
- Reusa componentes/tema del repo base.

## References

- [Source: epic-11-consola-local-router.md#Story 11.7]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code)

### Completion Notes List

- `app/estado/page.tsx` shows CPU/RAM/disk/temp + connectivity (RTSP/S3/Supabase)
  + upload-queue counters + per-camera status badges, polling `/api/health` and
  `/api/cameras` every 3 s via `usePolling`. Touch-first: ≥96px cards, no hovers.
- Files live in the frontend repo `Emil1an/GTI_satelites` (not this repo).

### File List

- (GTI_satelites) `app/estado/page.tsx`
- (GTI_satelites) `hooks/usePolling.ts`
