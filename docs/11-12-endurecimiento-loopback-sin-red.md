# Story 11.12: Endurecimiento (loopback, sin red)

Status: done

## Story

As a **responsable de seguridad/operación**,
I want **que la consola local sea segura y funcione sin internet**,
so that **no exponga datos del equipo en la red ni se caiga si no hay conexión**.

## Acceptance Criteria

1. La mini-API queda **solo en `127.0.0.1`** (no accesible desde la LAN).
2. La consola **funciona sin internet** (local-first): nada del path crítico depende de Supabase/Mapbox/CDNs externas.
3. No hay tokens de servicio ni credenciales expuestas en el cliente.
4. Prueba con **red desconectada**: estado, cámaras (last-frame/HLS local) y QR se ven correctamente.

## Tasks / Subtasks

- [ ] **Task 1: Verificar binding loopback** (AC: #1)
- [ ] **Task 2: Quitar dependencias externas del path crítico** (AC: #2, #3)
  - [ ] Sin Mapbox/CDN; assets locales; sin llamadas a la nube en la consola
- [ ] **Task 3: Prueba sin red** (AC: #4)

## Dev Notes

- Cierre de la Épica 11: garantiza que la consola es local-first y no filtra nada.
- Depende de todo lo anterior.

## References

- [Source: epic-11-consola-local-router.md#Story 11.12]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code)

### Completion Notes List

- Two-layer hardening: (1) API binds `127.0.0.1` only (enforced in
  `web/server.py` from `console.host`); (2) nftables drop-in drops tcp/udp 8770
  on any non-`lo` interface (defense-in-depth). `verify-loopback.sh` asserts the
  socket is loopback-bound, loopback works, and the LAN IP is refused.
  Local-first front-end (no Supabase/Mapbox/CDN in the critical path; no service
  secrets in the client — `/api/qr` exposes only the claim token).

### File List

- `packaging/nftables/gti-router-loopback.nft`
- `scripts/verify-loopback.sh`
- `src/web/server.py` (loopback binding)
- `scripts/setup-pi.sh` (apply nftables + verify)
