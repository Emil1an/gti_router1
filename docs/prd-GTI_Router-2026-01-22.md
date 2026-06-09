---
project_name: "GTI Router"
type: "prd"
version: "2.0"
generated_by: "pm agent (John)"
date: "2026-01-22"
lastEdited: "2026-06-01"
status: "ready-for-architecture"
sources:
  - "product-brief-GTI_Router-2026-01-21.md"
  - "GTI_Router_Feasibility_Study_Results-2025-01-20.md"
editHistory:
  - date: "2026-06-01"
    changes: "v2.0 — Introducción de dos SKUs (Router Base / Router Pro). Nuevas fuentes de entrada (capturadora de video + feed DJI vista en vivo), multicámara desde switch, GPS en heartbeat, snapshot last-frame propio del Router, orientación de cámara para vista 3D en Satélites, esquema Supabase 1:N, contrato de integración cross-sistema. Código objetivo RPi4 + RPi5."
---

# GTI Router - Product Requirements Document (PRD)

## 1. Objetivos y Contexto

### 1.1 Objetivos

- **Habilitar expansión de red de cámaras a bajo costo** - Permitir a organizaciones agregar cámaras IP existentes a la red de detección GTI sin requerir hardware GPU costoso en cada ubicación
- **Reducir barrera de entrada** - Proveer un dispositivo edge de ~$200 USD que captura y transmite video a procesamiento centralizado (Gateway o Cloud)
- **Lograr operación confiable 24/7** - Mantener ≥99% de uptime para vigilancia continua de incendios con recuperación automática ante interrupciones
- **Soportar inversiones existentes en cámaras** - Habilitar modelo BYOC (Bring Your Own Camera) para cámaras Hikvision y compatibles ONVIF
- **Ofrecer dos líneas de producto (Base y Pro)** - Atender desde el caso simple de 1 cámara IP hasta nodos con múltiples cámaras y fuentes por capturadora, con un mismo código base
- **Habilitar fuentes de video flexibles** - Soportar tanto cámaras IP (RTSP/ONVIF) como capturadoras de video (cámaras analógicas, salida HDMI/AV de control DJI) como entrada al Router
- **Geolocalizar y orientar dispositivos** - Reportar GPS en el heartbeat y orientación de cámara para ubicar y dirigir cada Router en el mapa 3D de GTI Satélites
- **Validar product-market fit** - Desplegar 30-50 unidades en Año 1 para establecer viabilidad antes de escalar

### 1.1.1 Modelo de dos SKUs

GTI Router se ofrece en dos variantes que comparten el mismo código base (objetivo de portabilidad RPi4 + RPi5):

| Variante | Hardware base | Fuentes / cámaras | GPS | Capturadora | Precio objetivo |
|----------|---------------|-------------------|-----|-------------|-----------------|
| **Router Base** | Raspberry Pi 4 2GB | 1 cámara IP (RTSP), passthrough | No | No | ~$200 USD (BOM ≤$100) |
| **Router Pro** | Raspberry Pi 5 | Multicámara IP + 1 capturadora (encoding); GPS; orientación | Sí | Sí | BOM/precio a definir (BOM nuevo) |

**Principio de portabilidad de código:** el software soporta passthrough y encoding para **H.264 y H.265** y se ejecuta tanto en RPi4 como en RPi5, seleccionando el encoder según el hardware disponible (encoder por hardware cuando exista, *fallback* a software cuando no). Esto significa que el RPi5 —que no incluye encoder de video por hardware— usa codificación por software o HEVC cuando procesa una fuente por capturadora.

### 1.2 Contexto

GTI Router aborda un gap crítico en el ecosistema de detección de incendios GTI. Mientras GTI Gateway provee detección IA de fuego usando YOLOv8, su costo de ~$1,500 USD lo hace impráctico para desplegar en cada ubicación de cámara. Muchos clientes potenciales (organizaciones de conservación, empresas agrícolas, propietarios rurales) ya tienen cámaras IP de seguridad instaladas pero carecen de capacidades automatizadas de detección de incendios.

El Router actúa como nodo edge económico (~$85 USD de costo de hardware) que captura streams RTSP de cámaras existentes, los segmenta en formato HLS, y sube a AWS S3 para procesamiento por un Gateway centralizado o servicio cloud futuro. Esta arquitectura habilita un modelo hub-and-spoke donde un Gateway puede procesar streams de múltiples Routers, reduciendo dramáticamente el costo por cámara de detección IA.

### 1.3 Change Log

| Fecha | Versión | Descripción | Autor |
|-------|---------|-------------|-------|
| 2026-01-22 | 1.0 | PRD inicial basado en Product Brief | John (PM) |
| 2026-06-01 | 2.0 | Dos SKUs (Base/Pro); fuentes por capturadora + feed DJI (vista en vivo); multicámara desde switch; GPS en heartbeat; snapshot last-frame propio; orientación de cámara para 3D; esquema Supabase 1:N; contrato cross-sistema; código RPi4+RPi5 con H.264/H.265 | John (PM) |

---

## 2. Requerimientos

### 2.1 Requerimientos Funcionales

**Convención de variantes:** salvo indicación, FR1–FR15 aplican a **Router Base y Router Pro**. FR16–FR24 son **transversales o exclusivos de Router Pro** según la columna SKU. La detección de fuego/humo **nunca** ocurre en el Router (ni Base ni Pro): el Router solo captura, segmenta, transmite y muestra video; la inferencia IA se procesa exclusivamente en GTI Gateway (o Cloud en Fase 2).

| ID | Requerimiento | Prioridad | SKU |
|----|---------------|-----------|-----|
| FR1 | El Router debe conectarse a una cámara IP vía protocolo RTSP y capturar el stream de video de forma continua 24/7 | Must Have | Base+Pro |
| FR2 | El Router debe segmentar el stream de video en formato HLS con duración de segmento configurable (default 4 segundos, rango 2-8 segundos) para permitir ajuste según calidad de conexión | Must Have | Base+Pro |
| FR3 | El Router debe subir los segmentos de video a un bucket AWS S3 configurado, usando estructura `s3://bucket/{user_id}/{router_id}/{camera_id}/` (un prefijo por cámara para soportar multicámara) | Must Have | Base+Pro |
| FR4 | El Router debe implementar upload resumible con reintentos automáticos ante fallos de conectividad | Must Have | Base+Pro |
| FR5 | El Router debe almacenar segmentos localmente con capacidad mínima de 4 horas de buffer durante desconexiones de red | Must Have | Base+Pro |
| FR6 | Al reconectar tras desconexión, el Router debe priorizar upload con ratio 3:1 (3 segmentos nuevos por cada 1 del backlog) | Must Have | Base+Pro |
| FR7 | El Router debe implementar auto-recuperación mediante watchdog y systemd para reinicio automático tras fallos | Must Have | Base+Pro |
| FR8 | El Router debe reportar su estado de salud (CPU, temperatura, conectividad, cola de upload, y coordenadas GPS) a Supabase cada 60 segundos | Must Have | Base+Pro |
| FR9 | El Router debe registrarse y vincularse a un Gateway específico en Supabase durante la configuración inicial | Must Have | Base+Pro |
| FR10 | El Router debe proveer configuración inicial sin terminal (YAML en partición boot) para credenciales de cámara(s), AWS, vinculación, parámetros de segmentación, orientación de cámara y coordenadas GPS | Must Have | Base+Pro |
| FR11 | El Router debe ofrecer opción de soporte remoto para instalación (Raspberry Pi Connect o similar) | Should Have | Base+Pro |
| FR12 | El Router debe soportar passthrough de codecs H.264 y H.265 sin transcodificación para fuentes RTSP | Must Have | Base+Pro |
| FR13 | El Router debe mantener logs locales accesibles para diagnóstico (rotación automática, últimas 24 horas) | Must Have | Base+Pro |
| FR14 | El Router debe soportar control PTZ vía ONVIF, recibiendo comandos del Gateway y ejecutándolos en la cámara | Must Have | Base+Pro |
| FR15 | El Router debe soportar conectividad WiFi integrada (2.4/5 GHz) para uplink a internet cuando el Ethernet esté ocupado por la cámara (Base) o por el switch del nodo de cámaras (Pro) | Must Have | Base+Pro |
| FR16 | El Router debe abstraer la fuente de video mediante un parámetro `input_type` que soporte `rtsp_ip` (cámara IP) y `capture_card` (capturadora de video V4L2) | Must Have | Base+Pro |
| FR17 | El Router debe capturar video desde una capturadora de video y codificarlo a H.264/H.265, usando el encoder por hardware cuando esté disponible (RPi4: `h264_v4l2m2m`) y haciendo *fallback* a codificación por software o HEVC cuando no exista encoder por hardware (RPi5) | Must Have | Pro |
| FR18 | El Router debe aceptar el feed de un control DJI (salida HDMI/AV) vía capturadora como fuente de **vista en vivo** transmitida a Satélites; este feed no se procesa para detección en el Router | Must Have | Pro |
| FR19 | El Router debe capturar simultáneamente múltiples cámaras IP conectadas a un switch y transmitir múltiples streams, con un límite configurable y aplicado por licencia (default recomendado: RPi4 = 2 cámaras IP, RPi5 = 3 cámaras IP; +1 fuente por capturadora en cualquiera) | Must Have | Pro |
| FR20 | El Router debe capturar sus coordenadas GPS y persistirlas en Supabase (tabla `routers`) para posicionarlo automáticamente en el mapa 3D de GTI Satélites; el propósito es ubicación en campo, no tracking continuo | Must Have | Pro |
| FR21 | El Router debe generar y subir periódicamente un snapshot JPEG (last-frame) por cámara, de forma autónoma y sin depender del Gateway, para alimentar la vista "sin detección" en Satélites | Must Have | Base+Pro |
| FR22 | El Router debe permitir configurar manualmente la orientación de cada cámara (azimut, tilt, FOV y altura de montaje) y persistirla en Supabase para construir el frustum de la cámara en el 3D de Satélites | Must Have | Base+Pro |
| FR23 | El Router debe reportar el estado operativo individual de cada cámara/stream (conectado, transmitiendo, error) en el health report | Must Have | Pro |
| FR24 | El Router debe exponer en GTI Satélites su vista en vivo / last-frame **sin detecciones**, diferenciada de las imágenes con detecciones generadas por el Gateway | Must Have | Base+Pro |

### 2.2 Requerimientos No Funcionales

| ID    | Requerimiento                                                          | Métrica                      |
| ----- | ---------------------------------------------------------------------- | ---------------------------- |
| NFR1  | El uso de CPU debe mantenerse bajo en passthrough; en encoding por capturadora debe permanecer dentro de límites operables | <30% promedio (passthrough); <70% por stream en encoding |
| NFR2  | El uso de RAM debe mantenerse contenido según variante                 | <500MB (Base); <1.5GB (Pro multicámara) |
| NFR3  | La temperatura de operación del CPU debe mantenerse controlada         | <75°C sostenido              |
| NFR4  | La latencia end-to-end (captura → disponible en S3) debe ser aceptable | <10s típico, <15s max        |
| NFR5  | El uptime del stream debe ser alto cuando hay conectividad disponible  | ≥99%                         |
| NFR6  | El tiempo de reconexión automática tras fallo debe ser rápido          | <60 segundos                 |
| NFR7  | La tasa de éxito de upload de segmentos debe ser alta                  | ≥99.5%                       |
| NFR8  | El BOM del hardware Base debe mantenerse económico                     | Base ≤$100 USD; Pro: target a definir |
| NFR9  | Las credenciales sensibles deben almacenarse de forma segura           | Variables de entorno o vault |
| NFR10 | Todo tráfico externo debe usar conexiones seguras                      | HTTPS/TLS 1.2+               |
| NFR11 | El ancho de banda de subida requerido escala con el número de cámaras  | ≥5 Mbps × N cámaras sostenido; documentar mínimo recomendado por configuración |
| NFR12 | El número máximo de streams simultáneos debe respetar el hardware      | RPi4: 2 IP (3 máx) + 1 capturadora; RPi5: 3 IP (4 máx) + 1 capturadora; configurable y por licencia; validar en piloto |
| NFR13 | El snapshot last-frame debe subirse con frecuencia configurable        | Default cada 10s por cámara; ajustable |
| NFR14 | Las coordenadas GPS de instalaciones son dato sensible                 | Protegidas por RLS en Supabase, visibles solo a usuarios autorizados |

---

## 3. Objetivos de Diseño de Interfaz de Usuario

### 3.1 Visión General de UX

GTI Router es un dispositivo **plug-and-play** con mínima fricción de instalación. La experiencia del usuario evoluciona en fases:

| Fase | Configuración | Usuario objetivo |
|------|---------------|------------------|
| **MVP** | Configuración sin terminal (YAML en partición boot) + soporte remoto opcional | Técnico con o sin experiencia |
| **Fase 2** | Web UI local | Técnicos y usuarios avanzados |
| **Fase 3** | App móvil guiada | Usuarios finales (particulares) |

**Visión final:** Un usuario particular puede comprar un Router, descargar la app GTI, escanear un QR en el dispositivo, y ser guiado paso a paso para conectar su cámara existente al ecosistema GTI. La misma app servirá para:
- Validar compatibilidad de cámara antes de comprar Router
- Configuración guiada del Router
- Monitoreo básico de estado

### 3.2 Paradigmas de Interacción Clave

| Fase   | Contexto              | Interfaz                         | Usuario                  | Paradigma                          |
| ------ | --------------------- | -------------------------------- | ------------------------ | ---------------------------------- |
| MVP    | Configuración         | YAML en partición boot           | Técnico                  | Edición guiada sin terminal        |
| MVP    | Soporte remoto opcional| Raspberry Pi Connect (o similar) | Soporte GTI              | Acceso remoto para instalación     |
| MVP    | Feedback en sitio     | LEDs (opcional)                  | Técnico                  | Indicadores visuales básicos       |
| MVP    | Monitoreo             | GTI Satélites web                | Admin                    | Dashboard de dispositivos          |
| Fase 2 | Configuración         | Web UI local      | Técnico/Usuario avanzado | Formulario web simple         |
| Fase 2 | Validación pre-compra | App móvil         | Prospecto                | Test de cámara                |
| Fase 3 | Configuración         | App móvil guiada  | Usuario final            | Wizard paso a paso            |
| Fase 3 | Monitoreo móvil       | App móvil         | Usuario final            | Vista de estado simplificada  |

### 3.3 Pantallas y Vistas Core

**MVP:**
1. **Archivo de configuración (`router.yaml`)** - Estructura clara, comentarios explicativos, ejemplos incluidos. En Router Pro la sección `cameras` es una **lista**, cada entrada con su `input_type` (`rtsp_ip` | `capture_card`), credenciales/dispositivo, y bloque `orientation` (azimut, tilt, FOV, altura). Bloque `gps` con las coordenadas de instalación.
2. **Vista de Router en GTI Satélites** - Card/panel mostrando estado de salud, métricas, última actividad y estado por cámara
3. **Posicionamiento automático en mapa 3D de Satélites** - El Router aparece en el mapa 3D usando sus coordenadas GPS (tabla `routers`); cada cámara dibuja su frustum según su orientación configurada (azimut/tilt/FOV/altura)
4. **Panel de vista en vivo / last-frame del Router (sin detecciones)** - Imágenes crudas de las cámaras del Router, diferenciadas visualmente del panel de detecciones del Gateway. Permite seleccionar el Router en el 3D y direccionar/orientar la vista de cada cámara

**Fase 2:**
3. **Web UI local de configuración** - Formulario accesible vía IP del Router
4. **App móvil - Modo validación** - Probar conexión RTSP de cámara existente antes de comprar Router

**Fase 3 (Visión):**
5. **App móvil - Configuración guiada** - Wizard: Escanear QR → Conectar WiFi → Detectar cámara → Vincular con cuenta GTI
6. **App móvil - Dashboard personal** - Estado de mis Routers y alertas

### 3.4 Accesibilidad

- **MVP:** Ninguno específico (usuarios técnicos)
- **Fase 3 (App móvil):** WCAG AA para accesibilidad en app destinada a usuarios finales

### 3.5 Branding

- Dispositivo físico: **Etiqueta con logo GTI y código QR** (esencial para flujo de app móvil)
- GTI Satélites: Hereda branding existente
- App móvil / Web UI local: Paleta de colores y tipografía GTI consistente

### 3.6 Dispositivos y Plataformas Objetivo

| Componente | Plataforma | Fase |
|------------|------------|------|
| Dispositivo Router Base | Raspberry Pi 4 2GB | MVP |
| Dispositivo Router Pro | Raspberry Pi 5 (multicámara, capturadora, GPS) | MVP |
| Configuración YAML | Cualquier editor | MVP |
| Monitoreo web | GTI Satélites (Web) | MVP |
| Web UI local | Web Responsive | Fase 2 |
| App móvil | iOS + Android | Fase 2-3 |

**Nota arquitectónica:** El diseño de API y estructura de configuración del Router debe considerar desde MVP la futura integración con app móvil.

---

## 4. Supuestos Técnicos

### 4.1 Estructura del Repositorio

**Decisión:** Monorepo

```
gti-router/
├── src/
│   ├── main.py
│   ├── camera/
│   │   ├── rtsp_client.py
│   │   └── ptz_control.py
│   ├── pipeline/
│   │   ├── ffmpeg_hls.py
│   │   └── buffer.py
│   ├── upload/
│   │   ├── s3_client.py
│   │   └── queue.py
│   ├── health/
│   │   ├── registration.py
│   │   ├── reporter.py
│   │   ├── monitor.py
│   │   └── watchdog.py
│   ├── config/
│   │   └── loader.py
│   └── utils/
│       ├── logging.py
│       └── retry.py
├── config/
│   └── router.yaml.example
├── scripts/
│   ├── install.sh
│   └── uninstall.sh
├── systemd/
│   └── gti-router.service
├── tests/
│   └── fixtures/
│       └── sample.mp4
└── README.md
```

### 4.2 Arquitectura de Servicio

**Decisión:** Aplicación monolítica con módulos y workers async

**Patrón de concurrencia:**
- Event loop principal (asyncio)
- aioboto3 para operaciones S3 async nativas
- Colas en memoria con límites definidos

### 4.3 Requerimientos de Testing

| Nivel | Alcance | Herramientas |
|-------|---------|--------------|
| **Unit** | Funciones individuales | pytest |
| **Integration** | Flujos con mocks (RTSP mock → HLS → S3 mock) | pytest + moto |
| **Fault injection** | Simular fallos en cada punto del pipeline | pytest fixtures |
| **Manual** | Hardware real y cámaras físicas | Checklist documentado |

### 4.4 Stack Técnico

| Componente | Tecnología | Notas |
|------------|------------|-------|
| Lenguaje | Python 3.11+ | asyncio nativo |
| Video | FFmpeg 6.x | Passthrough HLS |
| AWS | aioboto3 | Upload async nativo |
| PTZ | onvif-zeep 0.2.x | ONVIF Profile S |
| Proceso | systemd | Watchdog, restart |
| Config | PyYAML | Validación al inicio |
| Logging | logging → journald | Rotación automática |
| OS | RPi OS Lite 64-bit | Bookworm |
| Captura por capturadora | V4L2 + FFmpeg | Dispositivos `/dev/videoN`; encoder HW `h264_v4l2m2m` (RPi4) o software/HEVC (RPi5) |
| Codecs | H.264 + H.265 (HEVC) | Passthrough para RTSP; encoding para capturadora |
| GPS | gpsd / pynmea2 | Módulo GPS USB/UART (solo Pro); lectura puntual de coordenadas |
| Snapshot | FFmpeg / OpenCV | Extracción de last-frame JPEG por cámara |
| Hardware soportado | Raspberry Pi 4 2GB y Raspberry Pi 5 | Código único, selección de encoder según board |

### 4.5 Notas Arquitectónicas

**Orden de inicialización requerido:**
1. Cargar y validar configuración → Si falla: EXIT con error claro
2. Inicializar logging
3. Inicializar monitor de sistema
4. Registrar en Supabase → Si falla: WARN, continuar (modo degradado)
5. Conectar cámara RTSP → Si falla: RETRY con backoff exponencial
6. Cargar cola persistida + escanear huérfanos
7. Iniciar pipeline FFmpeg → Si falla: RETRY
8. Iniciar upload worker
9. Iniciar health reporter
10. Iniciar watchdog heartbeat
11. Iniciar PTZ controller (si soportado)
12. OPERACIÓN NORMAL

**Gestión de colas y buffer:**
- Cola de upload: máximo 1000 items o 4 horas de segmentos
- Política de limpieza: FIFO cuando buffer lleno (solo segmentos ya subidos)
- Alertar via health report cuando buffer >80%

**Modo degradado (sin Supabase):**
- El Router DEBE operar sin conectividad a Supabase
- Health reports se encolan localmente (max 1 hora)
- PTZ no funciona sin Supabase (comandos vienen de ahí)

**Integraciones:**

| Sistema | Protocolo | Auth | Comportamiento ante fallo |
|---------|-----------|------|---------------------------|
| Cámara RTSP | RTSP/TCP | User/pass | Retry infinito con backoff |
| Cámara PTZ | ONVIF SOAP | User/pass | Retry 3x, luego descartar comando |
| Capturadora de video (Pro) | V4L2 local (`/dev/videoN`) | N/A (dispositivo local) | Reintentar apertura del dispositivo con backoff |
| Control DJI (Pro) | HDMI/AV → capturadora → V4L2 | N/A | Vista en vivo; si no hay señal, marcar fuente inactiva |
| GPS (Pro) | Serial/UART o USB (gpsd) | N/A | Si no hay fix, reintentar; persistir última coordenada conocida |
| AWS S3 | HTTPS | IAM env vars | Retry con backoff, encolar si falla |
| Supabase | HTTPS REST + Realtime | API key | Modo degradado, encolar reports |

**Seguridad:**
- Credenciales: Variables de entorno, NO en YAML
- AWS IAM: Permisos mínimos (solo bucket específico)
- SSH: Solo keys, puerto configurable
- TLS 1.2+ obligatorio para todo tráfico externo
- GPS: las coordenadas de instalación son dato sensible → protegidas por RLS en Supabase

### 4.6 Contrato de Integración Cross-Sistema (Router ↔ Gateway ↔ Satélites)

El Router participa en un flujo de tres sistemas con responsabilidades estrictamente separadas:

| Sistema | Responsabilidad | Produce | Consume |
|---------|-----------------|---------|---------|
| **GTI Router** | Captura, segmentación, encoding (capturadora), upload, GPS, orientación, snapshot last-frame | Segmentos HLS a S3; last-frame JPEG **sin detección**; health + GPS a Supabase | Comandos PTZ (Supabase Realtime) |
| **GTI Gateway** | Inferencia IA (YOLOv8) sobre los streams del Router | Imágenes/eventos **con detección** | Segmentos HLS del Router (S3) |
| **GTI Satélites** | Visualización 3D, triage, operación | Selección y orientación de cámara | GPS + orientación + last-frame del Router; detecciones del Gateway |

**Reglas del contrato:**
- El Router **nunca** ejecuta modelos de detección. Toda inferencia ocurre en el Gateway (o Cloud en Fase 2).
- Satélites distingue dos orígenes de imagen para cada cámara: (a) **last-frame sin detección** producido por el Router, (b) **frame con detección** producido por el Gateway. Ambos coexisten en el panel del Router dentro del 3D.
- El feed de control DJI es exclusivamente **vista en vivo** vía Router → Satélites; no genera detecciones.
- La posición 3D de cada cámara se compone de: coordenadas GPS del Router (`routers`) + orientación manual por cámara (azimut/tilt/FOV/altura).

---

## 5. Lista de Épicas

| # | Épica | Objetivo | Stories |
|---|-------|----------|---------|
| 1 | **Fundación y Captura Core** | Setup proyecto, config YAML, conexión RTSP, segmentación HLS local, servicio systemd | 6 |
| 2 | **Upload a S3 y Buffer** | Upload con aioboto3, retry, buffer local, priorización backlog | 6 |
| 3 | **Registro, Monitoreo y Resiliencia** | Registro Supabase, health reporting, watchdog, auto-recuperación, orquestación | 7 |
| 4 | **Control PTZ vía ONVIF** | Cliente ONVIF, recepción comandos Realtime, ejecución, seguridad | 6 |
| 5 | **Multicámara y Fuentes de Entrada (Pro)** | Abstracción `input_type`, capturadora con encoding HW/SW, feed DJI vista en vivo, multicámara desde switch, límites por licencia, portabilidad RPi4/RPi5 | ~7 |
| 6 | **GPS, Orientación y Vista 3D en Satélites** | Captura GPS y persistencia en Supabase, orientación por cámara, snapshot last-frame propio, panel de vista sin detección, posicionamiento y frustum en mapa 3D | ~7 |

**Total: 6 Épicas, ~39 Stories** *(conteo de épicas 5 y 6 a refinar en `bmad-create-epics-and-stories`)*

### Diagrama de Dependencias

```
Épica 1: Fundación + Captura
        │
        ▼
Épica 2: Upload + Buffer
        │
        ▼
Épica 3: Registro + Monitoreo
        │
        ▼
Épica 4: Control PTZ
        │
        ├──────────────┬──────────────┐
        ▼              ▼              ▼
Épica 5 (Pro):   Épica 6 (Base+Pro):
Multicámara +    GPS + Orientación +
Capturadora      Vista 3D Satélites
        │              │
        └──────┬───────┘
               ▼
          MVP COMPLETO
```

---

## 6. Épica 1: Fundación y Captura Core

### Objetivo

Establecer la base del proyecto GTI Router con estructura de código, logging, sistema de configuración, conexión a cámara RTSP, y pipeline de segmentación HLS local. Al completar esta épica, el Router puede capturar video de una cámara IP y almacenarlo localmente en formato HLS listo para upload.

### Diagrama de Dependencias

```
           Story 1.1
    Setup + Logging Básico
              │
      ┌───────┴───────┐
      ▼               ▼
  Story 1.2       Story 1.3
 Config YAML    RTSP Client
      │               │
      └───────┬───────┘
              ▼
        Story 1.4
   Pipeline FFmpeg HLS
              │
      ┌───────┴───────┐
      ▼               ▼
  Story 1.5       Story 1.6
Orquestación    Systemd Service
              │
              ▼
        Story 1.7
      WiFi Uplink
```

### Stories

#### Story 1.1: Setup del Proyecto, Estructura Base y Logging

**Como** desarrollador del equipo GTI,  
**Quiero** un proyecto Python estructurado con logging desde el inicio,  
**Para que** pueda desarrollar y diagnosticar problemas desde las primeras líneas de código.

**Criterios de Aceptación:**

1. Repositorio `gti-router` creado con estructura completa de directorios
2. `pyproject.toml` con dependencias fijadas: Python 3.11+, PyYAML, aioboto3, onvif-zeep, pytest, pytest-asyncio, systemd-python
3. `.gitignore` para Python, secretos, archivos de video (.ts, .m3u8)
4. `README.md` con descripción, requisitos de hardware, instrucciones de setup
5. Módulo `src/utils/logging.py` con formato estructurado: `{timestamp} [{level}] [{module}] {message}` + soporte JSON extra
6. Módulo `src/utils/retry.py` con decorator async reutilizable para backoff exponencial
7. Niveles de log: DEBUG, INFO, WARNING, ERROR documentados
8. Video de prueba (10 segundos, H.264) en `tests/fixtures/sample.mp4`
9. GitHub Actions básico ejecutando `pytest`
10. Todos los módulos creados con docstrings y `__init__.py`

---

#### Story 1.2: Sistema de Configuración YAML

**Como** técnico de instalación,  
**Quiero** configurar el Router mediante un archivo YAML claro y validado,  
**Para que** pueda especificar los parámetros de cámara y operación sin modificar código.

**Criterios de Aceptación:**

1. Archivo `config/router.yaml.example` con schema COMPLETO (camera, hls, storage, aws, upload, supabase, device, health)
2. Módulo `src/config/loader.py` que carga y parsea YAML
3. Validación de campos requeridos con mensajes específicos
4. Validación de tipos y rangos (ej: segment_duration entre 2-8)
5. Documentación de valores por defecto en comentarios
6. Expansión de variables de entorno `${VAR_NAME}`
7. Función `get_config()` que retorna objeto tipado
8. Tests unitarios con casos válidos, inválidos, y variables de entorno
9. En primer arranque, si existe `/boot/router.yaml`, se copia a `/etc/gti-router/router.yaml` con permisos seguros
10. Si falta o es inválida la configuración, el servicio entra en modo seguro y reporta el error en logs y señal de estado

---

#### Story 1.3: Cliente RTSP con Fixtures de Prueba

**Como** sistema GTI Router,  
**Quiero** conectarme a una cámara IP vía RTSP y verificar que el stream está disponible,  
**Para que** pueda confirmar conectividad antes de iniciar la captura.

**Criterios de Aceptación:**

1. Módulo `src/camera/rtsp_client.py` con clase async `RTSPClient`
2. Método `probe()` que verifica conexión y retorna metadata del stream
3. Conexión usando protocolo TCP: `rtsp_transport=tcp`
4. Timeout configurable (default 10 segundos)
5. Retorna: codec (H.264/H.265), resolución, framerate
6. Excepciones tipadas: `RTSPConnectionError`, `RTSPAuthError`, `RTSPCodecError`
7. Log de eventos de conexión
8. Mock de servidor RTSP en `tests/fixtures/` para tests sin hardware
9. Tests unitarios con mocks
10. Documentación de URLs RTSP para cámaras Hikvision comunes

---

#### Story 1.4: Pipeline FFmpeg para Segmentación HLS

**Como** sistema GTI Router,  
**Quiero** capturar el stream RTSP y segmentarlo en formato HLS,  
**Para que** el video esté listo para upload incremental a S3.

**Criterios de Aceptación:**

1. Módulo `src/pipeline/ffmpeg_hls.py` con clase `HLSPipeline`
2. FFmpeg ejecutado como subprocess async con `asyncio.create_subprocess_exec`
3. Configuración passthrough: `-c copy`
4. Segmentos HLS con duración configurable: `-hls_time {segment_duration}`
5. Output: `segment_%05d.ts` y `playlist.m3u8`
6. Playlist tipo EVENT: `-hls_playlist_type event`
7. Monitoreo del proceso FFmpeg: detectar exit code, stderr, reintentar
8. Callback/evento cuando nuevo segmento es generado
9. Logging de segmentos: nombre, tamaño, timestamp
10. Módulo `src/pipeline/buffer.py` para limpieza de segmentos antiguos
11. Tests de integración usando `tests/fixtures/sample.mp4`
12. Tests verifican: generación de .ts, estructura de .m3u8

---

#### Story 1.5: Orquestación Principal

**Como** operador del sistema,  
**Quiero** un punto de entrada que coordine todos los módulos,  
**Para que** el Router funcione como una unidad cohesiva.

**Criterios de Aceptación:**

1. `src/main.py` como entry point con función `async def main()`
2. Secuencia de inicialización: cargar config → validar → log inicio → conectar cámara → iniciar pipeline
3. Event loop asyncio gestionando todos los componentes
4. Exit codes: 0 (éxito), 1 (error config), 2 (error cámara), 3 (error pipeline)
5. Manejo graceful de SIGTERM/SIGINT
6. Estado de aplicación accesible para health checks
7. Retry automático de conexión RTSP con backoff exponencial
8. Tests de integración end-to-end

---

#### Story 1.6: Servicio Systemd

**Como** técnico de instalación,  
**Quiero** instalar el Router como servicio systemd,  
**Para que** inicie automáticamente con el sistema y pueda controlarlo con comandos estándar.

**Criterios de Aceptación:**

1. Archivo `systemd/gti-router.service` con configuración completa
2. `Type=simple`, `ExecStart` apuntando a Python con main.py
3. Reinicio automático: `Restart=on-failure`, `RestartSec=10`
4. Variables de entorno desde archivo: `EnvironmentFile=/etc/gti-router/env`
5. Usuario dedicado no-root: `User=gti-router`
6. Límites: `MemoryMax=400M`, `CPUQuota=50%`
7. Script `scripts/install.sh` y `scripts/uninstall.sh`
8. Documentación de comandos systemctl
9. Verificación: servicio inicia correctamente en RPi
10. `OOMPolicy=kill` para manejo de Out of Memory
11. Instalación sin terminal: flujo documentado para copiar `router.yaml` en partición boot
12. Opción de acceso remoto de soporte activable (Raspberry Pi Connect o similar)

---

#### Story 1.7: WiFi Uplink Integrado

**Como** técnico de instalación,  
**Quiero** poder conectar el Router a internet usando el WiFi integrado,  
**Para que** el único puerto Ethernet quede disponible para la cámara.

**Criterios de Aceptación:**

1. Configuración de WiFi soportada en modo sin terminal mediante archivo en partición boot
2. Soporte de redes 2.4 GHz y 5 GHz
3. Validación de SSID/PSK y mensajes de error claros en logs
4. Prioridad de uplink: usar WiFi cuando Ethernet esté ocupado por cámara
5. Reintentos con backoff ante fallos de conexión
6. Estado de conexión reportado en health report
7. Documentación de procedimiento de configuración para técnicos no expertos

---

## 7. Épica 2: Upload a S3 y Buffer

### Objetivo

Implementar el upload de segmentos HLS a AWS S3 usando aioboto3, con retry resiliente, buffer local para desconexiones, y priorización inteligente de backlog al reconectar. Al completar esta épica, el video capturado llega a S3 y está disponible para procesamiento por GTI Gateway.

### Diagrama de Dependencias

```
      ÉPICA 1 COMPLETA
            │
  ┌─────────┴─────────┐
  ▼                   ▼
Story 2.1         Story 2.2
Cliente S3      Cola + Worker
  │                   │
  ▼            ┌──────┴──────┐
Story 2.3      ▼             ▼
Retry       Story 2.4    (parcial)
  │        Buffer Local      │
  └────────────┬─────────────┘
               ▼
         Story 2.5
      Priorización 3:1
               │
               ▼
         Story 2.6
      Integración E2E
```

### Stories

#### Story 2.1: Cliente S3 con aioboto3

**Como** sistema GTI Router,  
**Quiero** subir archivos a S3 de forma async,  
**Para que** el upload no bloquee la captura de video.

**Criterios de Aceptación:**

1. Módulo `src/upload/s3_client.py` con clase `S3Uploader`
2. Uso de `aioboto3` para operaciones async nativas
3. Configuración desde YAML: `aws.bucket`, `aws.region`, `aws.prefix`
4. Credenciales AWS desde variables de entorno
5. Método `async upload_segment(local_path) -> s3_url`
6. Método `async upload_playlist(local_path)` con Content-Type correcto
7. Multipart upload automático para archivos >5MB
8. Logging de cada upload
9. Tests con `moto` (mock de S3)

---

#### Story 2.2: Cola de Upload y Worker Async

**Como** sistema GTI Router,  
**Quiero** una cola que gestione los segmentos pendientes de upload,  
**Para que** el pipeline de video y el uploader estén desacoplados.

**Criterios de Aceptación:**

1. Módulo `src/upload/queue.py` con clase `UploadQueue`
2. Cola basada en `asyncio.Queue` con límite máximo (default 1000)
3. Método `enqueue(segment_path)` llamado por callback HLS
4. Worker async básico que consume de la cola
5. Persistencia de cola en JSON para sobrevivir reinicios
6. Al iniciar, cargar cola persistida
7. Al iniciar, escanear directorio de buffer por segmentos huérfanos
8. Logging de tamaño de cola, items procesados
9. Métricas expuestas: `queue_size`, `items_processed`, `items_pending`
10. Tests verificando: enqueue, dequeue, persistencia, escaneo huérfanos

---

#### Story 2.3: Retry con Backoff Exponencial

**Como** sistema GTI Router,  
**Quiero** reintentar uploads fallidos con backoff inteligente,  
**Para que** las fallas temporales de red no causen pérdida de video.

**Criterios de Aceptación:**

1. Decorator async `with_retry` para operaciones de upload
2. Backoff exponencial: 1s, 2s, 4s, 8s, 16s, 32s, max 60s
3. Máximo de reintentos configurable (default 10)
4. Jitter aleatorio (±20%)
5. Errores transitorios reintentados: timeout, connection reset, 5xx
6. Errores permanentes NO reintentados: 403, 404
7. Logging de cada reintento
8. Tras agotar reintentos, mover a cola "failed"
9. Métricas: `upload_success_count`, `upload_error_count`, `upload_retry_count`
10. Tests simulando fallos

---

#### Story 2.4: Buffer Local y Gestión de Espacio

**Como** sistema GTI Router,  
**Quiero** mantener segmentos localmente cuando no puedo subirlos,  
**Para que** las desconexiones de red no causen pérdida de video.

**Criterios de Aceptación:**

1. Módulo `src/pipeline/buffer.py` extendido (reemplaza lógica de 1.4)
2. Capacidad de buffer: mínimo 4 horas
3. Monitoreo de espacio cada 60 segundos
4. Alerta cuando buffer >80%
5. Política FIFO: eliminar segmentos más antiguos YA SUBIDOS
6. Segmentos no subidos NUNCA se eliminan
7. Logging de: espacio usado, disponible, eliminados
8. Métricas: `buffer_used_bytes`, `buffer_capacity_bytes`, `buffer_usage_percent`
9. Tests verificando política de limpieza

---

#### Story 2.5: Priorización de Backlog (Ratio 3:1)

**Como** sistema GTI Router,  
**Quiero** priorizar video en tiempo real sobre backlog al reconectar,  
**Para que** los operadores vean video actual mientras se recupera el histórico.

**Criterios de Aceptación:**

1. Dos colas internas: `realtime_queue` y `backlog_queue`
2. Segmentos nuevos van a `realtime_queue`
3. Segmentos persistidos/escaneados van a `backlog_queue`
4. Worker consume con ratio 3:1
5. Si una cola vacía, consumir solo de la otra
6. Ratio configurable en YAML (default 3)
7. Logging de ratio efectivo, tamaño de cada cola
8. Métricas: `realtime_queue_size`, `backlog_queue_size`, `backlog_oldest_age_seconds`
9. Tests verificando comportamiento del ratio

---

#### Story 2.6: Integración Pipeline → Upload

**Como** sistema GTI Router,  
**Quiero** que los segmentos generados se encolen automáticamente para upload,  
**Para que** el flujo sea continuo sin intervención manual.

**Criterios de Aceptación:**

1. Callback en `HLSPipeline` que notifica a `UploadQueue.enqueue()`
2. Integración en `main.py`: pipeline + upload worker como tasks concurrentes
3. Graceful shutdown: esperar uploads (max 30s), persistir cola
4. Logging end-to-end: creado → encolado → subido → confirmado
5. Playlist .m3u8 se sube después de cada segmento
6. Métrica: `upload_latency_seconds`
7. Tests de integración E2E con mocks
8. Documentación de flujo de datos en README

---

## 8. Épica 3: Registro, Monitoreo y Resiliencia

### Objetivo

Implementar registro del Router en Supabase, health reporting continuo, watchdog systemd, y auto-recuperación ante fallos. Al completar esta épica, el Router es visible en GTI Satélites, opera 24/7 sin intervención, y se recupera automáticamente de problemas.

### Stories

#### Story 3.1: Registro de Dispositivo en Supabase

**Como** administrador del sistema GTI,  
**Quiero** que el Router se registre en Supabase al iniciar,  
**Para que** pueda verlo en GTI Satélites y vincularlo a un Gateway.

**Criterios de Aceptación:**

1. Módulo `src/health/registration.py` con clase `DeviceRegistration`
2. Configuración desde YAML: `supabase.url`, `supabase.anon_key`, `device.id`, `device.name`
3. Al iniciar, llamar API Supabase para registrar/actualizar (upsert)
4. Datos: device_id, device_name, ip_local, ip_pública, versión firmware, timestamp
5. Si Supabase no disponible, continuar en modo degradado
6. Guardar `gateway_id` asignado para referencias futuras
7. Logging de registro exitoso o fallido
8. Tests con mock de Supabase API

---

#### Story 3.2: Health Reporter

**Como** administrador del sistema GTI,  
**Quiero** ver el estado de salud del Router en tiempo real,  
**Para que** pueda detectar problemas antes de que causen pérdida de video.

**Criterios de Aceptación:**

1. Módulo `src/health/reporter.py` con clase `HealthReporter`
2. Recolección de métricas cada 60 segundos (configurable)
3. Métricas del sistema: cpu_percent, memory_percent, disk_percent, temperature_celsius
4. Métricas de aplicación: todas las de Épica 2
5. Métricas de conectividad: rtsp_connected, s3_reachable, supabase_connected
6. Métrica: `firmware_version`
7. Envío a Supabase via API REST
8. Si Supabase no disponible, encolar localmente (max 1 hora)
9. Al reconectar, enviar en batch
10. Tests verificando recolección y envío

---

#### Story 3.3: Monitor de Sistema

**Como** sistema GTI Router,  
**Quiero** monitorear recursos del sistema continuamente,  
**Para que** pueda reportar estado y tomar acciones preventivas.

**Criterios de Aceptación:**

1. Módulo `src/health/monitor.py` con clase `SystemMonitor`
2. Monitoreo de CPU, RAM, disco, temperatura
3. Umbrales de alerta configurables desde YAML
4. Flag de alerta cuando umbral excedido
5. Comportamiento ante temperatura crítica (>80°C): log WARNING, flag throttling
6. Logging de alertas
7. Tests con mocks

---

#### Story 3.4: Auto-recuperación RTSP

**Como** sistema GTI Router,  
**Quiero** reconectarme automáticamente a la cámara cuando se pierde conexión,  
**Para que** la captura continúe sin intervención humana.

**Criterios de Aceptación:**

1. Detección de pérdida de conexión (FFmpeg exit, timeout)
2. Backoff exponencial: 1s, 2s, 4s... max 60s
3. Mantener buffer y cola intactos
4. Al reconectar, FFmpeg continúa (numeración continua)
5. Logging de cada intento
6. Métricas: `rtsp_reconnect_count`, `rtsp_connected`, `rtsp_last_connected`
7. Después de N fallos (default 30), marcar "cámara no disponible"
8. Tests simulando desconexión

---

#### Story 3.5: Watchdog Systemd

**Como** operador del sistema,  
**Quiero** que systemd reinicie el servicio automáticamente si falla,  
**Para que** el Router se recupere de crashes sin intervención.

**Criterios de Aceptación:**

1. Actualizar `gti-router.service` con `WatchdogSec=30`
2. Aplicación envía heartbeat cada 15 segundos via `sd_notify`
3. Módulo `src/health/watchdog.py` con función `notify_watchdog()`
4. Integración en main loop
5. `Restart=on-failure`, `RestartSec=10`
6. Límites: `StartLimitIntervalSec=300`, `StartLimitBurst=5`
7. `OOMPolicy=kill`
8. Logging de heartbeats
9. Tests en RPi real

---

#### Story 3.6: Modo Degradado sin Supabase

**Como** sistema GTI Router,  
**Quiero** seguir operando aunque Supabase no esté disponible,  
**Para que** la captura y upload continúen sin dependencia externa.

**Criterios de Aceptación:**

1. Todas las operaciones Supabase son no-bloqueantes
2. Si registro falla, continuar sin gateway_id (PTZ no funcionará)
3. Health reports se encolan localmente (max 1 hora, FIFO)
4. Reintentar conexión cada 60 segundos
5. Al reconectar, enviar encolados
6. Flag `supabase_connected` en métricas
7. Tests de integración con fault injection
8. Documentar comportamiento en cada escenario

---

#### Story 3.7: Orquestación Final y Ciclo de Vida

**Como** operador del sistema,  
**Quiero** que todos los componentes se inicialicen y detengan en orden correcto,  
**Para que** el Router opere de forma predecible y no pierda datos en shutdown.

**Criterios de Aceptación:**

1. Actualizar `main.py` con secuencia de inicialización completa (12 pasos)
2. Cada componente tiene métodos `async start()` y `async stop()`
3. Inicialización falla-rápido: si config o cámara fallan, exit inmediato
4. Inicialización tolerante: si Supabase falla, modo degradado
5. Secuencia de shutdown ordenado (6 pasos)
6. Timeout de shutdown configurable (default 30s)
7. Log de cada fase
8. Health report final antes de shutdown
9. Exit code 0 solo si shutdown fue limpio
10. Tests verificando inicio y shutdown

---

## 9. Épica 4: Control PTZ vía ONVIF

### Objetivo

Implementar control remoto de cámaras PTZ mediante protocolo ONVIF, recibiendo comandos en tiempo real desde GTI Gateway a través de Supabase Realtime y ejecutándolos en la cámara. Al completar esta épica, los operadores pueden controlar cámaras PTZ desde GTI Satélites para investigar detecciones de incendio con latencia mínima.

### Stories

#### Story 4.1: Cliente ONVIF para PTZ

**Como** sistema GTI Router,  
**Quiero** conectarme a la cámara vía ONVIF y ejecutar comandos PTZ,  
**Para que** pueda mover la cámara según instrucciones remotas.

**Criterios de Aceptación:**

1. Módulo `src/camera/ptz_control.py` con clase `PTZController`
2. Uso de `onvif-zeep` para ONVIF Profile S
3. Método `async connect()` con descubrimiento de capacidades
4. Detectar capacidades: `supports_pan`, `supports_tilt`, `supports_zoom`, `supports_presets`, `preset_count`
5. Método `async continuous_move(pan_speed, tilt_speed, zoom_speed)` - velocidades -1.0 a 1.0
6. Método `async relative_move(pan_delta, tilt_delta, zoom_delta)`
7. Método `async absolute_move(pan_pos, tilt_pos, zoom_pos)`
8. Método `async stop()`
9. Método `async get_presets()` - lista de presets de la cámara
10. Método `async go_to_preset(preset_number)`
11. Método `async get_position()` - posición actual
12. Timeout configurable (default 5s)
13. Excepciones tipadas
14. Logging y tests con mock ONVIF

---

#### Story 4.2: Recepción de Comandos desde Supabase

**Como** sistema GTI Router,  
**Quiero** recibir comandos PTZ en tiempo real desde Supabase,  
**Para que** el control de cámara tenga latencia mínima.

**Criterios de Aceptación:**

1. Módulo `src/camera/command_receiver.py` con clase `CommandReceiver`
2. Conexión primaria: Supabase Realtime (WebSocket) para latencia <200ms
3. Suscripción a `router_commands` filtrado por `router_id`
4. Fallback automático a polling cada 2 segundos
5. Reconexión automática con backoff
6. Comandos `ptz_stop` tienen prioridad alta - procesan inmediatamente
7. Nuevos comandos de movimiento cancelan pendientes
8. Marcar comando como `processing` antes de ejecutar
9. Timeout de comando: 10 segundos
10. Si Supabase no disponible, PTZ no funciona
11. Métricas: `ptz_realtime_connected`, `ptz_commands_received`
12. Tests con mock Realtime

---

#### Story 4.3: Ejecución y Feedback de Comandos

**Como** sistema GTI Router,  
**Quiero** ejecutar comandos PTZ y reportar el resultado con posición actual,  
**Para que** el operador tenga feedback inmediato de sus acciones.

**Criterios de Aceptación:**

1. Integrar `CommandReceiver` con `PTZController`
2. Parsear payload según `command_type`, llamar método correspondiente
3. Capturar resultado + obtener posición actual post-ejecución
4. Actualizar comando en Supabase con resultado completo incluyendo posición
5. Reintentar actualización (max 3 intentos), encolar si falla
6. Métricas: `ptz_commands_executed`, `ptz_commands_failed`, `ptz_commands_cancelled`
7. Métrica de latencia: `ptz_command_latency_ms`
8. Logging detallado
9. Tests de integración

---

#### Story 4.4: Validación de Permisos y Seguridad

**Como** administrador del sistema GTI,  
**Quiero** que solo comandos autorizados se ejecuten,  
**Para que** la cámara no sea controlada por actores no autorizados.

**Criterios de Aceptación:**

1. Validar `router_id` coincide con configuración
2. Validar comando de Gateway vinculado
3. Ignorar comandos con `created_at` > 30 segundos
4. Rate limiting: 60 comandos por minuto
5. Rate limiting NO aplica a `ptz_stop`
6. Logging de rechazos con razón
7. Métrica: `ptz_commands_rejected`
8. Actualizar comando rechazado en Supabase con razón
9. Tests de validación

---

#### Story 4.5: Integración con Ciclo de Vida

**Como** operador del sistema,  
**Quiero** que PTZ se integre correctamente con el resto del Router,  
**Para que** funcione de forma coherente con los demás componentes.

**Criterios de Aceptación:**

1. Actualizar orquestación (Story 3.7) para incluir PTZ
2. PTZ solo activo si cámara soporta PTZ Y registro Supabase exitoso
3. Si cámara no soporta PTZ, log INFO y continuar
4. Health report incluye capacidades PTZ detalladas y posición actual
5. Documentación de flujo PTZ
6. Tests E2E

---

#### Story 4.6: Consulta de Posición sin Movimiento

**Como** operador en GTI Satélites,  
**Quiero** saber la posición actual de la cámara sin moverla,  
**Para que** pueda orientarme antes de enviar comandos de movimiento.

**Criterios de Aceptación:**

1. Comando tipo `ptz_get_position`
2. Respuesta incluye posición + preset activo si aplica
3. Latencia mínima, no afectado por rate limiting
4. Puede ejecutarse durante movimiento
5. Tests verificando respuesta sin efecto en cámara

---

## Épica 5: Multicámara y Fuentes de Entrada (Router Pro)

### Objetivo

Habilitar el Router Pro para capturar simultáneamente múltiples cámaras IP desde un switch y aceptar fuentes por capturadora de video (cámaras analógicas y feed de control DJI). Introduce la abstracción de fuente de entrada, el encoding por hardware/software para H.264/H.265, los límites por licencia, y la portabilidad del código entre RPi4 y RPi5. Al completar esta épica, un Router Pro transmite varios streams y soporta fuentes que no son RTSP.

### Stories

#### Story 5.1: Abstracción de Fuente de Entrada (`input_type`)

**Como** desarrollador del equipo GTI,
**Quiero** una capa que abstraiga el origen del video (RTSP IP o capturadora),
**Para que** el pipeline trate ambas fuentes de forma uniforme.

**Criterios de Aceptación:**
1. Interfaz común `VideoSource` con implementaciones `RTSPSource` y `CaptureCardSource`
2. Config por cámara con campo `input_type: rtsp_ip | capture_card`
3. `CaptureCardSource` abre dispositivos V4L2 (`/dev/videoN`) configurables
4. Metadata común expuesta: resolución, framerate, codec de entrada
5. Tests unitarios con mocks de ambas fuentes

#### Story 5.2: Encoding desde Capturadora (HW/SW, RPi4 y RPi5)

**Como** sistema GTI Router Pro,
**Quiero** codificar a H.264/H.265 el video de una capturadora,
**Para que** pueda segmentarse y subirse igual que un stream RTSP.

**Criterios de Aceptación:**
1. Selección automática de encoder: `h264_v4l2m2m` (HW) en RPi4; software (`libx264`) o HEVC en RPi5
2. Detección de board en tiempo de ejecución y elección de pipeline FFmpeg adecuado
3. Parámetros de calidad/bitrate configurables
4. Soporte de H.264 y H.265 en ambas placas
5. Medición de CPU por stream documentada; respeta NFR1/NFR12
6. Tests de integración con un dispositivo V4L2 simulado

#### Story 5.3: Feed de Control DJI (Vista en Vivo)

**Como** operador en GTI Satélites,
**Quiero** ver en vivo el video del control de un DJI conectado por capturadora,
**Para que** pueda observar la zona sin esperar procesamiento de detección.

**Criterios de Aceptación:**
1. Fuente `capture_card` etiquetada como `live_view_only`
2. El feed se transmite a Satélites como vista en vivo; no se procesa para detección
3. Si no hay señal en la capturadora, la fuente se marca inactiva en el health report
4. Documentación del cableado control DJI → capturadora → RPi
5. Tests verificando que el feed no se enruta al pipeline de detección

#### Story 5.4: Captura Multicámara desde Switch

**Como** sistema GTI Router Pro,
**Quiero** capturar varias cámaras IP conectadas a un switch,
**Para que** un solo Router cubra un nodo de cámaras.

**Criterios de Aceptación:**
1. La sección `cameras` del YAML es una lista; cada entrada se inicializa como pipeline independiente
2. Cada cámara tiene su propio `camera_id`, buffer y cola de upload
3. Prefijo S3 por cámara: `s3://bucket/{user_id}/{router_id}/{camera_id}/`
4. Aislamiento de fallos: la caída de una cámara no detiene las demás
5. Tests de integración con 2–3 fuentes simultáneas

#### Story 5.5: Límites por Hardware y Licencia

**Como** administrador del sistema GTI,
**Quiero** limitar el número de cámaras según hardware y licencia,
**Para que** el Router no se degrade ni exceda lo contratado.

**Criterios de Aceptación:**
1. Límite por defecto: RPi4 = 2 IP (3 máx); RPi5 = 3 IP (4 máx); +1 capturadora en cualquiera
2. Límite por licencia leído desde Supabase (campo en `routers`)
3. Si se excede el límite, rechazar la cámara extra con log y señal de estado
4. Límite configurable y validado al inicio
5. Tests de validación de límites

#### Story 5.6: Priorización y Recursos Multicámara

**Como** sistema GTI Router Pro,
**Quiero** repartir ancho de banda y CPU entre cámaras,
**Para que** ninguna monopolice los recursos.

**Criterios de Aceptación:**
1. Cola de upload compartida con reparto equitativo entre cámaras
2. El ratio 3:1 realtime/backlog aplica por cámara
3. Métricas por cámara: `camera_id`, estado, latencia, cola
4. Alerta cuando el ancho de banda agregado excede el disponible (NFR11)
5. Tests verificando reparto justo

#### Story 5.7: Salud por Cámara

**Como** administrador del sistema GTI,
**Quiero** ver el estado individual de cada cámara del Router,
**Para que** pueda diagnosticar problemas por fuente.

**Criterios de Aceptación:**
1. Health report incluye arreglo por cámara: `camera_id`, `input_type`, `connected`, `streaming`, `last_segment_at`, `error`
2. Estado agregado del Router refleja cámaras activas/totales
3. Persistencia en Supabase (ver §10)
4. Tests de recolección por cámara

---

## Épica 6: GPS, Orientación y Vista 3D en Satélites

### Objetivo

Dotar al Router de geolocalización (GPS), orientación de cámara configurable y generación autónoma de imágenes last-frame, y exponer todo ello en GTI Satélites para posicionar el Router en el mapa 3D, dibujar el frustum de cada cámara y mostrar vista en vivo / last-frame **sin detecciones**, diferenciada de las imágenes con detección del Gateway. Al completar esta épica, un operador ubica el Router en el 3D, selecciona una cámara y orienta su vista.

### Stories

#### Story 6.1: Captura y Persistencia de GPS

**Como** técnico de instalación,
**Quiero** que el Router capture sus coordenadas GPS en campo,
**Para que** aparezca automáticamente en el mapa 3D de Satélites.

**Criterios de Aceptación:**
1. Lectura de GPS vía gpsd/serial (módulo USB/UART, solo Pro)
2. Coordenadas (lat, lon, altitud, calidad de fix) persistidas en `routers` (Supabase)
3. Propósito de ubicación, no tracking continuo: actualización al iniciar y bajo cambio significativo
4. Si no hay fix, conservar última coordenada conocida y reportar `gps_fix=false`
5. Coordenada manual de respaldo configurable en YAML
6. Tests con mock de GPS

#### Story 6.2: Orientación de Cámara Configurable

**Como** instalador,
**Quiero** definir la orientación de cada cámara,
**Para que** Satélites dibuje correctamente su frustum en el 3D.

**Criterios de Aceptación:**
1. Bloque `orientation` por cámara en YAML: `azimuth`, `tilt`, `fov`, `mount_height`
2. Validación de rangos (azimut 0–360, tilt, FOV)
3. Persistencia en Supabase (por cámara/stream)
4. Cambios de orientación se reflejan en el siguiente health report
5. Tests de validación y persistencia

#### Story 6.3: Generación y Upload de Last-Frame (sin Gateway)

**Como** sistema GTI Router,
**Quiero** producir un JPEG periódico por cámara,
**Para que** Satélites muestre una vista sin depender del Gateway.

**Criterios de Aceptación:**
1. Extracción de last-frame JPEG por cámara con FFmpeg/OpenCV
2. Frecuencia configurable (default 10s, NFR13)
3. Upload a S3 / referencia en Supabase (`camera_streams.last_frame_url`)
4. Funciona aunque no exista Gateway vinculado
5. Tests de extracción y upload

#### Story 6.4: Posicionamiento del Router en el Mapa 3D

**Como** operador en GTI Satélites,
**Quiero** ver el Router ubicado en el mapa 3D según su GPS,
**Para que** lo identifique geográficamente. *(Implementación principal en GTI Satélites; el Router provee los datos.)*

**Criterios de Aceptación:**
1. Satélites lee GPS de `routers` y coloca el dispositivo en el 3D
2. Cada cámara dibuja su frustum según orientación (azimut/tilt/FOV/altura)
3. Estado del Router (online/offline) reflejado en el marcador 3D
4. Datos sensibles (GPS) sujetos a RLS
5. Contrato de datos documentado entre Router y Satélites

#### Story 6.5: Panel de Vista en Vivo / Last-Frame sin Detección

**Como** operador en GTI Satélites,
**Quiero** ver las imágenes del Router sin detecciones, separadas de las del Gateway,
**Para que** distinga la vista cruda de la analizada. *(Implementación principal en GTI Satélites.)*

**Criterios de Aceptación:**
1. Panel del Router muestra last-frame/vista en vivo **sin detección**
2. Diferenciación visual clara respecto a imágenes con detección del Gateway
3. Selección de cámara desde el 3D abre su vista
4. El feed DJI aparece como vista en vivo
5. Coexistencia de ambos orígenes (Router crudo / Gateway con detección)

#### Story 6.6: Direccionar/Orientar la Cámara desde el 3D

**Como** operador en GTI Satélites,
**Quiero** direccionar la vista/orientación de una cámara desde el mapa 3D,
**Para que** apunte la cobertura donde la necesito. *(Para cámaras PTZ usa el canal de comandos de la Épica 4; para fijas, ajusta la orientación configurada.)*

**Criterios de Aceptación:**
1. Para cámaras PTZ: el control de dirección emite comandos vía `router_commands` (Épica 4)
2. Para cámaras fijas: permite actualizar la orientación configurada (azimut/tilt) que redibuja el frustum
3. Feedback de posición/orientación actual
4. Respeta permisos y RLS
5. Tests de extremo a extremo del flujo de orientación

#### Story 6.7: Integración GPS/Orientación con Ciclo de Vida y Health

**Como** operador del sistema,
**Quiero** que GPS y orientación se integren con el health report y la orquestación,
**Para que** se reporten de forma consistente.

**Criterios de Aceptación:**
1. GPS y orientación incluidos en el health report
2. Inicialización tolerante: sin GPS el Router opera y usa coordenada manual
3. Documentación del flujo de datos GPS/orientación → Supabase → Satélites
4. Tests de integración

---

## 10. Esquemas de Base de Datos (Supabase)

### 10.1 Tablas Nuevas Requeridas

#### Tabla: `routers`

```sql
CREATE TABLE routers (
  router_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_name TEXT NOT NULL,
  user_id UUID REFERENCES users(user_id) ON DELETE CASCADE,
  gateway_id UUID,
  -- NOTA v2.0: se elimina stream_id (1:1). La relación router→cámaras es 1:N
  -- mediante camera_streams.router_id (ver §10.2).
  sku TEXT DEFAULT 'base',                 -- 'base' | 'pro'
  firmware_version TEXT NOT NULL,
  hardware_model TEXT DEFAULT 'rpi4-2gb',  -- 'rpi4-2gb' | 'rpi5'
  ip_local TEXT,
  ip_public TEXT,
  -- Geolocalización (Pro): ubicación en campo para mapa 3D de Satélites
  gps_lat FLOAT8,
  gps_lon FLOAT8,
  gps_altitude FLOAT4,
  gps_fix BOOLEAN DEFAULT false,
  gps_updated_at TIMESTAMPTZ,
  -- Límite de cámaras por licencia (multicámara Pro)
  max_cameras INT4 DEFAULT 1,
  config_segment_duration INT4 DEFAULT 4,
  config_buffer_hours INT4 DEFAULT 4,
  config_backlog_ratio INT4 DEFAULT 3,
  status TEXT DEFAULT 'pending',
  last_seen_at TIMESTAMPTZ,
  ptz_supported BOOLEAN DEFAULT false,
  ptz_capabilities JSONB,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
```

#### Tabla: `router_health`

```sql
CREATE TABLE router_health (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  router_id UUID NOT NULL REFERENCES routers(router_id) ON DELETE CASCADE,
  cpu_percent FLOAT4,
  memory_percent FLOAT4,
  disk_percent FLOAT4,
  temperature_celsius FLOAT4,
  rtsp_connected BOOLEAN,
  s3_reachable BOOLEAN,
  supabase_connected BOOLEAN,
  queue_size INT4,
  realtime_queue_size INT4,
  backlog_queue_size INT4,
  buffer_used_bytes BIGINT,
  buffer_capacity_bytes BIGINT,
  upload_success_count INT4,
  upload_error_count INT4,
  upload_latency_ms INT4,
  ptz_active BOOLEAN,
  ptz_commands_executed INT4,
  ptz_current_position JSONB,
  -- v2.0: GPS y estado por cámara
  gps_lat FLOAT8,
  gps_lon FLOAT8,
  gps_fix BOOLEAN,
  cameras_active INT4,                 -- cámaras transmitiendo
  cameras_total INT4,                  -- cámaras configuradas
  per_camera JSONB,                    -- [{camera_id, input_type, connected, streaming, last_segment_at, error}]
  alerts JSONB,
  reported_at TIMESTAMPTZ DEFAULT now()
);
```

#### Tabla: `router_commands`

```sql
CREATE TABLE router_commands (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  router_id UUID NOT NULL REFERENCES routers(router_id) ON DELETE CASCADE,
  gateway_id UUID,
  user_id UUID REFERENCES users(user_id),
  command_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  priority TEXT DEFAULT 'normal',
  status TEXT DEFAULT 'pending',
  result JSONB,
  created_at TIMESTAMPTZ DEFAULT now(),
  processed_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ
);

-- Habilitar Realtime
ALTER PUBLICATION supabase_realtime ADD TABLE router_commands;
```

### 10.2 Modificaciones a Tablas Existentes

```sql
-- Agregar campos a camera_streams
-- NOTA v2.0: camera_streams.router_id establece la relación 1:N (un router → N cámaras).
ALTER TABLE camera_streams ADD COLUMN IF NOT EXISTS router_id UUID REFERENCES routers(router_id);
ALTER TABLE camera_streams ADD COLUMN IF NOT EXISTS s3_bucket TEXT;
ALTER TABLE camera_streams ADD COLUMN IF NOT EXISTS s3_prefix TEXT;        -- {user_id}/{router_id}/{camera_id}/
ALTER TABLE camera_streams ADD COLUMN IF NOT EXISTS stream_status TEXT DEFAULT 'inactive';
ALTER TABLE camera_streams ADD COLUMN IF NOT EXISTS last_segment_at TIMESTAMPTZ;
-- v2.0: fuente de entrada, vista en vivo, last-frame y orientación para el 3D
ALTER TABLE camera_streams ADD COLUMN IF NOT EXISTS input_type TEXT DEFAULT 'rtsp_ip';  -- 'rtsp_ip' | 'capture_card'
ALTER TABLE camera_streams ADD COLUMN IF NOT EXISTS live_view_only BOOLEAN DEFAULT false; -- true para feed DJI
ALTER TABLE camera_streams ADD COLUMN IF NOT EXISTS last_frame_url TEXT;     -- snapshot sin detección (producido por el Router)
ALTER TABLE camera_streams ADD COLUMN IF NOT EXISTS last_frame_at TIMESTAMPTZ;
ALTER TABLE camera_streams ADD COLUMN IF NOT EXISTS orient_azimuth FLOAT4;   -- 0–360
ALTER TABLE camera_streams ADD COLUMN IF NOT EXISTS orient_tilt FLOAT4;
ALTER TABLE camera_streams ADD COLUMN IF NOT EXISTS orient_fov FLOAT4;
ALTER TABLE camera_streams ADD COLUMN IF NOT EXISTS mount_height FLOAT4;
```

### 10.3 Políticas RLS

```sql
-- Routers
CREATE POLICY "Users can view own routers" ON routers FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Users can insert own routers" ON routers FOR INSERT WITH CHECK (auth.uid() = user_id);

-- Router Health
CREATE POLICY "Users can view own router health" ON router_health FOR SELECT 
  USING (router_id IN (SELECT router_id FROM routers WHERE user_id = auth.uid()));

-- Router Commands
CREATE POLICY "Users can create commands for own routers" ON router_commands FOR INSERT 
  WITH CHECK (router_id IN (SELECT router_id FROM routers WHERE user_id = auth.uid()));
CREATE POLICY "Users can view own router commands" ON router_commands FOR SELECT 
  USING (router_id IN (SELECT router_id FROM routers WHERE user_id = auth.uid()));
```

**Nota de privacidad (v2.0):** las coordenadas GPS (`routers.gps_lat/gps_lon`) son **dato sensible**. La visibilidad de la ubicación en el mapa 3D de GTI Satélites debe regirse por las políticas RLS de datos sensibles de Satélites (ver epic de roles/RLS de GTI Satélites), no exponiéndose en vistas públicas/no autorizadas.

---

## 10.4 Riesgos Técnicos Clave (v2.0)

| ID | Riesgo | Prob. | Impacto | Mitigación |
|----|--------|-------|---------|------------|
| RT1 | **Encoding desde capturadora no rinde** — codificar H.264/H.265 en RPi5 (sin encoder HW) puede saturar CPU; rompe el supuesto de solo-passthrough | Media | Alto | Benchmark temprano; usar encoder HW en RPi4; en RPi5 limitar resolución/fps o HEVC SW; marcar como riesgo #1 a validar antes del piloto |
| RT2 | **Multicámara degrada el Router** — N streams saturan ancho de banda de subida, escritura SD o RAM | Media | Alto | Límites por hardware/licencia (NFR12), reparto de recursos, validación en piloto, recomendación de ancho de banda (NFR11) |
| RT3 | **BOM/precio del Pro indefinido** — GPS + capturadora + RPi5 + switch elevan costo y pueden romper el margen | Alta | Medio | Definir BOM y precio del Pro como SKU aparte antes de ventas; tratar Base y Pro con márgenes independientes |
| RT4 | **Portabilidad RPi4/RPi5** — diferencias de codecs/encoder por hardware introducen rutas de código divergentes | Media | Medio | Abstracción de encoder con detección de board y *fallback*; matriz de pruebas en ambas placas |
| RT5 | **Exposición de GPS** — ubicación de instalaciones es dato sensible | Baja | Alto | RLS en Supabase; no exponer en vistas públicas de Satélites |

**Riesgo principal del MVP v2.0: RT1 (viabilidad de encoding por capturadora).**

---

## 11. Resultados del Checklist de PM

### Resumen

| Métrica | Resultado |
|---------|-----------|
| **Completitud del PRD** | **92%** |
| **Alcance MVP** | ✅ Apropiado |
| **Preparación para Arquitectura** | ✅ **READY** |

### Análisis por Categoría

| Categoría | Status |
|-----------|--------|
| 1. Definición del Problema y Contexto | ✅ PASS |
| 2. Definición del Alcance MVP | ✅ PASS |
| 3. Requerimientos de UX | ✅ PASS |
| 4. Requerimientos Funcionales | ✅ PASS |
| 5. Requerimientos No Funcionales | ✅ PASS |
| 6. Estructura de Épicas y Stories | ✅ PASS |
| 7. Guía Técnica | ✅ PASS |
| 8. Requerimientos Cross-Funcionales | ✅ PASS |
| 9. Claridad y Comunicación | ✅ PASS |

### Decisión Final

**✅ READY FOR ARCHITECT (v2.0 — alcance ampliado)**

El PRD de GTI Router v2.0 amplía el alcance a **dos SKUs (Base/Pro)** con **6 épicas** (las 4 originales + Multicámara/Fuentes y GPS/Orientación/Vista 3D). El conteo de stories de las épicas 5 y 6 (~14) se refinará en `bmad-create-epics-and-stories`. 

> **Nota v2.0:** los cambios de esta versión (capturadora con encoding, multicámara, GPS, contrato cross-sistema) son sustanciales y conviene **re-validar** el PRD (`bmad-validate-prd`) y crear la **arquitectura del Router** (aún inexistente) antes de generar epics/stories. El checklist de 92% corresponde a v1.0 y debe recalcularse.

---

## 12. Próximos Pasos

### Prompt para UX Expert

```
Revisar el PRD de GTI Router (prd-GTI_Router-2026-01-22.md) y diseñar:
1. Estructura del archivo de configuración YAML para MVP
2. Wireframes de la vista de Router en GTI Satélites
3. Flujo de usuario para configuración inicial por técnico
```

### Prompt para Arquitecto

```
Revisar el PRD de GTI Router v2.0 (prd-GTI_Router-2026-01-22.md) y crear 
el documento de arquitectura (aún inexistente) incluyendo:
1. Diagrama de componentes detallado (multicámara, fuentes de entrada, GPS)
2. Diseño de módulos Python con interfaces (abstracción VideoSource: RTSP/capturadora)
3. Estrategia de encoding HW/SW portable RPi4 + RPi5 (H.264/H.265) — validar RT1
4. Estrategia de testing con mocks de hardware (cámaras, capturadora V4L2, GPS)
5. Plan de integración con Supabase (esquema 1:N) y S3 (prefijo por cámara)
6. Contrato de integración cross-sistema Router ↔ Gateway ↔ Satélites
7. Consideraciones de seguridad para credenciales y privacidad de GPS (RLS)
```

### Pendientes de coordinación con otros productos

- **GTI Satélites:** actualizar epics/stories para el **panel 3D** del Router (posicionamiento por GPS, frustum por orientación, vista en vivo/last-frame **sin detección** diferenciada de las detecciones del Gateway, y direccionar la cámara desde el 3D). Tocar epics existentes (panel routers, último frame) y RLS de GPS.
- **GTI Gateway:** confirmar que consume streams multicámara (prefijo S3 por `camera_id`) y que la vista sin detección del Router coexiste con sus imágenes con detección.

---

*Documento generado por John (PM Agent) - BMAD Framework*  
*Fecha: 2026-01-22 · Última edición: 2026-06-01*  
*Versión: 2.0*
