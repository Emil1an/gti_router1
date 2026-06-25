# Story 11.5: Adaptar la capa de datos de la UI a la API local

Status: done

## Story

As a **desarrollador de la consola local**,
I want **que la UI lea sus datos de la API local del Router (no de Supabase)**,
so that **la consola funcione local-first, sin internet, mostrando el estado real del equipo**.

## Acceptance Criteria

1. Se reescribe la capa de servicio (`deviceService.ts`, tomada del repo del practicante) para que consuma la **API local** (Story 11.1) en vez de Supabase.
2. Se **conservan los tipos** (`types/devices.ts`), recortados a **un solo equipo** (no flota multi-dispositivo).
3. Mientras la API local no exista, hay un **mock** (usar `demoData`) para poder avanzar la UI.
4. Sin dependencias de Supabase/Mapbox en este módulo.

## Tasks / Subtasks

- [ ] **Task 1: Reescribir `deviceService`** (AC: #1, #2)
  - [ ] Funciones: `getIdentity()`, `getHealth()`, `getCameras()`, `getLastFrameUrl(id)`, `getQrPayload()` → fetch a la API local
- [ ] **Task 2: Tipos** (AC: #2)
  - [ ] Recortar `types/devices.ts` a un equipo; quitar tipos de flota/mapa
- [ ] **Task 3: Mock** (AC: #3)
  - [ ] Adaptar `demoData` como respuesta simulada de la API local
- [ ] **Task 4: Limpieza de deps** (AC: #4)

## Dev Notes

- Base de UI: repo del practicante `github.com/Emil1an/GTI_satelites` (Next.js). Su `deviceService` hoy habla con Supabase; aquí se reapunta a la API local.
- Depende de Story 11.1. Lo consumen 11.7/11.8/11.9.

## References

- [Source: epic-11-consola-local-router.md#Story 11.5]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code)

### Completion Notes List

- `deviceService.ts` rewritten to fetch the local API (`API_BASE` = same-origin
  in production, `127.0.0.1:8770` under `next dev`): `getIdentity/getHealth/
  getCameras/getQrPayload/getLastFrameUrl/getHlsUrl`. Types trimmed to a single
  device in `types/devices.ts` (no fleet/map). No Supabase/Mapbox deps.
- Files live in the frontend repo `Emil1an/GTI_satelites` (not this repo).

### File List

- (GTI_satelites) `lib/deviceService.ts`
- (GTI_satelites) `types/devices.ts`
- (GTI_satelites) `hooks/usePolling.ts`
