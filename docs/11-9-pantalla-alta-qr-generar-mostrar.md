# Story 11.9: Pantalla "Alta / QR" (generar y mostrar el QR)

Status: done

## Story

As a **técnico o cliente en sitio**,
I want **ver en la pantalla del equipo el QR de alta**,
so that **lo escanee desde gtisatelites.com y deje el equipo a su nombre**.

## Acceptance Criteria

1. La pantalla **genera y muestra** el QR a partir del payload de `/api/qr` (Story 11.4), usando una librería de generación (`qrcode.react` o equivalente — **nueva dependencia**, el repo base solo tiene escaneo).
2. Muestra el **serial legible** como respaldo (por si no se puede escanear).
3. Instrucción clara en pantalla: "Escanéalo desde gtisatelites.com para dar de alta este equipo".
4. Si el equipo ya tiene dueño (`router_id` con `user_id`), muestra estado "Ya reclamado" en vez del QR.

## Tasks / Subtasks

- [ ] **Task 1: Dependencia de generación de QR** (AC: #1)
  - [ ] Agregar `qrcode.react`
- [ ] **Task 2: Pantalla de alta** (AC: #1, #2, #3)
  - [ ] Dibujar QR + serial + instrucción
- [ ] **Task 3: Estado reclamado** (AC: #4)

## Dev Notes

- La consola **muestra** el QR; el **escaneo y el claim** ocurren en gtisatelites.com (Story 7.2 → RPC `claim_device`, Story 0.8).
- Depende de 11.4 y 11.5.

## References

- [Source: epic-11-consola-local-router.md#Story 11.9]
- [Source: bmad/docs/GTI-Decision-Onboarding-Provisioning.md]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code)

### Completion Notes List

- `app/alta/page.tsx` draws the QR with `qrcode.react` (QRCodeSVG) from
  `/api/qr`'s `claim_token`, shows the readable serial as backup + the
  "scan from gtisatelites.com" instruction, and renders "Ya reclamado" when
  `status === "claimed"`. `qrcode.react` added as a new dependency.
- Files live in the frontend repo `Emil1an/GTI_satelites` (not this repo).

### File List

- (GTI_satelites) `app/alta/page.tsx`
- (GTI_satelites) `package.json` (qrcode.react dep)
