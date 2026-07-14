# PLAN_FEATURE.md
## Objetivo: Implementar monitoreo de temperatura del sistema
- Tarea: Crear un nuevo módulo asíncrono que lea la temperatura del CPU de la Raspberry Pi cada 60 segundos.
- Acción: Insertar el dato (temperatura, marca de tiempo) en la base de datos local (SQLite) y, si hay conexión, reportarlo a Supabase mediante una tarea en segundo plano.
- Restricción 1: No bloquear el hilo principal de captura de video.
- Restricción 2: El módulo debe ser robusto (manejar errores de lectura sin detener el router).
- Criterios de Aceptación:
  1. El módulo debe estar integrado en la lógica de `asyncio` existente.
  2. Debe haber un nuevo endpoint en la API local (`src/web/local_api.py`) que permita consultar la temperatura actual.
  3. Los tests unitarios deben validar que la lectura no bloquea el event loop.
