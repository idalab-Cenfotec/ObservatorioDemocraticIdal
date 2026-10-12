#!/usr/bin/env bash
# Exportación diaria de los workflows de N8N (solo los workflows: no llevan secretos).
# El volcado de PostgreSQL lo hace el respaldo de la tarea SCRUM-19 (backup_db.sh, cron del usuario devidalab),
# con retención diaria/semanal/mensual. Este script cubre lo que ese no incluye.
#
# Se instala en la VM como /opt/observatorio/respaldo/respaldo_n8n_workflows.sh y lo corre cron (ver README.md).
# Cuando backup_db.sh incorpore la exportación de workflows, este script y su cron se pueden retirar.
set -euo pipefail

DESTINO="${DESTINO:-/var/backups/observatorio}"
DIAS_RETENCION="${DIAS_RETENCION:-30}"
CONTENEDOR_N8N="${CONTENEDOR_N8N:-observatorio_n8n}"

sello="$(date -u +%Y%m%d_%H%M%S)"
parcial="$DESTINO/n8n_workflows_${sello}.json.parcial"
final="$DESTINO/n8n_workflows_${sello}.json"

mkdir -p "$DESTINO"
chmod 700 "$DESTINO"
echo "[$(date -u +%FT%TZ)] inicio exportación de workflows -> $final"

# La CLI imprime una línea de aviso antes del JSON; se descarta lo que no sea el arreglo
docker exec "$CONTENEDOR_N8N" n8n export:workflow --all 2>/dev/null | sed -n '/^\[/,$p' > "$parcial"

# Debe ser JSON válido y traer al menos un workflow
python3 - "$parcial" <<'PY'
import json, sys
datos = json.load(open(sys.argv[1], encoding="utf-8"))
assert isinstance(datos, list) and datos, "la exportación no trae workflows"
print("workflows exportados:", ", ".join(w.get("name", "?") for w in datos))
PY

mv "$parcial" "$final"
chmod 600 "$final"

# Retención: solo los más recientes
ls -1t "$DESTINO"/n8n_workflows_*.json 2>/dev/null | tail -n +$((DIAS_RETENCION + 1)) | xargs -r rm -f --
echo "[$(date -u +%FT%TZ)] exportación OK; copias guardadas: $(ls -1 "$DESTINO"/n8n_workflows_*.json | wc -l)"
