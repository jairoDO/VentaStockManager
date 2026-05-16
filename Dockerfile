# Imagen para el entorno de desarrollo local.
# No se usa en Render (Render usa su buildpack de Python directo desde
# requirements.txt). Esto es solo para `docker-compose up` en tu Mac.

FROM python:3.11.4-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Dependencias del sistema:
#   build-essential + libpq-dev → para compilar psycopg2 contra Postgres
#   default-libmysqlclient-dev pkg-config → para mysqlclient (legacy de PA)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar deps primero (capa cacheable)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# El código se monta como volumen desde docker-compose.yml, no hace falta
# COPY acá durante dev. Solo aseguramos un default para que la imagen
# funcione si se usa sin volumen.
COPY . .

# manage.py vive en VentaStockManager/, no en la raíz
WORKDIR /app/VentaStockManager

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
