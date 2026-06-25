# Story 11.2: Retener last-frame local + endpoint para servirlo

Status: done

## Story

As a **consola local del Router**,
I want **tener disponible localmente la última imagen de cada cámara**,
so that **la UI muestre el "último frame" aunque la subida a la nube ya haya ocurrido**.

## Acceptance Criteria

1. `snapshot.py` deja de **borrar** la última copia tras subirla a S3: conserva en disco el `last_frame.jpg` más reciente por cámara (p.ej. en `${hls.output_dir}/{camera_id}/`).
2. La mini-API (Story 11.1) expone `GET /api/cameras/{id}/last_frame.jpg` que sirve esa imagen.
3. Si no hay frame aún para una cámara, responde 404 con mensaje claro (la UI muestra placeholder).
4. No crece sin control: se conserva solo el último (se sobrescribe), no histórico.

## Tasks / Subtasks

- [ ] **Task 1: Conservar copia local** (AC: #1, #4)
  - [ ] Ajustar `snapshot.py` para retener/sobrescribir `last_frame.jpg` por cámara
- [ ] **Task 2: Endpoint** (AC: #2, #3)
  - [ ] `GET /api/cameras/{id}/last_frame.jpg` (Content-Type image/jpeg) + 404 si no existe
- [ ] **Task 3: Tests** (AC: all)
  - [ ] Frame presente → 200; ausente → 404

## Dev Notes

- Hoy `snapshot.py` sube el JPEG a S3 y lo borra (Story 6.4). Aquí solo se conserva la última copia local para servirla en la consola.
- Alternativa válida: generar el frame on-demand desde el último segmento `.ts` del HLS si no se quiere tocar `snapshot.py`.
- Lo consume la pantalla de cámaras (Story 11.8).

## References

- [Source: epic-11-consola-local-router.md#Story 11.2]
- [Source: GTIservices/Router/gti_router1/src/pipeline/snapshot.py]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Code)

### Completion Notes List

- `snapshot.py` now retains `last_frame.jpg` per camera (overwritten each cycle,
  only the latest kept — AC#4). The S3 upload runs from a throwaway temp copy so
  the retained file is never deleted. Frame is written even in degraded/no-cloud
  mode (extraction happens before upload).
- API serves `GET /api/cameras/{id}/last_frame.jpg` (image/jpeg, no-store);
  validates the camera id against config (no arbitrary FS access); 404 when no
  frame exists yet.

### File List

- `src/pipeline/snapshot.py` (retain last_frame.jpg)
- `src/web/local_api.py` (last_frame endpoint)
- `tests/web/test_local_api.py`
