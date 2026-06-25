# Story 11.6: Limpiar la UI (quitar flota/nube/login/scan)

Status: done

## Story

As a **desarrollador de la consola local**,
I want **quitar de la base de UI todo lo que es de la plataforma de flota en la nube**,
so that **quede una consola ligera de un solo equipo, sin piezas que no aplican localmente**.

## Acceptance Criteria

1. Se **eliminan** del repo base (consola): `/login`, `/register`, `/map` (mapa de flota), `/devices/claim`, `supabaseClient`, `useAuth`, `usePermissions`, `QrScanner` (escaneo), `claim_device.sql`.
2. Se **conservan**: el UI kit (`components/ui/*`), tema día/noche, layout, `DeviceCard`, `LastFrameViewer`, `geo.ts`, `utils.ts`.
3. La app compila y corre sin esas piezas (sin imports rotos).

## Tasks / Subtasks

- [ ] **Task 1: Borrar módulos de flota/nube** (AC: #1)
- [ ] **Task 2: Conservar y verificar UI kit/tema/componentes** (AC: #2)
- [ ] **Task 3: Compilar y limpiar imports** (AC: #3)

## Dev Notes

- El **escaneo** de QR (`QrScanner`) NO va en la consola local (la consola **muestra** el QR, Story 11.9; el escaneo vive en gtisatelites.com).
- Puede hacerse en paralelo con 11.5.

## References

- [Source: epic-11-consola-local-router.md#Story 11.6]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code)

### Completion Notes List

- Removed fleet/cloud/auth/scan modules (login, register, map, devices/claim,
  supabaseClient, useAuth, usePermissions, QrScanner, *.sql). Kept UI kit
  (`components/ui/*`), theme, layout, `DeviceCard`, `LastFrameViewer`, `geo.ts`,
  `utils.ts`. App compiles clean (verified via `output:'export'` build).
- Files live in the frontend repo `Emil1an/GTI_satelites` (not this repo).

### File List

- (GTI_satelites) deletions per AC#1; UI kit/theme retained per AC#2
