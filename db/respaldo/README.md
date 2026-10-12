# Respaldos de PostgreSQL y de N8N

## Quién respalda qué

| Qué | Script | Quién lo corre | Retención |
|---|---|---|---|
| Base de datos PostgreSQL (`pg_dump` en SQL comprimido) | `backup_db.sh` en `/home/devidalab/backups/` (tarea SCRUM-19) | cron del usuario `devidalab`, 02:00 UTC | 7 diarios, semanales de 30 días, mensuales de 180 días |
| Workflows de N8N (JSON, sin secretos) | `respaldo_n8n_workflows.sh` (este directorio) | cron de root, 12:30 UTC | 30 copias |

El respaldo de la base es el de la tarea SCRUM-19; este directorio solo agrega lo que ese no cubre. Cuando `backup_db.sh`
incorpore la exportación de workflows, `respaldo_n8n_workflows.sh` y su cron se retiran.

## Instalación del respaldo de workflows en la VM

```bash
sudo install -d -m 700 /opt/observatorio/respaldo /var/backups/observatorio
sudo install -m 750 respaldo_n8n_workflows.sh /opt/observatorio/respaldo/
echo '30 12 * * * root /opt/observatorio/respaldo/respaldo_n8n_workflows.sh >> /var/log/observatorio_respaldo.log 2>&1' \
  | sudo tee /etc/cron.d/observatorio-respaldo
```

## Restaurar la base (respaldo de SCRUM-19)

```bash
# en una base temporal, para revisar antes de tocar producción
docker exec observatorio_postgres createdb -U devidalab restauracion
zcat /home/devidalab/backups/postgres/daily/backup_observatorio_AAAA-MM-DD_HHMMSS.sql.gz \
  | docker exec -i observatorio_postgres psql -U devidalab -d restauracion
```

## Restaurar workflows de N8N

```bash
docker cp /var/backups/observatorio/n8n_workflows_AAAAMMDD_HHMMSS.json observatorio_n8n:/tmp/wf.json
docker exec observatorio_n8n n8n import:workflow --input=/tmp/wf.json
```

## Lo que NO cubre

- **Credenciales y clave de cifrado de N8N.** La exportación de credenciales sale cifrada y solo sirve con la clave
  (`encryptionKey` del archivo `config` del volumen `observatorio_n8n_data`). La clave debe guardarse aparte, en el
  gestor de contraseñas, nunca junto a las copias.
- **Los respaldos viven en el mismo disco de la VM:** protegen contra borrados y errores de datos, no contra perder la VM.
  Para eso hace falta un snapshot del disco en Azure o una copia a otro almacenamiento.
