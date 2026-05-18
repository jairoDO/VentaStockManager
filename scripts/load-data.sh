#!/usr/bin/env bash
# Carga un dump.json en una DB Neon (staging o production), usando el
# container web de docker-compose como "runtime". No depende de variables
# de shell — la URL la lee de .env.staging o .env.production (gitignored).
#
# Uso:
#   ./scripts/load-data.sh staging ~/Downloads/dump.json
#   ./scripts/load-data.sh production ~/Downloads/dump.json
#
# Requiere:
#   - docker compose up corriendo
#   - .env.<env> en la raíz del repo con NEON_URL=<url completa>

set -euo pipefail

ENV_NAME="${1:?Falta: staging | production}"
DUMP_PATH="${2:?Falta: ruta al dump.json}"
ENV_FILE=".env.${ENV_NAME}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ No encontré $ENV_FILE. Crealo con: NEON_URL=postgresql://..."
  exit 1
fi
if [[ ! -f "$DUMP_PATH" ]]; then
  echo "❌ No encontré el dump: $DUMP_PATH"
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"
: "${NEON_URL:?$ENV_FILE no define NEON_URL}"

CID=$(docker compose ps -q web)
[[ -z "$CID" ]] && { echo "❌ Container web no esta corriendo. docker compose up -d"; exit 1; }

echo "→ Copiando $DUMP_PATH al container..."
docker cp "$DUMP_PATH" "$CID:/tmp/dump.json"

echo "→ Aplicando migraciones a Neon $ENV_NAME..."
docker compose exec -e DATABASE_URL="$NEON_URL" web python manage.py migrate --no-input

echo "→ Cargando datos (puede tardar 10-30 min)..."
docker compose exec -e DATABASE_URL="$NEON_URL" web python manage.py loaddata /tmp/dump.json

echo "✅ Listo"
