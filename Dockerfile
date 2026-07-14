# 1. Base ligera de Python
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# 2. Dependencias del sistema
RUN apt-get update && apt-get install -y \
  ffmpeg \
  libsm6 \
  libxext6 \
  gcc \
  g++ \
  pkg-config \
  libsystemd-dev \
  libffi-dev \
  && rm -rf /var/lib/apt/lists/*

# 3. Directorio de trabajo
WORKDIR /app

# 4. Copiar todo el código fuente
COPY . /app

# 5. Instalar el proyecto
RUN pip install --upgrade pip
RUN pip install .

# 6. Crear carpetas necesarias (¡AQUÍ ESTÁ EL CAMBIO!)
# Agregamos /var/lib/gti-router para que SQLite pueda escribir ahí
RUN mkdir -p /app/data /app/logs /var/lib/gti-router

# 7. Variables de entorno
ENV PYTHONPATH=src
ENV ROUTER_CONFIG=/app/config/router.yaml

# 8. Exponer el puerto de la Mini-API local (coincide con console.port)
EXPOSE 8770

# 9. Comando exacto de arranque
CMD ["python", "-m", "main"]
