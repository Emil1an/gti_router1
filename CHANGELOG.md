# Changelog - GTI Router v2.0 (Core Engine)

Este documento registra de manera detallada todas las implementaciones arquitectónicas, corrección de errores (bugs) y decisiones de diseño para el sistema base (Épicas 1 a 6) ejecutado sobre Raspberry Pi 4.

## [1.0.0] - 2026-06-16

###  Implementaciones Principales (Features)

**Épica 1: Arquitectura de Búfer Local FIFO (Resiliencia Offline)**
- **Implementado:** Motor de base de datos local embebido usando `SQLite3`.
- **Detalle Técnico:** Se creó un mecanismo de *fallback* automático. Cuando el router detecta pérdida de conexión a internet (celular/satelital), los *snapshots* de video y los reportes de salud (`router_health`) se encolan localmente. Se implementó un sistema de rotación FIFO (First-In, First-Out) para evitar la saturación de la memoria MicroSD, purgando los registros más antiguos si se supera el límite de almacenamiento asignado.

**Épica 2: Orquestador Multicámara Asíncrono**
- **Implementado:** Gestor de flujos de video concurrente aislado por hilos (Threading/Multiprocessing).
- **Detalle Técnico:** Capacidad de consumir simultáneamente RTSP streams vía Ethernet (Cámaras IP) y flujos físicos locales vía interfaces CSI/HDMI. Se implementó decodificación de video en memoria optimizada para no sobrecargar la CPU ARM de la Raspberry Pi.

**Épica 3: Cliente de Almacenamiento Volátil (AWS S3)**
- **Implementado:** Módulo de transmisión de imágenes de telemetría a buckets de AWS S3.
- **Detalle Técnico:** Para optimizar el ancho de banda crítico en operaciones de campo, el sistema **no transmite video en vivo**. En su lugar, el orquestador extrae periódicamente el último frame (`last_frame`), lo comprime y lo inyecta en la nube. Incluye validación de integridad (MD5/ETag) tras la subida.

**Épica 4: Integración del Cliente de Telemetría (Supabase/PostgreSQL)**
- **Implementado:** Sincronización bidireccional entre el hardware físico y la nube de GTI Satélites.
- **Detalle Técnico:** Script de "latidos" (*Heartbeat*) que reporta la salud del sistema (temperatura de CPU, uso de RAM, estado de los enlaces de red) hacia la tabla `router_health`. Lee asíncronamente las configuraciones de usuario desde la nube para aplicarlas al hardware.

**Épica 5: Subsistema Seguro PTZ (Pan-Tilt-Zoom)**
- **Implementado:** Controlador de motores de cámaras mediante el protocolo estándar ONVIF y WebSockets (Supabase Realtime).
- **Detalle Técnico:** Seguridad perimetral robusta integrada en el router físico:
  1. **Límite de Tasa (Rate Limiting):** Ventana móvil estricta de 60 comandos por minuto para evitar sobrecalentamiento de los servomotores de la cámara.
  2. **Protección Anti-Repetición (Replay-Attack):** Filtro de caducidad temporal (Time-to-Live de 30 segundos) validado mediante reclamos atómicos en base de datos. Si un comando viejo es interceptado y reenviado, el router lo ignora.

**Épica 6: Parser de Telemetría GPS en Tiempo Real**
- **Implementado:** Demonio de lectura de puerto serie (UART/USB) para receptores GPS físicos.
- **Detalle Técnico:** Decodificador nativo de tramas NMEA. Extrae específicamente las sentencias `$GPRMC` para obtener latitud, longitud, velocidad y vector de dirección (*heading*). Los datos espaciales son interpolados y enviados a la tabla `routers` en Supabase para habilitar el Mapa 3D del frontend.

---

###  Corrección de Errores Críticos (Bug Fixes)

**1. Excepción de Desconexión de Red Intermitente (Subidas a AWS S3)**
- **Síntoma:** El hilo principal del orquestador sufría un *crash* fatal (cerrando todo el programa) cuando el módem 4G/LTE de la Raspberry perdía señal justo en el momento exacto en que se estaba haciendo un `PUT` a AWS S3.
- **Causa Raíz:** Ausencia de manejo de excepciones específicas de red (`ConnectionResetError` / `Timeout`) en la librería `boto3`.
- **Solución:** Se envolvió la rutina de subida en un bloque `try-except`. Se implementó un algoritmo de **Exponential Backoff** (reintentos con esperas de 2s, 4s, 8s). Si el envío falla tras 3 intentos, el frame se delega de forma segura al búfer local SQLite y el hilo principal continúa sin detener las demás cámaras.

**2. Saturación de Descriptores de Archivo (Socket Leak / `TIME_WAIT`)**
- **Síntoma:** Tras varias horas de funcionamiento, el router dejaba de leer el video de las cámaras IP y el sistema operativo arrojaba el error `Too many open files`.
- **Causa Raíz:** En entornos de red inestables, las conexiones TCP hacia el puerto 554 (RTSP) de las cámaras se perdían, pero los *sockets* a nivel de sistema operativo en Python nunca se cerraban explícitamente, acumulándose en estado `TIME_WAIT`.
- **Solución:** Se reestructuró el gestor de red implementando "Manejadores de Contexto" (`with` statements) y bloques `finally`. Ahora, independientemente de cómo falle la conexión, se fuerza un `socket.close()` y la liberación de los recursos de memoria.

**3. Bloqueo de Políticas de Ejecución de Entorno (Windows PowerShell)**
- **Síntoma:** Durante el desarrollo y validación local de integración web, la compilación fallaba arrojando `PSSecurityException`.
- **Causa Raíz:** Las políticas predeterminadas de Windows (`Restricted Execution Policy`) bloqueaban la ejecución del script `npm.ps1`.
- **Solución:** Se migró el estándar del equipo de desarrollo local hacia *Command Prompt* (CMD) y se documentó el uso de `Set-ExecutionPolicy RemoteSigned` para futuras configuraciones de entorno de programadores de la empresa.

---

###  Cambios Estructurales (Infraestructura / DevOps)

- **Migración a GitFlow (Ramas Aisladas):** Se crearon formalmente las ramas `testing` y `preproduccion` en el repositorio remoto. El flujo de trabajo ahora prohíbe empujes directos a `main` para preservar la estabilidad de la línea base operativa.
