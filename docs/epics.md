---
stepsCompleted: [1, 2, 3]
inputDocuments:
  - "project-planning-artifacts/prd-GTI_Router-2026-01-22.md"
  - "project-planning-artifacts/architecture-GTI_Router.md"
  - "project-planning-artifacts/gtisatelites-brownfield-database.md"
  - "project-planning-artifacts/product-brief-GTI_Router-2026-01-21.md (contexto)"
  - "project-planning-artifacts/GTI_Router_Feasibility_Study_Results-2025-01-20.md (contexto)"
  - "docs/GTI-Design-System.md (UX de superficies del Router en Satélites)"
  - "Código desplegado: GTIservices/SatellitesGTI/gti-satelites/gti-satelites (sistema final, mayo 2026)"
scope: "Router edge (Python) + Épica 0 fundación BD + Épica de integración mínima en GTI Satélites"
language: "Spanish"
---

# GTI Router - Epic Breakdown

## Overview

Este documento descompone los requerimientos del PRD de GTI Router (v2.0), las decisiones de arquitectura, y el modelo de datos real verificado (brownfield) en épicas y stories implementables.

**Alcance acordado:** (a) el servicio edge en Python del Router, (b) una **Épica 0 de fundación/relinking de base de datos**, y (c) una **épica de integración mínima en GTI Satélites** (panel de dispositivos básico, visor de last-frame, posición/frustum 3D, control PTZ mínimo) — porque la UI de dispositivos en Satélites hoy **no existe** (la BD ya tiene las tablas, pero ningún componente las consume).

### Convención de clasificación de épicas (dueño + extra)

Cada épica se etiqueta por **track** para saber a qué producto pertenece y qué es opcional/diferible:

| Tag | Dueño | Qué incluye | Bloquea MVP del Router |
|-----|-------|-------------|------------------------|
| `[ROUTER]` | Dispositivo edge (Python) | Captura, upload, resiliencia, PTZ, multicámara, GPS/last-frame | Sí (es el producto) |
| `[FOUNDATION]` | Base de datos (compartida) | Relinking CORE: tipos `text→uuid`, FKs, `router_health`, columnas (`last_frame_url`, orientación, serial UNIQUE), RLS de propiedad + GPS | Sí (sin esto el Router no liga datos) |
| `[SATÉLITES]` | Plataforma web | Panel de dispositivos, visor last-frame, 3D unificado (gateways/routers/drones) + combate al 3D, PTZ UI | Parcial (para *ver* la data del Router) |
| `[EXTRA]` | Comercial / facturación | **Toda la facturación**: `organizations`, `device_subscriptions`, `promotions`, factura consolidada Stripe, cuotas/límites por suscripción, enforcement de cobro | **No** — se termina bien en fases posteriores |

**Regla:** la **facturación jerárquica completa es `[EXTRA]`** (no bloquea que el Router capture, suba y se vea en Satélites). La Épica 0 se **parte en dos**: `[FOUNDATION]` (lo que el Router necesita para funcionar) y `[EXTRA]` (las tablas/lógica de cobro). Los **límites de cámara por hardware** (`max_cameras`) quedan en `[FOUNDATION]`; los **límites por suscripción pagada** (`camera_quota`, cobro) quedan en `[EXTRA]`.

## Requirements Inventory

### Functional Requirements

FR1: Conectar a cámara IP vía RTSP y capturar el stream de forma continua 24/7.
FR2: Segmentar el stream en HLS con duración de segmento configurable (default 4s, rango 2–8s).
FR3: Subir segmentos a AWS S3 con estructura `{user_id}/{router_id}/{camera_id}/` (un prefijo por cámara).
FR4: Upload resumible con reintentos automáticos ante fallos de conectividad.
FR5: Buffer local de segmentos con capacidad mínima de 4 horas durante desconexiones.
FR6: Al reconectar, priorizar upload con ratio 3:1 (3 segmentos nuevos por 1 del backlog).
FR7: Auto-recuperación mediante watchdog y systemd (reinicio automático tras fallos).
FR8: Reportar health (CPU, temperatura, conectividad, cola de upload, GPS) a Supabase cada 60s.
FR9: Registrarse y vincularse a un Gateway específico en Supabase en la configuración inicial.
FR10: Configuración inicial sin terminal (YAML en partición boot): credenciales de cámara(s), AWS, vinculación, segmentación, orientación de cámara y GPS.
FR11: Soporte remoto opcional para instalación (Raspberry Pi Connect o similar).
FR12: Passthrough de codecs H.264 y H.265 sin transcodificación para fuentes RTSP.
FR13: Logs locales accesibles para diagnóstico (rotación automática, últimas 24h).
FR14: Control PTZ vía ONVIF, recibiendo comandos y ejecutándolos en la cámara.
FR15: Conectividad WiFi integrada (2.4/5 GHz) para uplink cuando el Ethernet esté ocupado.
FR16: Abstraer la fuente de video mediante `input_type` (`rtsp_ip` | `capture_card`).
FR17: [Pro] Capturar desde capturadora y codificar H.264/H.265 (encoder HW en RPi4; SW/HEVC en RPi5).
FR18: [Pro] Aceptar el feed de un control DJI (HDMI/AV) vía capturadora como vista en vivo (sin detección).
FR19: [Pro] Capturar simultáneamente múltiples cámaras IP desde un switch, con límite configurable y por licencia.
FR20: [Pro] Capturar coordenadas GPS y persistirlas en `routers` para posicionamiento en el mapa 3D de Satélites.
FR21: Generar y subir periódicamente un snapshot JPEG (last-frame) por cámara, autónomo y sin depender del Gateway.
FR22: Configurar manualmente la orientación de cada cámara (azimut, tilt, FOV, altura) y persistirla en Supabase para el frustum 3D.
FR23: [Pro] Reportar el estado operativo individual de cada cámara/stream (conectado, transmitiendo, error) en el health report.
FR24: Exponer en GTI Satélites la vista en vivo / last-frame **sin detecciones**, diferenciada de las imágenes con detección del Gateway.

### NonFunctional Requirements

NFR1: CPU bajo en passthrough (<30% promedio); en encoding por capturadora <70% por stream.
NFR2: RAM contenida por variante: <500MB (Base); <1.5GB (Pro multicámara).
NFR3: Temperatura de CPU <75°C sostenido.
NFR4: Latencia end-to-end (captura → disponible en S3) <10s típico, <15s máx.
NFR5: Uptime del stream ≥99% con conectividad disponible.
NFR6: Reconexión automática tras fallo <60s.
NFR7: Tasa de éxito de upload de segmentos ≥99.5%.
NFR8: BOM del hardware Base ≤$100 USD; Pro a definir.
NFR9: Credenciales sensibles en variables de entorno o vault (nunca en YAML).
NFR10: Todo tráfico externo por HTTPS/TLS 1.2+.
NFR11: Ancho de banda de subida ≥5 Mbps × N cámaras sostenido; documentar mínimo por configuración.
NFR12: Streams simultáneos máx por hardware y licencia (RPi4: 2 IP +1 capturadora; RPi5: 3 IP +1 capturadora); configurable; validar en piloto.
NFR13: Snapshot last-frame con frecuencia configurable (default 10s por cámara).
NFR14: Coordenadas GPS de instalaciones protegidas por RLS en Supabase (solo usuarios autorizados).

### Additional Requirements

**De Arquitectura (architecture-GTI_Router.md):**
- AR1: Story 1.1 = scaffold del repo `gti-router` (andamiaje propio monorepo PRD §4.1 + `uv init` + árbol `src/` + deps fijadas). Python 3.11 (venv PEP 668); FFmpeg 5.1 apt (passthrough); aioboto3 ~=15.
- AR2: Estado local en **SQLite** (índice de cola/backlog, durable) — desviación deliberada del JSON propuesto en PRD; segmentos en FS.
- AR3: Validación de config con `pydantic-settings ~=2.14`, fail-fast al inicio; acceso solo vía `get_config()`.
- AR4: Aislamiento por cámara: 1 subprocess FFmpeg por cámara + task asyncio supervisora; cola de upload compartida con reparto justo 3:1.
- AR5: `EncoderSelector` por board-detection (RPi4 `h264_v4l2m2m` HW; RPi5 `libx264` SW; HEVC-SW prohibido). Gate **RT1** (benchmark de encoding por capturadora) antes del piloto.
- AR6: systemd `Type=notify` + `sd_notify` watchdog; `MemoryMax`/`CPUQuota`/`OOMPolicy=kill` por variante.
- AR7: Patrón único `@with_retry` (backoff+jitter) para toda operación de red; modo degradado obligatorio sin Supabase.
- AR8: Contrato cross-sistema: Router produce last-frame **SIN** detección; Gateway produce frames **CON** detección; Satélites los diferencia.
- AR9: CI = GitHub Actions `pytest` en x86 con mocks (moto-S3, RTSP, V4L2, GPS); hardware real = checklist manual en RPi4/RPi5.

**De Base de Datos / Facturación (gtisatelites-brownfield-database.md):**
- DB1: Conversión de tipos `text→uuid` (pre-auditoría no-destructiva primero): `routers.user_id`, `gateway_health.gateway_id`, `gateway_detections.gateway_id`, `gateway_detections.camera_id`.
- DB2: Crear FKs del clúster de dispositivos (NOT VALID → VALIDATE) con `ON DELETE` protector (RESTRICT en gateway, SET NULL en dueño/router, CASCADE en telemetría/comandos) + índices en columnas FK.
- DB3: Tablas nuevas: `organizations`, `device_subscriptions`, `promotions`, `promotion_applications`, `router_health`.
- DB4: Columnas nuevas: `cameras.last_frame_url`, `cameras.tilt/fov_h/mount_height_m`, `cameras.rtsp_url` nullable + CHECK; `routers.max_cameras/firmware_version/last_seen_at/organization_id`, `routers.serial_number` UNIQUE; `gateways.organization_id/max_cameras`; `users.organization_id` FK.
- DB5: Facturación jerárquica (Organización → suscripción Satélites + `device_subscriptions` por equipo + cámaras extra → **factura consolidada Stripe**); prendible/apagable por equipo con fricción (cancel_at_period_end + confirmación + rol admin).
- DB6: Límite efectivo de cámaras = `LEAST(camera_quota, max_cameras)` (vista `device_camera_limit`); enforcement al registrar cámara + `audit_logs`.
- DB7: Promociones early-adopter (Stripe Coupons espejo): descuento %/fijo, cohorte, vigencia, cupos, no-apilar.
- DB8: PTZ usa `ptz_commands` (por `camera_id`), NO `router_commands` (que queda para OTA/reboot/config).
- DB9: Onboarding QR: el QR codifica `serial_number` (UNIQUE); el claim setea `routers.user_id`.
- DB10: RLS de propiedad (user/org → dispositivos → cámaras) y RLS de GPS sensible (NFR14). El servicio Router escribe con `service_role`.
- DB11: `camera_streams` queda **LEGACY** (congelado, no se conecta); su drift de código va a limpieza de Satélites aparte.

### UX Design Requirements

> Fuente: `docs/GTI-Design-System.md` + sistema desplegado de Satélites. Aplican a la **épica de integración mínima en Satélites**.

UX-DR1: **Panel de dispositivos** en Satélites — lista de gateways/routers del usuario/organización con estado (online/offline vía `last_seen_at`), nombre, serial, salud resumida. Molde: el panel `camera-streams` + patrón `mapService.ts`.
UX-DR2: **Visor de last-frame por cámara** — muestra la imagen cruda (`cameras.last_frame_url`) **SIN detección**, diferenciada visualmente de los frames con detección del Gateway (rojo/naranja reservados a alerta/detección; el last-frame debe leerse como "vista sin analizar").
UX-DR3: **Mapa 3D unificado de dispositivos** en `MapCanvas` — markers para **gateways, routers Y drones** sobre el mapa 3D principal, cada uno por su GPS (`routers.location` / `gateways` / posición de drone), con **frustum por cámara** según orientación (`heading`/`tilt`/`fov_h`/`mount_height_m`) y estado (online/offline). Click → last-frame + (si aplica) PTZ. Respeta RLS de GPS (NFR14).
UX-DR8: **Capa operacional común de combate en el 3D principal** — los reportes de combate (`firefighting_reports` + `fire_geometries` + perímetros/polígonos) se proyectan en el **mapa 3D principal** (no solo en el editor 2D del módulo de combate), de modo que **todos los usuarios con acceso** vean la ubicación del incendio, sus polígonos y las características del lugar para planear el combate. Unifica: incendios + polígonos de combate + infraestructura + dispositivos (gateway/router/drone) + frustums/last-frame en un solo lienzo 3D compartido.
UX-DR9: **Drones como fuente móvil** — el drone (feed DJI vía Router Pro, FR18) aparece en el 3D como dispositivo; a diferencia de gateways/routers fijos, su **posición es móvil** (a definir: marker actualizable vs. atado a su Router Pro). Su vista en vivo es **sin detección** y diferenciada visualmente.
UX-DR4: **Control PTZ mínimo** desde Satélites — UI básica (pan/tilt/zoom/stop) que emite filas a `ptz_commands` por `camera_id`; feedback de estado del comando.
UX-DR5: **Adherencia al Design System** — Next.js 15 + Tailwind + shadcn/ui + Lucide + Mapbox GL + Geist; paleta verde forestal `#166534` (marca), rojo `#ef4444` (alertas), naranja `#f97316` (solo tab activo).
UX-DR6: **Reuso de patrones existentes** — `usePermissions.ts` (roles anon/free/plus/pro/specialist/admin), `ProtectedRoute`/`ProtectedContent`, `mapService.ts` (CRUD), capas de `MapCanvas`/`mapLayers`.
UX-DR7: **Control de acceso** — visibilidad de dispositivos por rol + suscripción activa (patrón `canViewCameras`); el panel respeta RLS de propiedad y de GPS.

### FR Coverage Map

FR1: E1 — Conexión RTSP y captura 24/7
FR2: E1 — Segmentación HLS configurable
FR3: E2 — Upload a S3 con prefijo por cámara
FR4: E2 — Upload resumible con reintentos
FR5: E2 — Buffer local ≥4h
FR6: E2 — Priorización 3:1 del backlog
FR7: E3 — Auto-recuperación watchdog/systemd (base systemd en E1)
FR8: E3 — Health a Supabase cada 60s (→ router_health, habilitado por E0)
FR9: E3 — Registro/vinculación en Supabase
FR10: E1 — Config sin terminal (YAML boot)
FR11: E3 — Soporte remoto de instalación
FR12: E1 — Passthrough H.264/H.265
FR13: E1 — Logs locales rotados
FR14: E4 (ejecuta en cámara) + E9 (UI en Satélites)
FR15: E1 — WiFi uplink
FR16: E5 — Abstracción input_type
FR17: E5 — Encoding desde capturadora HW/SW
FR18: E5 — Feed DJI vista en vivo
FR19: E5 — Multicámara IP desde switch
FR20: E6 (captura/persiste GPS) + E8 (posición 3D)
FR21: E6 — Snapshot last-frame autónomo
FR22: E6 (orientación) + E8 (frustum 3D)
FR23: E5 — Estado operativo por cámara en health
FR24: E6 (produce sin detección) + E7 (muestra diferenciada)

## Epic List

### Epic 0: Relinking CORE de Base de Datos `[FOUNDATION]`
El ecosistema deja de tener dispositivos huérfanos: cada router/cámara queda ligado por identidad real (uuid + FK) a su usuario, la salud tiene hogar (`router_health`), y el last-frame/orientación tienen columnas donde vivir. Habilita que registro, health, 3D y PTZ operen sobre datos íntegros.
**Cubre:** DB1, DB2, DB4 (columnas core + `max_cameras` hardware), DB10 (RLS propiedad/GPS) — habilita FR8, FR9, FR20–FR24

### Epic 1: Fundación y Captura Core `[ROUTER]`
El Router captura video de una cámara IP, lo segmenta en HLS localmente y corre como servicio systemd 24/7.
**FRs:** FR1, FR2, FR10, FR12, FR13, FR15 · AR1, AR3, AR6

### Epic 2: Upload a S3 y Buffer `[ROUTER]`
El video llega a S3 de forma resiliente, sobrevive desconexiones (buffer ≥4h) y prioriza tiempo real (3:1).
**FRs:** FR3, FR4, FR5, FR6 · AR2, NFR7

### Epic 3: Registro, Monitoreo y Resiliencia `[ROUTER]`
El Router es visible en Satélites, reporta su salud a `router_health`, y se recupera solo de fallos 24/7.
**FRs:** FR7, FR8, FR9, FR11 · AR7 (modo degradado)

### Epic 4: Control PTZ vía ONVIF `[ROUTER]`
El Router ejecuta en la cámara los comandos PTZ recibidos vía `ptz_commands` (por `camera_id`).
**FRs:** FR14 · DB8

### Epic 5: Multicámara y Fuentes de Entrada (Pro) `[ROUTER]`
El Router Pro captura múltiples cámaras IP y fuentes por capturadora (analógicas, feed DJI) con encoding HW/SW.
**FRs:** FR16, FR17, FR18, FR19, FR23 · AR5 (EncoderSelector + gate RT1), NFR12

### Epic 6: GPS, Orientación y Last-Frame `[ROUTER]`
El Router aporta los datos del 3D: su GPS, la orientación por cámara, y el snapshot last-frame sin detección.
**FRs:** FR20, FR21, FR22, FR24 · AR8 (contrato cross-sistema), NFR13, NFR14

### Epic 7: Panel de Dispositivos + Visor Last-Frame `[SATÉLITES]`
Los usuarios ven sus gateways/routers/cámaras en Satélites, con estado y la imagen cruda (sin detección) de cada cámara. Incluye alta/claim por serial/QR.
**UX-DR:** 1, 2, 6, 7 — consume FR24, DB9

### Epic 8: Mapa 3D Unificado + Combate al 3D `[SATÉLITES]`
El mapa 3D principal muestra gateways/routers/drones con su frustum, y proyecta los reportes de combate (incendio + polígonos) como vista operacional compartida para todos los usuarios con acceso.
**UX-DR:** 3, 8, 9 — consume FR20, FR22

### Epic 9: Control PTZ desde la UI de Satélites `[SATÉLITES]`
Operadores emiten comandos PTZ desde Satélites que el Router ejecuta (par de E4).
**UX-DR:** 4 — par de FR14

### Epic 10: Facturación Jerárquica `[EXTRA]`
La organización paga por dispositivo con factura consolidada Stripe, elige qué equipos activar, y aplica promos early-adopter. Diferible: no bloquea el MVP del Router.
**Cubre:** DB3, DB5, DB6 (`camera_quota`), DB7

### Dependencias
```
E0 → E1 → E2 → E3 ┬→ E4 → E9
                  ├→ E5
                  └→ E6 → E7 → E8
E10 [EXTRA] independiente (usa tablas de E0, se construye al final)
```

---

## Epic 0: Relinking CORE de Base de Datos `[FOUNDATION]`

El ecosistema deja de tener dispositivos huérfanos: cada router/cámara queda ligado por identidad real (uuid + FK) a su usuario, la salud del router tiene hogar, y el last-frame/orientación tienen columnas donde vivir. Toda la migración es **no-destructiva** (solo agrega/convierte en sitio; nada se elimina) y se ejecuta con aprobación explícita en staging antes de producción.

### Story 0.1: Pre-auditoría de integridad (solo lectura)

As a **administrador de datos de GTI**,
I want **detectar valores `text` que no son uuid válido y filas huérfanas antes de tocar el esquema**,
So that **ninguna conversión ni FK borre o rompa datos por sorpresa**.

**Acceptance Criteria:**

**Given** la base de producción con las 4 islas `text` (`routers.user_id`, `gateway_health.gateway_id`, `gateway_detections.gateway_id`, `gateway_detections.camera_id`)
**When** se ejecutan las queries de auditoría (§7 del doc brownfield) en modo solo lectura
**Then** se produce un reporte con: filas con valor no-uuid, filas huérfanas (sin padre en `users`/`gateways`/`cameras`), y orphans en columnas uuid candidatas a FK (`cameras.gateway_id/router_id`, `routers.gateway_id`)
**And** se emite un veredicto **GO / NO-GO**: si hay hallazgos, se documentan para corrección manual antes de la Story 0.2
**And** no se ejecuta ningún `ALTER`/`UPDATE`/`DELETE` en esta story

### Story 0.2: Conversión de tipos `text → uuid`

As a **administrador de datos de GTI**,
I want **convertir en sitio las 4 columnas `text` a `uuid` y hacer `routers.user_id` nullable**,
So that **se pueda enlazar identidad real usuario↔dispositivo y permitir inventario sin reclamar**.

**Acceptance Criteria:**

**Given** la pre-auditoría (0.1) con veredicto GO
**When** se ejecuta la migración dentro de una transacción en staging
**Then** `routers.user_id`, `gateway_health.gateway_id`, `gateway_detections.gateway_id` y `gateway_detections.camera_id` quedan tipadas como `uuid` vía `ALTER COLUMN ... TYPE uuid USING col::uuid` (conversión en sitio, datos preservados)
**And** `routers.user_id` queda `NULL`-able (inventario sin reclamar)
**And** existe un snapshot/PITR previo y un script de rollback documentado
**And** un conteo de filas antes/después confirma que no se perdió ninguna fila

### Story 0.3: Foreign keys del clúster de dispositivos + índices

As a **administrador de datos de GTI**,
I want **crear las FKs del clúster de dispositivos con `ON DELETE` protector e índices en columnas FK**,
So that **Postgres garantice integridad y los joins/RLS sean performantes sin borrados en cascada accidentales**.

**Acceptance Criteria:**

**Given** las columnas ya tipadas como uuid (0.2)
**When** se agregan las FKs como `NOT VALID` y luego se `VALIDATE`
**Then** quedan creadas: `routers.gateway_id→gateways` (RESTRICT), `routers.user_id→users` (SET NULL), `cameras.gateway_id→gateways` (RESTRICT), `cameras.router_id→routers` (SET NULL), `router_commands.router_id→routers` (CASCADE), `ptz_commands.camera_id→cameras` (CASCADE), `ptz_commands.issued_by→users` (SET NULL)
**And** se crean índices en todas las columnas FK hijas
**And** si `VALIDATE` revela huérfanos, se reportan sin borrar nada (se corrigen aparte y se re-valida)

### Story 0.4: Tabla `router_health` y columnas operativas de `routers`

As a **operador del sistema GTI**,
I want **una tabla `router_health` bien tipada y las columnas operativas que faltan en `routers`**,
So that **el Router pueda reportar su salud y el sistema conozca su firmware/última conexión y tope de cámaras**.

**Acceptance Criteria:**

**Given** la tabla `routers` con FKs ya creadas (0.3)
**When** se aplica la migración aditiva
**Then** existe `router_health` con `id uuid PK`, `router_id uuid NOT NULL → routers.id (CASCADE)`, métricas (cpu/mem/disk/temp/uptime/latencias/connectivity/upload_queue), `gps jsonb`, `per_camera jsonb`, `services_status jsonb`, `reported_at`
**And** `routers` gana `max_cameras int` (tope de hardware), `firmware_version text`, `last_seen_at timestamptz`
**And** existe índice `router_health(router_id, reported_at desc)`
**And** ninguna columna o tabla existente fue eliminada

### Story 0.5: Columnas de cámara para last-frame y orientación 3D

As a **operador del sistema GTI**,
I want **las columnas de `cameras` para guardar el last-frame y la orientación completa, y permitir fuentes sin RTSP**,
So that **el Router pueda alimentar la vista cruda y el frustum 3D, e integrar capturadoras (Pro)**.

**Acceptance Criteria:**

**Given** la tabla `cameras` existente (con `heading` y `last_frame_at`)
**When** se aplica la migración aditiva
**Then** `cameras` gana `last_frame_url text`, `tilt float8`, `fov_h float8`, `mount_height_m float8`
**And** `cameras.rtsp_url` pasa a ser `NULL`-able con un CHECK `(source_type <> 'rtsp_ip' OR rtsp_url IS NOT NULL)` (las fuentes `capture_card` no tienen RTSP)
**And** los datos existentes de `cameras` permanecen intactos

### Story 0.6: `serial_number` único en `routers` (base del onboarding QR)

As a **técnico de instalación**,
I want **que `routers.serial_number` sea único**,
So that **el onboarding por QR pueda reclamar un router por su serial sin ambigüedad**.

**Acceptance Criteria:**

**Given** la tabla `routers`
**When** se verifica que no existan seriales duplicados y se agrega la constraint
**Then** existe `UNIQUE (serial_number)` en `routers`
**And** si hubiera duplicados previos, se reportan para resolución manual antes de aplicar la constraint
**And** la constraint permite `NULL` (routers aún sin serial asignado en fábrica)

### Story 0.7: RLS de propiedad y de GPS sensible

As a **administrador del sistema GTI**,
I want **políticas RLS que liguen usuario → dispositivos → cámaras y protejan el GPS**,
So that **cada usuario vea solo sus equipos y las coordenadas sensibles queden protegidas (NFR14)**.

**Acceptance Criteria:**

**Given** el hilo de identidad ya cableado (0.2–0.3)
**When** se habilita RLS en `routers`, `cameras`, `router_health`, `router_commands`, `ptz_commands`
**Then** un usuario solo puede leer dispositivos/cámaras/salud de los que es dueño (`routers.user_id = auth.uid()` y herencia por join)
**And** las coordenadas GPS (`routers.location`, `cameras.location`) solo son visibles a usuarios autorizados (NFR14)
**And** el servicio Router (que escribe health/registro) opera con `service_role` (bypassa RLS) sin bloquearse
**And** se cubre el caso de routers sin dueño (`user_id IS NULL`, inventario) — no visibles a usuarios finales

---

## Epic 1: Fundación y Captura Core `[ROUTER]`

El Router captura video de una cámara IP, lo segmenta en HLS localmente y corre como servicio systemd 24/7.

### Story 1.1: Scaffold del proyecto, logging y retry

As a **desarrollador del equipo GTI**,
I want **un proyecto Python estructurado con logging y retry reutilizable desde el inicio**,
So that **se pueda desarrollar y diagnosticar desde las primeras líneas**.

**Acceptance Criteria:**

**Given** un repo vacío `gti-router`
**When** se ejecuta el scaffold (AR1)
**Then** existe el árbol `src/` (config, platform, camera/sources, pipeline, upload, storage, health, location, utils) con `pyproject.toml` (uv) y deps fijadas (aioboto3~=15, onvif-zeep, pydantic-settings~=2.14, PyYAML, psutil, pynmea2, systemd-python; dev: pytest, pytest-asyncio, moto, ruff)
**And** `src/utils/logging.py` formatea a journald con `camera_id` en contexto, y `src/utils/retry.py` expone el único `@with_retry` (backoff exponencial + jitter)
**And** hay `tests/fixtures/sample.mp4` (10s H.264) y un GitHub Actions que corre `pytest` en x86

### Story 1.2: Sistema de configuración YAML validada

As a **técnico de instalación**,
I want **configurar el Router con un `router.yaml` validado y sin terminal**,
So that **se especifiquen cámaras y parámetros sin tocar código**.

**Acceptance Criteria:**

**Given** un `router.yaml` en la partición boot
**When** el servicio arranca
**Then** `get_config()` carga y valida con `pydantic-settings` (cámaras como lista con `input_type`/`orientation`/`gps`; bloques hls, aws, supabase, device, health, licensing) con expansión de `${ENV}` y fail-fast ante config inválida
**And** en primer arranque copia `/boot/router.yaml` a `/etc/gti-router/` con permisos seguros
**And** ningún módulo fuera de `src/config/` lee YAML o `os.environ` directo

### Story 1.3: Cliente/Fuente RTSP con probe

As a **sistema GTI Router**,
I want **conectarme a una cámara IP vía RTSP y verificar el stream**,
So that **confirmar conectividad antes de capturar**.

**Acceptance Criteria:**

**Given** una URL RTSP configurada
**When** se llama `RTSPSource.probe()`
**Then** conecta por TCP (`rtsp_transport=tcp`), retorna metadata (codec H.264/H.265, resolución, framerate) con timeout configurable
**And** lanza excepciones tipadas (`RTSPConnectionError`, `RTSPAuthError`, `RTSPCodecError`)
**And** hay tests con mock RTSP sin hardware

### Story 1.4: Pipeline FFmpeg para segmentación HLS

As a **sistema GTI Router**,
I want **segmentar el stream en HLS por passthrough**,
So that **el video quede listo para upload incremental**.

**Acceptance Criteria:**

**Given** una fuente de video válida
**When** arranca `HLSPipeline` (1 subprocess FFmpeg por cámara)
**Then** segmenta con `-c copy` y `-hls_time {segment_duration}` (2–8s) generando `segment_%05d.ts` + `playlist.m3u8`, monitorea exit code/stderr y reintenta
**And** emite un callback por segmento nuevo con el contrato `(camera_id, segment_path, created_at)`
**And** hay tests de integración con `tests/fixtures/sample.mp4`

### Story 1.5: Orquestación principal (main.py)

As a **operador del sistema**,
I want **un entry point que coordine los módulos**,
So that **el Router funcione como una unidad cohesiva**.

**Acceptance Criteria:**

**Given** la config validada y la cámara conectada
**When** se ejecuta `async main()`
**Then** inicializa en secuencia (config → log → cámara → pipeline) gestionando todo en el event loop asyncio con retry RTSP por backoff
**And** maneja `SIGTERM`/`SIGINT` de forma graceful y expone estado de app para health checks
**And** usa exit codes definidos (0 ok, 1 config, 2 cámara, 3 pipeline)

### Story 1.6: Servicio systemd

As a **técnico de instalación**,
I want **instalar el Router como servicio systemd**,
So that **inicie con el sistema y se controle con comandos estándar**.

**Acceptance Criteria:**

**Given** un RPi con el código instalado
**When** se instala vía `scripts/install.sh`
**Then** existe `gti-router.service` con `Type=notify`, `Restart=on-failure`, `MemoryMax`/`CPUQuota`/`OOMPolicy=kill` según variante (Base/Pro) y `EnvironmentFile` para secretos
**And** el servicio inicia correctamente y la config se toma de la partición boot
**And** existe opción documentada de acceso remoto de soporte (RPi Connect o similar)

### Story 1.7: WiFi uplink integrado

As a **técnico de instalación**,
I want **conectar el Router a internet por WiFi integrado**,
So that **el Ethernet quede libre para la cámara/switch**.

**Acceptance Criteria:**

**Given** credenciales WiFi en la config sin terminal
**When** el Router arranca sin Ethernet disponible
**Then** conecta por WiFi (2.4/5 GHz) validando SSID/PSK con mensajes claros y reintentos por backoff
**And** prioriza WiFi cuando el Ethernet está ocupado por la cámara/switch
**And** reporta el estado de conexión en el health report

---

## Epic 2: Upload a S3 y Buffer `[ROUTER]`

El video llega a S3 de forma resiliente, sobrevive desconexiones (buffer ≥4h) y prioriza tiempo real (3:1).

### Story 2.1: Cliente S3 con aioboto3

As a **sistema GTI Router**,
I want **subir segmentos a S3 de forma async**,
So that **el upload no bloquee la captura**.

**Acceptance Criteria:**

**Given** credenciales AWS en variables de entorno
**When** se llama `S3Uploader.upload_segment(path)`
**Then** sube con `aioboto3` (multipart para >5MB) usando el prefijo `{user_id}/{router_id}/{camera_id}/segment_%05d.ts` y retorna la URL S3
**And** sube el `playlist.m3u8` con Content-Type correcto
**And** hay tests con `moto`

### Story 2.2: Cola de upload con índice en SQLite

As a **sistema GTI Router**,
I want **una cola que gestione los segmentos pendientes con estado durable**,
So that **el pipeline y el uploader estén desacoplados y sobrevivan reinicios**.

**Acceptance Criteria:**

**Given** segmentos generados por el pipeline
**When** el callback HLS encola y un worker consume
**Then** el índice de cola/estado vive en **SQLite** (`storage/db.py`), transaccional y durable (AR2)
**And** al iniciar carga la cola persistida y escanea segmentos huérfanos en el buffer
**And** expone métricas (`queue_size`, `items_processed`, `items_pending`)

### Story 2.3: Retry de upload con backoff

As a **sistema GTI Router**,
I want **reintentar uploads fallidos con backoff inteligente**,
So that **fallas temporales de red no causen pérdida de video**.

**Acceptance Criteria:**

**Given** un upload que falla
**When** el error es transitorio (timeout, reset, 5xx)
**Then** reintenta vía `@with_retry` (1→60s + jitter, máx configurable) y al agotar mueve el segmento a la cola "failed"
**And** los errores permanentes (403/404) NO se reintentan
**And** emite métricas (`upload_success_count`, `upload_error_count`, `upload_retry_count`)

### Story 2.4: Buffer local y gestión de espacio

As a **sistema GTI Router**,
I want **mantener segmentos localmente cuando no puedo subirlos**,
So that **las desconexiones no causen pérdida de video**.

**Acceptance Criteria:**

**Given** una desconexión de red prolongada
**When** el buffer crece
**Then** mantiene mínimo 4h de capacidad, monitorea espacio y aplica FIFO eliminando **solo segmentos ya subidos**
**And** alerta vía health cuando el buffer supera 80%
**And** los segmentos no subidos NUNCA se eliminan

### Story 2.5: Priorización de backlog (ratio 3:1)

As a **sistema GTI Router**,
I want **priorizar video en tiempo real sobre el backlog al reconectar**,
So that **los operadores vean video actual mientras se recupera el histórico**.

**Acceptance Criteria:**

**Given** un backlog acumulado y conexión restaurada
**When** el worker consume
**Then** usa dos colas (`realtime`/`backlog`) con ratio 3:1 configurable, consumiendo solo de la no vacía si una se agota
**And** emite métricas (`realtime_queue_size`, `backlog_queue_size`, `backlog_oldest_age_seconds`)

### Story 2.6: Integración pipeline → upload (E2E)

As a **sistema GTI Router**,
I want **que los segmentos generados se encolen automáticamente**,
So that **el flujo sea continuo sin intervención manual**.

**Acceptance Criteria:**

**Given** el pipeline y el upload worker corriendo como tasks concurrentes
**When** se genera un segmento
**Then** el callback de `HLSPipeline` llama a `UploadQueue.enqueue()` y el flujo creado→encolado→subido→confirmado queda logueado con `upload_latency_seconds`
**And** el graceful shutdown espera uploads (máx 30s) y persiste la cola en SQLite

---

## Epic 3: Registro, Monitoreo y Resiliencia `[ROUTER]`

El Router es visible en Satélites, reporta su salud a `router_health`, y se recupera solo de fallos 24/7.

### Story 3.1: Registro de dispositivo en Supabase

As a **administrador del sistema GTI**,
I want **que el Router se registre/actualice en `routers` al iniciar**,
So that **sea visible en Satélites y quede vinculado a su Gateway**.

**Acceptance Criteria:**

**Given** la config con `device` y `supabase`
**When** el Router arranca
**Then** hace upsert en `routers` (por `serial_number`) con `name`, `gateway_id`, `firmware_version`, `last_seen_at`, modo degradado si Supabase no responde
**And** guarda el `gateway_id` vinculado para referencias futuras (PTZ, health)
**And** hay tests con mock de Supabase

### Story 3.2: Health Reporter hacia router_health

As a **administrador del sistema GTI**,
I want **ver la salud del Router en tiempo real**,
So that **detectar problemas antes de que causen pérdida de video**.

**Acceptance Criteria:**

**Given** el Router en operación
**When** transcurren 60s (configurable)
**Then** inserta en `router_health` métricas de sistema (cpu/mem/disk/temp), de app (cola/uploads), de conectividad (rtsp/s3/supabase), `gps` y el bloque `per_camera`
**And** si Supabase no está disponible, encola localmente (máx 1h) y envía en batch al reconectar
**And** todas las llamadas a Supabase son no-bloqueantes

### Story 3.3: Monitor de sistema

As a **sistema GTI Router**,
I want **monitorear recursos continuamente**,
So that **reportar estado y tomar acciones preventivas**.

**Acceptance Criteria:**

**Given** umbrales configurados en YAML
**When** el `SystemMonitor` muestrea cpu/ram/disco/temperatura
**Then** marca flags de alerta al exceder umbrales y, ante temperatura crítica (>80°C), loguea WARNING y marca throttling
**And** hay tests con mocks de psutil

### Story 3.4: Auto-recuperación RTSP

As a **sistema GTI Router**,
I want **reconectarme automáticamente a la cámara al perder conexión**,
So that **la captura continúe sin intervención**.

**Acceptance Criteria:**

**Given** una pérdida de conexión (FFmpeg exit/timeout)
**When** se detecta
**Then** reintenta con backoff (1→60s) manteniendo buffer y cola intactos, y tras N fallos (default 30) marca "cámara no disponible"
**And** emite métricas (`rtsp_reconnect_count`, `rtsp_connected`, `rtsp_last_connected`)

### Story 3.5: Watchdog systemd

As a **operador del sistema**,
I want **que systemd reinicie el servicio si se cuelga**,
So that **el Router se recupere de crashes sin intervención**.

**Acceptance Criteria:**

**Given** `gti-router.service` con `WatchdogSec=30`
**When** el Router opera normalmente
**Then** envía heartbeat `sd_notify` cada 15s vía `health/watchdog.py`
**And** si el heartbeat se detiene, systemd reinicia el servicio (`Restart=on-failure`, límites `StartLimit*`)

### Story 3.6: Modo degradado sin Supabase

As a **sistema GTI Router**,
I want **seguir operando aunque Supabase no esté disponible**,
So that **captura y upload continúen sin dependencia externa**.

**Acceptance Criteria:**

**Given** Supabase no disponible
**When** el Router intenta registrar o reportar
**Then** continúa sin bloquear (captura/upload siguen), encola health localmente (máx 1h FIFO) y reintenta cada 60s
**And** expone el flag `supabase_connected` en métricas
**And** sin `gateway_id` el PTZ queda inactivo (documentado)

### Story 3.7: Orquestación final y ciclo de vida

As a **operador del sistema**,
I want **inicio y apagado ordenados de todos los componentes**,
So that **el Router opere predeciblemente y no pierda datos en shutdown**.

**Acceptance Criteria:**

**Given** todos los componentes con `async start()`/`async stop()`
**When** el Router arranca y luego recibe shutdown
**Then** sigue la secuencia de init (12 pasos: fail-fast en config/cámara, degradado en Supabase) y shutdown ordenado (6 pasos, timeout configurable 30s)
**And** emite un health report final y retorna exit 0 solo si el shutdown fue limpio

---

## Epic 4: Control PTZ vía ONVIF `[ROUTER]`

El Router ejecuta en la cámara los comandos PTZ recibidos vía `ptz_commands` (por `camera_id`).

### Story 4.1: Cliente ONVIF para PTZ

As a **sistema GTI Router**,
I want **conectarme a la cámara vía ONVIF y ejecutar PTZ**,
So that **mover la cámara según instrucciones remotas**.

**Acceptance Criteria:**

**Given** una cámara con `ptz_enabled = true`
**When** `PTZController.connect()`
**Then** detecta capacidades (`supports_pan/tilt/zoom/presets`) y expone `continuous_move`, `relative_move`, `absolute_move`, `stop`, `get_presets`, `go_to_preset`, `get_position` (ONVIF Profile S, timeout configurable)
**And** lanza excepciones tipadas y tiene tests con mock ONVIF

### Story 4.2: Recepción de comandos desde ptz_commands

As a **sistema GTI Router**,
I want **recibir comandos PTZ en tiempo real desde `ptz_commands`**,
So that **el control tenga latencia mínima**.

**Acceptance Criteria:**

**Given** las cámaras del router con `ptz_enabled`
**When** se inserta una fila en `ptz_commands`
**Then** `CommandReceiver` la recibe vía Supabase Realtime suscrito a `ptz_commands` filtrado por los `camera_id` del router (fallback polling 2s), con reconexión por backoff
**And** los comandos `ptz_stop` tienen prioridad y nuevos movimientos cancelan pendientes
**And** marca el comando como `processing` antes de ejecutar

### Story 4.3: Ejecución y feedback de comandos

As a **operador en GTI Satélites**,
I want **que el comando se ejecute y se reporte el resultado con la posición**,
So that **tener feedback inmediato de mis acciones**.

**Acceptance Criteria:**

**Given** un comando `processing`
**When** `PTZController` lo ejecuta
**Then** actualiza la fila en `ptz_commands` con `status` (`completed`/`failed`), `executed_at`, `error_message` y la posición post-ejecución
**And** reintenta la actualización (máx 3) y encola si falla
**And** emite latencia `ptz_command_latency_ms`

### Story 4.4: Validación de permisos y seguridad

As a **administrador del sistema GTI**,
I want **que solo comandos autorizados se ejecuten**,
So that **la cámara no sea controlada por actores no autorizados**.

**Acceptance Criteria:**

**Given** un comando entrante
**When** se valida
**Then** descarta comandos con `issued_at` > 30s, valida que `camera_id` pertenezca a una cámara de este router y aplica rate-limit 60/min (excepto `ptz_stop`)
**And** registra rechazos con razón y métrica `ptz_commands_rejected`

### Story 4.5: Integración de PTZ con el ciclo de vida

As a **operador del sistema**,
I want **que PTZ se integre coherentemente con el resto del Router**,
So that **funcione junto a los demás componentes**.

**Acceptance Criteria:**

**Given** la orquestación (3.7)
**When** arranca el Router
**Then** PTZ se activa solo si la cámara soporta PTZ Y el registro Supabase fue exitoso; si no soporta, loguea INFO y continúa
**And** el health report incluye capacidades PTZ y posición actual

### Story 4.6: Consulta de posición sin movimiento

As a **operador en GTI Satélites**,
I want **saber la posición actual de la cámara sin moverla**,
So that **orientarme antes de enviar movimientos**.

**Acceptance Criteria:**

**Given** un comando `ptz_get_position`
**When** se procesa
**Then** responde con posición (+ preset activo si aplica) sin afectar la cámara, sin rate-limit y aún durante un movimiento

---

## Epic 5: Multicámara y Fuentes de Entrada (Pro) `[ROUTER]`

El Router Pro captura múltiples cámaras IP y fuentes por capturadora (analógicas, feed DJI) con encoding HW/SW, manteniendo el principio calidad sobre cantidad.

### Story 5.1: Abstracción de fuente de entrada (`input_type`)

As a **desarrollador del equipo GTI**,
I want **una capa que abstraiga el origen del video (RTSP IP o capturadora)**,
So that **el pipeline trate ambas fuentes de forma uniforme**.

**Acceptance Criteria:**

**Given** una cámara con `input_type: rtsp_ip | capture_card`
**When** se inicializa su fuente
**Then** la interfaz `VideoSource` expone implementaciones `RTSPSource` (passthrough) y `CaptureCardSource` (V4L2 `/dev/videoN`) con metadata común (resolución, framerate, codec)
**And** `pipeline/` consume `VideoSource` sin conocer el origen concreto
**And** hay tests unitarios con mocks de ambas fuentes

### Story 5.2: Encoding desde capturadora (HW/SW) + gate RT1

As a **sistema GTI Router Pro**,
I want **codificar a H.264/H.265 el video de una capturadora según el hardware**,
So that **pueda segmentarse y subirse igual que un stream RTSP**.

**Acceptance Criteria:**

**Given** una `CaptureCardSource` en RPi4 o RPi5
**When** se selecciona el encoder
**Then** usa `h264_v4l2m2m` (HW) en RPi4 y `libx264` (SW) en RPi5, con HEVC-SW prohibido, respetando NFR1 (<70% CPU/stream)
**And** existe un benchmark documentado (**gate RT1**) que valida la viabilidad de encoding por capturadora antes del piloto
**And** si se excede el presupuesto de CPU, se reduce el nº de cámaras, nunca la calidad por stream

### Story 5.3: Feed de control DJI como vista en vivo

As a **operador en GTI Satélites**,
I want **ver el feed del control DJI vía capturadora**,
So that **tener vista en vivo del drone sin procesarlo para detección**.

**Acceptance Criteria:**

**Given** un control DJI conectado por HDMI/AV a una capturadora V4L2
**When** el Router captura ese `input_type: capture_card`
**Then** transmite el feed como **vista en vivo sin detección** a Satélites
**And** si no hay señal, marca la fuente como inactiva en el health report

### Story 5.4: Multicámara IP con aislamiento por cámara

As a **sistema GTI Router Pro**,
I want **capturar varias cámaras IP desde un switch sin que una afecte a otra**,
So that **la caída de una cámara no degrade las demás**.

**Acceptance Criteria:**

**Given** varias cámaras IP configuradas
**When** el Router opera
**Then** cada cámara tiene 1 subprocess FFmpeg + 1 task supervisora (frontera de fallo dura) y comparten el pool de upload con reparto justo 3:1
**And** la caída/reconexión de una cámara no interrumpe la captura ni el upload de las demás

### Story 5.5: Board-detection y portabilidad RPi4/RPi5

As a **desarrollador del equipo GTI**,
I want **que el mismo código detecte el board y elija el pipeline adecuado**,
So that **un solo binario corra en Base (RPi4) y Pro (RPi5)**.

**Acceptance Criteria:**

**Given** el arranque en un RPi
**When** `platform/board.py` lee `/proc/device-tree/model`
**Then** identifica RPi4/RPi5 y el `EncoderSelector` elige encoder y límites acordes (HW vs SW)
**And** hay tests que simulan ambos boards

### Story 5.6: Límites de cámaras por hardware y licencia

As a **administrador del sistema GTI**,
I want **aplicar el tope de cámaras por hardware**,
So that **se garantice calidad plena por stream (calidad sobre cantidad)**.

**Acceptance Criteria:**

**Given** `routers.max_cameras` (tope de hardware) y el nº de cámaras configuradas
**When** el Router arranca o se agrega una cámara
**Then** rechaza exceder `max_cameras` con un error claro y lo registra
**And** (la cuota por **suscripción pagada** `camera_quota` se aplica en E10; aquí solo el tope físico)

### Story 5.7: Estado operativo individual por cámara

As a **administrador del sistema GTI**,
I want **ver el estado de cada cámara/stream**,
So that **diagnosticar qué fuente falla en un nodo multicámara**.

**Acceptance Criteria:**

**Given** un Router con varias fuentes
**When** se emite el health report
**Then** el bloque `per_camera` reporta por cámara `{camera_id, input_type, connected, streaming, last_segment_at, error}`
**And** los estados se actualizan ante conexión/desconexión de cada fuente

---

## Epic 6: GPS, Orientación y Last-Frame `[ROUTER]`

El Router aporta los datos del 3D: su GPS, la orientación por cámara, y el snapshot last-frame sin detección.

### Story 6.1: Captura y persistencia de GPS

As a **administrador del sistema GTI**,
I want **que el Router capture su GPS y lo persista**,
So that **posicionarlo automáticamente en el mapa 3D de Satélites**.

**Acceptance Criteria:**

**Given** un módulo GPS (gpsd/pynmea2) en un Router Pro
**When** hay fix disponible
**Then** persiste las coordenadas en `routers.location` (jsonb) y las incluye en el health report; si no hay fix, conserva la última coordenada conocida
**And** las coordenadas se tratan como dato sensible (protegidas por RLS, NFR14)

### Story 6.2: Orientación por cámara

As a **técnico de instalación**,
I want **configurar la orientación de cada cámara**,
So that **construir el frustum de la cámara en el 3D de Satélites**.

**Acceptance Criteria:**

**Given** el bloque `orientation` de cada cámara en `router.yaml`
**When** el Router registra/actualiza la cámara
**Then** persiste azimut/tilt/FOV/altura en `cameras` (`heading`, `tilt`, `fov_h`, `mount_height_m`)
**And** valida rangos (azimut 0–360, tilt y FOV plausibles) con error claro si son inválidos

### Story 6.3: Snapshot last-frame autónomo

As a **operador en GTI Satélites**,
I want **un last-frame periódico por cámara, sin depender del Gateway**,
So that **ver la imagen cruda de cada cámara aunque no haya detección**.

**Acceptance Criteria:**

**Given** una cámara activa
**When** transcurre el intervalo configurable (default 10s, NFR13)
**Then** genera un JPEG last-frame, lo sube a S3 y actualiza `cameras.last_frame_url` + `cameras.last_frame_at`
**And** funciona de forma autónoma aunque no haya Gateway vinculado
**And** el snapshot no lleva ninguna semántica de detección

### Story 6.4: Contrato de exposición sin detección

As a **arquitecto del ecosistema GTI**,
I want **que el Router exponga su vista claramente marcada como "sin detección"**,
So that **Satélites la diferencie de los frames con detección del Gateway**.

**Acceptance Criteria:**

**Given** las imágenes que produce el Router (last-frame, feed DJI)
**When** se publican para Satélites
**Then** se marcan con un origen/versión de contrato que indica **sin detección** (`source` del Router, no del Gateway)
**And** el Router nunca ejecuta modelos de detección (toda inferencia es del Gateway)

---

## Epic 7: Panel de Dispositivos + Visor Last-Frame `[SATÉLITES]`

Los usuarios ven sus gateways/routers/cámaras en Satélites, con estado y la imagen cruda (sin detección) de cada cámara. Incluye alta/claim por serial/QR.

### Story 7.1: Panel de dispositivos

As a **usuario de GTI Satélites**,
I want **un panel que liste mis gateways y routers con su estado**,
So that **monitorear de un vistazo mis equipos**.

**Acceptance Criteria:**

**Given** un usuario autenticado con dispositivos propios
**When** abre el panel de dispositivos
**Then** ve sus gateways/routers (nombre, serial, online/offline por `last_seen_at`, salud resumida desde `gateway_health`/`router_health`) respetando RLS de propiedad
**And** reutiliza patrones existentes (`usePermissions`, `mapService`, molde del panel `camera-streams`)
**And** un usuario no ve dispositivos de otros

### Story 7.2: Alta/claim de dispositivo por serial/QR

As a **usuario de GTI Satélites**,
I want **reclamar un router/gateway escaneando su QR o ingresando el serial**,
So that **vincularlo a mi cuenta**.

**Acceptance Criteria:**

**Given** un dispositivo registrado con `serial_number` único y sin dueño (`user_id IS NULL`)
**When** el usuario escanea el QR (que codifica el serial) o ingresa el serial
**Then** el claim setea `routers.user_id`/`gateways.user_id` al usuario y el equipo aparece en su panel
**And** si el serial no existe o ya tiene dueño, muestra un error claro

### Story 7.3: Visor de last-frame por cámara

As a **usuario de GTI Satélites**,
I want **ver la última imagen cruda de cada cámara del Router**,
So that **inspeccionar la escena sin esperar una detección**.

**Acceptance Criteria:**

**Given** una cámara con `last_frame_url`
**When** el usuario abre su visor
**Then** muestra la imagen cruda con su `last_frame_at`, **diferenciada visualmente** de los frames con detección del Gateway (rojo/naranja reservados a alerta/detección; el last-frame se lee como "vista sin analizar")
**And** respeta el Design System (verde `#166534`, shadcn/ui, Geist)

### Story 7.4: Control de acceso a dispositivos

As a **administrador del sistema GTI**,
I want **que el panel respete rol, suscripción y RLS**,
So that **solo usuarios autorizados vean/operen dispositivos**.

**Acceptance Criteria:**

**Given** los roles existentes (anon/free/plus/pro/specialist/admin)
**When** un usuario accede al panel
**Then** la visibilidad sigue el patrón `canViewDevices` (rol + suscripción activa) y la RLS de propiedad/GPS
**And** las coordenadas GPS solo se muestran a usuarios autorizados (NFR14)

---

## Epic 8: Mapa 3D Unificado + Combate al 3D `[SATÉLITES]`

El mapa 3D principal muestra gateways/routers/drones con su frustum, y proyecta los reportes de combate (incendio + polígonos) como vista operacional compartida para todos los usuarios con acceso.

### Story 8.1: Markers de dispositivos en el mapa 3D

As a **operador en GTI Satélites**,
I want **ver gateways, routers y drones posicionados en el mapa 3D**,
So that **tener una vista unificada de mis equipos en el terreno**.

**Acceptance Criteria:**

**Given** dispositivos con coordenadas (`routers.location`, gateways, drone)
**When** se carga el `MapCanvas`
**Then** dibuja un marker por dispositivo según su GPS, con badge de estado (online/offline por `last_seen_at`) y color por salud
**And** al hacer click abre el last-frame de sus cámaras (y PTZ si aplica)
**And** respeta la RLS de GPS (NFR14) y reutiliza la arquitectura de capas (`mapLayers`/store)

### Story 8.2: Frustums de cámara en el 3D

As a **operador en GTI Satélites**,
I want **ver el cono/frustum de visión de cada cámara**,
So that **entender qué área cubre cada cámara del Router**.

**Acceptance Criteria:**

**Given** cámaras con orientación (`heading`, `tilt`, `fov_h`, `mount_height_m`)
**When** se activa la capa de dispositivos
**Then** dibuja el frustum por cámara sobre el terreno 3D según su orientación y altura de montaje
**And** el frustum se diferencia visualmente de los markers de detección del Gateway (no usa rojo/naranja de alerta)

### Story 8.3: Reportes de combate proyectados al 3D principal

As a **usuario con acceso en GTI Satélites**,
I want **ver la ubicación del incendio y sus polígonos de combate en el mapa 3D principal**,
So that **todos veamos el lugar y planeemos cómo combatir el incendio**.

**Acceptance Criteria:**

**Given** un reporte de combate (`firefighting_reports` + `fire_geometries`)
**When** se carga el mapa 3D principal
**Then** proyecta la ubicación del incendio y sus polígonos/perímetros (hoy dibujados en el editor 2D) sobre el 3D, visibles para todos los usuarios con acceso
**And** la vista se actualiza cuando el combate cambia (perímetro/fase) respetando permisos de lectura

### Story 8.4: Drone como fuente móvil en el 3D

As a **operador en GTI Satélites**,
I want **ver el drone (feed DJI vía Router Pro) en el mapa 3D**,
So that **seguir su vista en vivo dentro de la imagen operacional**.

**Acceptance Criteria:**

**Given** un drone cuyo feed entra por un Router Pro
**When** se renderiza el 3D
**Then** aparece como dispositivo con **posición móvil** (actualizable, a diferencia de los fijos) y su vista en vivo marcada **sin detección**
**And** queda definido el modelo de posición del drone (marker propio actualizable vs. atado a su Router Pro)

---

## Epic 9: Control PTZ desde la UI de Satélites `[SATÉLITES]`

Operadores emiten comandos PTZ desde Satélites que el Router ejecuta (par de E4).

### Story 9.1: Controles PTZ en la interfaz

As a **operador en GTI Satélites**,
I want **una UI de control PTZ (pan/tilt/zoom/stop)**,
So that **orientar la cámara para investigar una zona**.

**Acceptance Criteria:**

**Given** una cámara con `ptz_enabled = true` que el usuario puede operar
**When** abre el control PTZ
**Then** muestra controles de pan/tilt, zoom y stop (y presets si la cámara los soporta), respetando rol/suscripción
**And** los controles se deshabilitan si la cámara no soporta PTZ o el dispositivo está offline

### Story 9.2: Emisión de comandos a ptz_commands

As a **operador en GTI Satélites**,
I want **que mis acciones generen comandos**,
So that **el Router los reciba y ejecute**.

**Acceptance Criteria:**

**Given** una acción del usuario en el control PTZ
**When** se confirma
**Then** inserta una fila en `ptz_commands` con `camera_id`, `command_type`, `payload`, `issued_by`, `issued_at`, `expires_at`
**And** `ptz_stop` se emite con prioridad y no está sujeto a rate-limit en la UI

### Story 9.3: Feedback de estado del comando

As a **operador en GTI Satélites**,
I want **ver si el comando se ejecutó**,
So that **saber el resultado de mi acción**.

**Acceptance Criteria:**

**Given** un comando emitido
**When** el Router lo procesa y actualiza `ptz_commands`
**Then** la UI refleja `status` (`processing`→`completed`/`failed`), la posición resultante y el `error_message` si falló (vía Realtime o polling)
**And** muestra latencia/timeout si el comando expira sin ejecutarse

---

## Epic 10: Facturación Jerárquica `[EXTRA]`

La organización paga por dispositivo con factura consolidada Stripe, elige qué equipos activar, y aplica promos early-adopter. Diferible: no bloquea el MVP del Router.

### Story 10.1: Organizaciones como cuenta de facturación

As a **administrador comercial de GTI**,
I want **una entidad `organizations` que agrupe usuarios y equipos**,
So that **tener una cuenta única que reciba la factura consolidada**.

**Acceptance Criteria:**

**Given** el `users.organization_id` hoy colgante
**When** se aplica la migración aditiva
**Then** existe `organizations` (`id`, `name`, `cohort`, `stripe_customer_id`) y `users.organization_id` queda con FK a ella
**And** `gateways` y `routers` ganan `organization_id` con FK (la org posee los equipos)
**And** nada existente se elimina

### Story 10.2: Suscripción por dispositivo

As a **administrador comercial de GTI**,
I want **una suscripción por gateway/router con cuota de cámaras**,
So that **cobrar por equipo de forma independiente**.

**Acceptance Criteria:**

**Given** equipos pertenecientes a una organización
**When** se crea su suscripción
**Then** existe `device_subscriptions` con FKs reales (`gateway_id` XOR `router_id`, `organization_id`), `plan`, `camera_quota`, `status`, campos Stripe y de período
**And** un equipo sin suscripción activa no expone su data en Satélites

### Story 10.3: Facturación consolidada con Stripe

As a **cliente de GTI**,
I want **una sola factura con todos mis equipos y extras**,
So that **ver el costo total de mis sistemas en un solo lugar**.

**Acceptance Criteria:**

**Given** una organización con un `stripe_customer_id` y varias suscripciones
**When** Stripe genera la factura
**Then** se produce **una factura consolidada** con líneas por suscripción de Satélites, por cada equipo y por cámaras extra, sumando el total
**And** los pagos se reflejan en `payments`/`device_subscriptions` vía webhooks

### Story 10.4: Activar/desactivar equipos con fricción

As a **dueño/admin de una organización**,
I want **elegir qué equipos pago y poder desactivar con cuidado**,
So that **controlar el gasto sin apagar equipos por error**.

**Acceptance Criteria:**

**Given** una organización con N equipos
**When** el dueño/admin activa o desactiva un equipo
**Then** solo los `active` se cobran y exponen data; desactivar usa `cancel_at_period_end` + confirmación + razón, y solo rol dueño/admin puede hacerlo
**And** la acción queda en `audit_logs`

### Story 10.5: Enforcement de cuota de cámaras

As a **sistema GTI**,
I want **aplicar el límite efectivo de cámaras por dispositivo**,
So that **no se transmitan más cámaras de las pagadas ni de las que soporta el hardware**.

**Acceptance Criteria:**

**Given** `device_subscriptions.camera_quota` y `routers.max_cameras`
**When** se intenta registrar/activar una cámara
**Then** el límite efectivo es `LEAST(camera_quota, max_cameras)` (expuesto por la vista `device_camera_limit`) y se rechaza el exceso
**And** el rechazo se registra en `audit_logs`

### Story 10.6: Promociones early-adopter

As a **administrador comercial de GTI**,
I want **aplicar descuentos a cohortes early-adopter**,
So that **les sea viable pagar por varios sistemas activos**.

**Acceptance Criteria:**

**Given** una promoción definida (`promotions`: tipo %/fijo, scope, cohorte, duración, cupos, vigencia)
**When** se aplica a una organización o equipo
**Then** se registra en `promotion_applications` (espejo de un Stripe Coupon) y el descuento aparece como línea en la factura consolidada
**And** se respetan los guardrails: no apilar, `max_redemptions`, vigencia; aplicación/revocación auditada
