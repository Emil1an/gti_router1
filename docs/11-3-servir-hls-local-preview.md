# Story 11.3: Servir HLS local para preview en vivo

Status: done

## Story

As a **consola local del Router**,
I want **servir el HLS que el Router genera localmente**,
so that **el técnico vea un preview casi en vivo de cada cámara en la pantalla táctil**.

## Acceptance Criteria

1. La mini-API (Story 11.1) sirve `GET /hls/{camera_id}/playlist.m3u8` y sus segmentos `.ts` desde `${hls.output_dir}` del Router.
2. El preview funciona en el navegador local (kiosko) con un reproductor HLS (la UI usará `hls.js`, Story 11.8).
3. Se documenta la latencia esperada (~varios segundos, por el tamaño de segmento) — sirve para verificar encuadre, NO para PTZ de precisión fina.
4. Solo sirve cámaras del propio Router (no expone rutas arbitrarias del filesystem).

## Tasks / Subtasks

- [ ] **Task 1: Ruta de archivos estáticos HLS** (AC: #1, #4)
  - [ ] Montar `${hls.output_dir}` como ruta servida bajo `/hls/`, restringida a directorios de cámara válidos
- [ ] **Task 2: Headers correctos** (AC: #2)
  - [ ] `application/vnd.apple.mpegurl` para `.m3u8`, `video/mp2t` para `.ts`; CORS local si aplica
- [ ] **Task 3: Tests** (AC: #1, #4)
  - [ ] Playlist existente → 200; ruta fuera del dir de cámaras → 403/404

## Dev Notes

- El pipeline ya escribe HLS en disco (`ffmpeg_hls.py` → `playlist.m3u8` + `segment_N.ts`); aquí solo se **sirve**.
- Lo consume la pantalla de cámaras (Story 11.8). Si se quiere baja latencia real (WebRTC/MSE) es otro alcance.

## References

- [Source: epic-11-consola-local-router.md#Story 11.3]
- [Source: GTIservices/Router/gti_router1/src/pipeline/ffmpeg_hls.py]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code)

### Completion Notes List

- `/hls` mounted via Starlette `StaticFiles` rooted at `hls.output_dir`; cannot
  escape the directory (no arbitrary FS paths). `.m3u8`/`.ts` get correct
  content-types from the static handler. Each camera exposes
  `hls_url=/hls/{id}/playlist.m3u8` in `/api/cameras`. Latency ~segment-size
  seconds (encuadre, no PTZ fino) — consumed by `hls.js` in Story 11.8.

### File List

- `src/web/local_api.py` (/hls static mount + hls_url per camera)
