#!/usr/bin/env bash
# Copia los datos del Postgres LOCAL (docker compose db) a una Neon DB.
#
# Mucho mas rapido que `manage.py loaddata` porque usa pg_dump+psql con
# COPY interno, no INSERT fila-por-fila.
#
# Uso:
#   ./scripts/copy-local-to-neon.sh staging
#   ./scripts/copy-local-to-neon.sh production
#
# Requiere:
#   - docker compose up corriendo (db container)
#   - .env.<env> con NEON_URL=postgresql://... (URL DIRECT, no pooler)
#   - Tablas ya existentes en Neon (ej. via Render deploy que corrio migrate)
#
# Lo que hace:
#   1. Trunca todas las tablas en Neon (sin tocar django_migrations)
#   2. pg_dump --data-only --disable-triggers del local
#   3. Pipe a psql contra Neon
#   4. Reset de sequences

set -euo pipefail

ENV_NAME="${1:?Falta: staging | production}"
ENV_FILE=".env.${ENV_NAME}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "❌ No encontré $ENV_FILE. Crealo con: NEON_URL=postgresql://..."
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"
: "${NEON_URL:?$ENV_FILE no define NEON_URL}"

if [[ "$NEON_URL" == *"-pooler."* ]]; then
  echo "⚠️  Estas usando la URL pooler de Neon. Para esta operacion usa la URL DIRECT (sin '-pooler')."
  echo "    Te conviene cambiar .env.${ENV_NAME} antes de seguir."
  read -p "Seguir igual? (s/N) " confirm
  [[ "$confirm" =~ ^[sS]$ ]] || exit 1
fi

echo "→ Verificando que docker compose esta corriendo..."
DB_CID=$(docker compose ps -q db)
[[ -z "$DB_CID" ]] && { echo "❌ container db no esta corriendo. docker compose up -d"; exit 1; }

# Tablas para no truncar (las de Django internas)
EXCLUDE="('django_migrations','django_content_type','auth_permission')"

echo "→ Truncando tablas en Neon ${ENV_NAME} (excepto migrations/content_type/permissions)..."
docker compose exec -T db psql "$NEON_URL" <<SQL
DO \$\$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
        AND tablename NOT IN ${EXCLUDE}
    LOOP
        EXECUTE 'TRUNCATE TABLE ' || quote_ident(r.tablename) || ' RESTART IDENTITY CASCADE';
    END LOOP;
END \$\$;
SQL

echo "→ Copiando datos local → Neon (pg_dump | psql)..."
docker compose exec -T db bash -c "
  pg_dump --data-only --disable-triggers \
    --exclude-table=django_migrations \
    --exclude-table=django_content_type \
    --exclude-table=auth_permission \
    'postgresql://ventastock:ventastock_dev@db:5432/ventastock' \
  | psql '$NEON_URL'
"

echo "→ Reseteando sequences de Postgres..."
docker compose exec -T web bash -c "
  python manage.py sqlsequencereset auth articulo cliente compra factura_config vendedor venta django_q 2>/dev/null
" | docker compose exec -T db psql "$NEON_URL" > /dev/null

echo "✅ Listo. Verifica con:"
echo "   docker compose exec -e DATABASE_URL='\$NEON_URL' web python manage.py shell -c 'from venta.models import Venta; print(Venta.objects.count())'"
