# Story 11.8: Pantalla "Cámaras / En vivo"

Status: done

## Story

As a **técnico en sitio**,
I want **ver las cámaras del Router (preview en vivo o último frame)**,
so that **confirme que cada cámara está capturando y bien encuadrada antes de irme**.

## Acceptance Criteria

1. Grid de cámaras del equipo; por cada una, **preview HLS** (`hls.js`) tomado de `/hls/{camera_id}/playlist.m3u8` (Story 11.3).
2. **Fallback** al último frame (`/api/cameras/{id}/last_frame.jpg`, Story 11.2) si el stream no está disponible.
3. Reusa/adapta `LastFrameViewer` del repo base.
4. Funciona en el navegador del kiosko (táctil); maneja el caso "sin imagen aún" con placeholder.

## Tasks / Subtasks

- [ ] **Task 1: Grid de cámaras** (AC: #1)
  - [ ] Consumir `getCameras()`; montar un reproductor `hls.js` por cámara
- [ ] **Task 2: Fallback a last-frame** (AC: #2, #4)
- [ ] **Task 3: Integrar `LastFrameViewer`** (AC: #3)

## Dev Notes

- Depende de 11.1, 11.2, 11.3, 11.5.
- Latencia HLS de varios segundos — aceptable para verificar encuadre (no PTZ fino).

## References

- [Source: epic-11-consola-local-router.md#Story 11.8]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code)

### Completion Notes List

- `CameraTile` mounts an `hls.js` player on `/hls/{id}/playlist.m3u8`, with a
  fatal-error fallback to `/api/cameras/{id}/last_frame.jpg` (cache-busted,
  refreshed every 5 s). Native HLS path for Safari/iOS. Grid in `app/camaras`.
- Files live in the frontend repo `Emil1an/GTI_satelites` (not this repo).

### File List

- (GTI_satelites) `components/CameraTile.tsx`
- (GTI_satelites) `app/camaras/page.tsx`
