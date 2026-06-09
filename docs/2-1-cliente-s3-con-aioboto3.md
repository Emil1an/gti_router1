# Story 2.1: Cliente S3 con aioboto3

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **sistema GTI Router**,
I want **subir segmentos a S3 de forma asíncrona con `aioboto3`**,
so that **el upload no bloquee la captura y el video llegue a la nube con baja latencia (NFR4)**.

## Acceptance Criteria

1. **Cliente async:** `src/upload/s3_client.py` define `S3Uploader` que crea el cliente S3 vía `aioboto3.Session().client("s3")` (no bloquea el event loop) y lo gestiona como recurso async (apertura/cierre en `async start()`/`async stop()` o context manager). Credenciales y región se leen **solo** de variables de entorno (vía `get_config()` / env), nunca del YAML (NFR9).
2. **Upload de segmento:** `async upload_segment(camera_id, segment_path) -> str` sube el `.ts` al bucket configurado usando el prefijo S3 `{user_id}/{router_id}/{camera_id}/segment_%05d.ts` (preservando el nombre `segment_NNNNN.ts` generado por el pipeline) y retorna la URL/clave S3 resultante.
3. **Multipart >5MB:** los archivos ≤5MB se suben con `put_object`; los >5MB usan multipart upload (`upload_file`/`upload_fileobj` con `TransferConfig` o multipart manual) con `multipart_threshold=5MB`. El upload es resumible/abortable (un fallo a mitad no deja un multipart colgado: se aborta).
4. **Content-Type correcto:** los `.ts` se suben con `Content-Type: video/mp2t`; el `playlist.m3u8` con `Content-Type: application/vnd.apple.mpegurl`. Existe `async upload_playlist(camera_id, playlist_path)`.
5. **TLS:** todo el tráfico a S3 va por HTTPS/TLS 1.2+ (default de aioboto3; no se desactiva `use_ssl`) (NFR10).
6. **Errores tipados:** los fallos lanzan excepciones tipadas del dominio (`S3UploadError` y subtipos para transitorio vs permanente) — **prohibido** `raise Exception(...)` genérico. Esta story NO implementa el retry (lo añade la 2.3); solo distingue/propaga el error de forma que 2.3 pueda decidir reintento.
7. **IAM mínimo:** la documentación (docstring/README) indica que la policy IAM debe estar scoped al prefijo del bucket (permisos mínimos).
8. **Tests con `moto`:** `tests/upload/test_s3_client.py` valida con `moto` (mock S3, sin AWS real): upload de un `.ts` pequeño (put_object), upload de un archivo >5MB (multipart), prefijo/clave correcta, Content-Type correcto de `.ts` y `.m3u8`, y propagación de error tipado ante fallo. No se sube hardware/AWS real en CI.

## Tasks / Subtasks

- [ ] **Task 1: Esqueleto del `S3Uploader`** (AC: #1, #5)
  - [ ] `src/upload/s3_client.py`: clase `S3Uploader` con `async start()`/`async stop()` (o `__aenter__`/`__aexit__`) que abre/cierra el cliente `aioboto3`
  - [ ] Leer credenciales/region/bucket vía `get_config()` (secretos desde env; nunca YAML) — dejar el hueco si 1.2 aún no expone el bloque `aws`, usando env vars directas documentadas
  - [ ] Confirmar HTTPS/TLS por default (no tocar `use_ssl`)
- [ ] **Task 2: Construcción de la clave S3** (AC: #2)
  - [ ] Helper que arma el prefijo `{user_id}/{router_id}/{camera_id}/` desde config + `camera_id` y preserva el basename `segment_NNNNN.ts`
  - [ ] `async upload_segment(camera_id, segment_path) -> str` que retorna la clave/URL S3
- [ ] **Task 3: Multipart >5MB y Content-Type** (AC: #3, #4)
  - [ ] Usar `TransferConfig(multipart_threshold=5*1024*1024)` o lógica equivalente; abortar multipart colgado ante fallo
  - [ ] `ExtraArgs={"ContentType": "video/mp2t"}` para `.ts`; `async upload_playlist()` con `application/vnd.apple.mpegurl`
- [ ] **Task 4: Errores tipados** (AC: #6)
  - [ ] En `src/utils/errors.py` (de la 1.1) asegurar/añadir `S3UploadError` (+ distinción transitorio/permanente, p. ej. por status 5xx/timeout vs 403/404) — NO reimplementar `@with_retry` aquí
- [ ] **Task 5: Tests con moto** (AC: #8)
  - [ ] `tests/upload/test_s3_client.py` con `@mock_aws`/`moto`: put_object pequeño, multipart >5MB, clave/prefijo, Content-Type `.ts` y `.m3u8`, error tipado
  - [ ] Generar el archivo >5MB en el test de forma temporal (no versionar binarios grandes)
- [ ] **Task 6: Documentación** (AC: #7)
  - [ ] Docstring de módulo + nota en README sobre IAM scoped al prefijo y secretos solo en env

## Dev Notes

**Esta es la primera story de la Épica 2 (Upload a S3 y Buffer, `[ROUTER]`). Define el contrato del cliente S3 que reutilizan 2.2–2.6. NO reimplementa `@with_retry` (lo añade 2.3) ni la cola (2.2): solo sube un archivo dado y reporta el resultado/error.**

### Decisiones de arquitectura aplicables
- **aioboto3 ~=15:** cliente S3 async para no bloquear el event loop; multipart resumible vía HTTPS. [Source: architecture-GTI_Router.md#API & Communication Patterns]
- **Prefijo S3 por cámara:** `{user_id}/{router_id}/{camera_id}/segment_%05d.ts` — un prefijo por cámara (FR3). [Source: architecture-GTI_Router.md#Naming Patterns / Data Architecture]
- **Multipart umbral 5MB:** archivos grandes en multipart; pequeños en put_object. [Source: epics.md#Story 2.1]
- **Secretos solo en env (NFR9) + TLS 1.2+ (NFR10):** credenciales nunca en YAML; IAM con permisos mínimos scoped al prefijo del bucket. [Source: architecture-GTI_Router.md#Authentication & Security]
- **Frontera cloud única:** `upload/` (S3) es de los únicos módulos que hablan con el exterior; todo encapsulado y degradable; el resto del código nunca llama a boto3 directo. [Source: architecture-GTI_Router.md#Architectural Boundaries (Frontera cloud)]

### Patrones obligatorios (de la 1.1 / arquitectura)
- **Retry:** toda operación de red usa el único `@with_retry` de `src/utils/retry.py`. Esta story **no** lo aplica todavía (2.3 envuelve `upload_segment`); pero debe lanzar errores que 2.3 pueda clasificar (transitorio vs permanente). [Source: architecture-GTI_Router.md#Process Patterns]
- **Errores tipados:** `S3UploadError` por dominio; **prohibido** `raise Exception("...")` genérico. Errores permanentes (403/404) se distinguen de transitorios (timeout/5xx). [Source: architecture-GTI_Router.md#Format Patterns / API & Communication Patterns]
- **Logging:** journald con `camera_id` en contexto por cámara; métricas con sufijo de unidad. [Source: architecture-GTI_Router.md#Process Patterns / Naming Patterns]
- **Config:** acceso solo vía `get_config()`; prohibido leer `os.environ`/YAML fuera de `src/config/` — salvo los secretos AWS que viven en env por diseño (NFR9). [Source: architecture-GTI_Router.md#Process Patterns]
- **Servicios:** una clase de servicio por módulo; exponen `async start()`/`async stop()`. [Source: architecture-GTI_Router.md#Structure Patterns / Naming Patterns]

### Anti-patrones a evitar
- ❌ Llamar a boto3 síncrono o bloquear el event loop · ❌ `raise Exception` genérico · ❌ poner credenciales AWS en el YAML · ❌ reimplementar retry aquí · ❌ desactivar TLS. [Source: architecture-GTI_Router.md#Enforcement Guidelines]

### Testing standards
- `pytest` + `pytest-asyncio`; `moto` para mock de S3 (introducido como dep dev en 1.1). CI corre en x86 sin AWS real. [Source: architecture-GTI_Router.md#Development Experience / CI; epics.md#Story 2.1]
- Marcar tests async; crear el bucket mock en un fixture; generar el archivo >5MB de forma temporal en el test.

### Project Structure Notes
Archivo principal de esta story (el paquete `upload/` ya existe vacío desde la 1.1):
```
src/upload/
├── __init__.py
├── s3_client.py     # S3Uploader (aioboto3, multipart >5MB, Content-Type, prefijo por cámara)  ← ESTA STORY
└── queue.py         # UploadQueue → Story 2.2
tests/upload/
└── test_s3_client.py   ← ESTA STORY
```
Variance: `queue.py` y la persistencia en SQLite (`storage/db.py`) los crean 2.2; el retry envolvente lo añade 2.3; el wiring pipeline→cola es la 2.6 — no en esta story. [Source: architecture-GTI_Router.md#Complete Project Directory Structure]

### References
- [Source: _bmad-output/gti-router/epics.md#Epic 2 / Story 2.1]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Data Architecture / API & Communication Patterns]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Authentication & Security]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Naming Patterns (S3 keys)]
- [Source: project-planning-artifacts/architecture-GTI_Router.md#Architectural Boundaries (Frontera cloud)]
- [Source: project-planning-artifacts/prd-GTI_Router-2026-01-22.md#FR3] (upload a S3 con prefijo por cámara)

### Notas de contexto del proyecto
- `aioboto3~=15.0` y `moto` ya fueron fijados como dependencias en la Story 1.1 — no agregar versiones nuevas.
- La Épica 0 (BD) provee el contexto de identidad (`user_id`, `router_id`, `camera_id`) pero esta story los toma de la config; no toca Supabase.

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
