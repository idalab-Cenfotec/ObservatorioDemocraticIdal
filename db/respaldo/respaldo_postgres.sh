#!/usr/bin/env bash
# Respaldo diario de PostgreSQL (observatorio_db) y de los workflows de N8N.
# Se instala en la VM como /opt/observatorio/respaldo/respaldo_postgres.sh y lo corre cron (ver instalar_respaldo.md).
#
#  - pg_dump en formato personalizado (-Fc, comprimido) desde el contenedor observatorio_postgres
#  - se verifica que el archivo se pueda leer (pg_restore --list) antes de darlo por bueno
#  - se conservan los últimos DIAS_RETENCION respaldos; los demás se borran
#  - si algo falla, el script sale con código distinto de cero y deja el motivo en el log
set -euo pipefail

DESTINO="${DESTINO:-/var/backups/observatorio}"
DIAS_RETENCION="${DIAS_RETENCION:-14}"
CONTENEDOR_DB="${CONTENEDOR_DB:-observatorio_postgres}"
CONTENEDOR_N8N="${CONTENEDOR_N8N:-observatorio_n8n}"
USUARIO_DB="${USUARIO_DB:-devidalab}"
NOMBRE_DB="${NOMBRE_DB:-observatorio_db}"

sello="$(date -u +%Y%m%d_%H%M%S)"
parcial="$DESTINO/observatorio_db_${sello}.dump.parcial"
final="$DESTINO/observatorio_db_${sello}.dump"

mkdir -p "$DESTINO"
chmod 700 "$DESTINO"
echo "[$(date -u +%FT%TZ)] inicio respaldo -> $final"

docker exec "$CONTENEDOR_DB" pg_dump -U "$USUARIO_DB" -d "$NOMBRE_DB" -Fc -Z 6 > "$parcial"

# El archivo debe ser legible y tener la tabla principal
if ! docker exec -i "$CONTENEDOR_DB" pg_restore --list < "$parcial" | grep -q "TABLE DATA public articles"; then
  echo "ERROR: el respaldo no contiene la tabla articles; se descarta" >&2
  rm -f "$parcial"
  exit 1
fi
mv "$parcial" "$final"
chmod 600 "$final"

# Workflows de N8N (las credenciales no se exportan; la clave de cifrado de N8N se respalda aparte)
docker exec "$CONTENEDOR_N8N" n8n export:workflow --all 2>/dev/null | sed -n '/^\[/,$p' > "$DESTINO/n8n_workflows_${sello}.json" || true
chmod 600 "$DESTINO"/n8n_workflows_${sello}.json 2>/dev/null || true

# Retención: solo los más recientes
ls -1t "$DESTINO"/observatorio_db_*.dump 2>/dev/null | tail -n +$((DIAS_RETENCION + 1)) | xargs -r rm -f --
ls -1t "$DESTINO"/n8n_workflows_*.json 2>/dev/null | tail -n +$((DIAS_RETENCION + 1)) | xargs -r rm -f --

tam="$(du -h "$final" | cut -f1)"
echo "[$(date -u +%FT%TZ)] respaldo OK ($tam); respaldos guardados: $(ls -1 "$DESTINO"/observatorio_db_*.dump | wc -l); disco libre: $(df -h "$DESTINO" | awk 'NR==2{print $4}')"
