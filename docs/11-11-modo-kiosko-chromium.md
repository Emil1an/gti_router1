# Story 11.11: Modo kiosko (Chromium táctil)

Status: done

## Story

As a **técnico en sitio**,
I want **que al conectar la pantalla táctil al Router aparezca la consola en pantalla completa**,
so that **la use directo sin teclado/mouse ni configurar nada**.

## Acceptance Criteria

1. El Router arranca **Chromium en modo kiosko** apuntando a `http://localhost:<puerto>` (la consola servida en Story 11.10).
2. **Autoarranque** integrado con el ciclo de vida del equipo (systemd / autostart), junto a `gti-router.service`.
3. Pantalla completa, **sin cursor visible**, sin gestos del navegador, optimizado para **táctil**.
4. Si la consola no está lista al boot, reintenta hasta que responda.

## Tasks / Subtasks

- [ ] **Task 1: Unit/autostart del kiosko** (AC: #1, #2, #4)
  - [ ] Servicio que lanza Chromium `--kiosk` al localhost, con espera/reintento
- [ ] **Task 2: Ergonomía kiosko** (AC: #3)
  - [ ] Ocultar cursor, deshabilitar gestos/atajos, modo táctil
- [ ] **Task 3: Verificación con pantalla HDMI** (AC: all)

## Dev Notes

- Requiere que el Pi tenga entorno gráfico mínimo + Chromium (revisar imagen base del Router).
- Depende de 11.10 (UI servida en localhost).

## References

- [Source: epic-11-consola-local-router.md#Story 11.11]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code)

### Completion Notes List

- `cage` (single-app Wayland kiosk) launches `gti-kiosk.sh`, which waits/retries
  on `/api/health` until ready (AC#4) then execs Chromium `--kiosk --app` with
  gestures/translate/pinch disabled, touch enabled, ephemeral `/tmp` profile,
  cursor hidden. `gti-kiosk.service` runs as user `kiosk` on VT1, `Restart=always`,
  starts after `gti-router`. Desktop-image alternative (labwc/wayfire autostart)
  documented. Provisioned by `setup-pi.sh`.

### File List

- `scripts/kiosk/gti-kiosk.sh`
- `systemd/gti-kiosk.service`
- `scripts/setup-pi.sh` (kiosk user + enable)
