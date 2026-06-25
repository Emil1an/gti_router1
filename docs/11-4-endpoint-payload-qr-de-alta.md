# Story 11.4: Endpoint + payload del QR de alta

Status: done

## Story

As a **consola local del Router**,
I want **un endpoint que entregue el dato de alta del equipo**,
so that **la UI pueda dibujar el QR que el usuario escanea desde gtisatelites.com para reclamar el equipo**.

## Acceptance Criteria

1. La mini-API (Story 11.1) expone `GET /api/qr` que devuelve el **payload de alta** (el `claim_token` / serial del equipo) desde la config/registro.
2. El payload es lo mínimo necesario para el claim; **no** expone secretos de servicio (service_role, credenciales).
3. Si el equipo aún no está registrado (sin `router_id`), responde con el dato disponible (token) y un estado claro.

## Tasks / Subtasks

- [ ] **Task 1: Endpoint** (AC: #1, #3)
  - [ ] `GET /api/qr` → `{ claim_token, serial_number, router_id?, status }`
- [ ] **Task 2: Fuente del dato** (AC: #1)
  - [ ] Leer `claim_token`/serial de la config (sembrado en provisioning) y `router_id` de `AppState` si ya registró
- [ ] **Task 3: Seguridad** (AC: #2)
  - [ ] Filtrar cualquier credencial; solo el dato de claim
- [ ] **Task 4: Tests** (AC: all)

## Dev Notes

- El **`claim_token`** lo siembra el provisioning de taller (ver decisión de onboarding) y se imprime/usa como QR.
- Lo consume la pantalla "Alta / QR" (Story 11.9), que **dibuja** el QR. El **escaneo** ocurre en gtisatelites.com (Story 7.2 / RPC `claim_device`).

## References

- [Source: epic-11-consola-local-router.md#Story 11.4]
- [Source: bmad/docs/GTI-Decision-Onboarding-Provisioning.md]
- [Source: gti-router/stories/0-8-rpc-claim-device-liveness-bloqueo.md]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code)

### Completion Notes List

- `GET /api/qr` returns `{claim_token, serial_number, router_id?, status}`.
  `claim_token` sourced from new optional `device.claim_token` (seeded by
  provisioning via `${ROUTER_CLAIM_TOKEN}`), falling back to `serial_number`.
  `status` ∈ {unregistered, registered, claimed}. No service secrets exposed.

### File List

- `src/web/local_api.py` (/api/qr endpoint)
- `src/config/schema.py` (device.claim_token)
- `config/router.yaml.example` (claim_token + console docs)
