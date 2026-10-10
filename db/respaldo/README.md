# Respaldo automático de PostgreSQL

`respaldo_postgres.sh` hace un `pg_dump` completo de `observatorio_db`, comprueba que el archivo se pueda leer y
conserva los últimos 14. También exporta los workflows de N8N.

Instalación en la VM (ya hecha el 2026-10-10):

```bash
sudo install -d -m 700 /opt/observatorio/respaldo /var/backups/observatorio
sudo install -m 750 respaldo_postgres.sh /opt/observatorio/respaldo/
echo '30 12 * * * root /opt/observatorio/respaldo/respaldo_postgres.sh >> /var/log/observatorio_respaldo.log 2>&1' \
  | sudo tee /etc/cron.d/observatorio-respaldo
```

Corre todos los días a las 12:30 UTC (6:30 a. m. en Costa Rica), después de que termina el pipeline.

## Restaurar

```bash
# en una base temporal, para revisar antes de tocar producción
docker exec observatorio_postgres createdb -U devidalab restauracion
docker exec -i observatorio_postgres pg_restore -U devidalab -d restauracion --no-owner < /var/backups/observatorio/observatorio_db_AAAAMMDD_HHMMSS.dump
```

## Lo que NO cubre

- La clave de cifrado de N8N (`N8N_ENCRYPTION_KEY`) y sus credenciales: respaldarlas aparte.
- Los respaldos viven en el mismo disco de la VM: protegen contra borrados y errores de datos, no contra perder la VM.
  Para eso hay que copiarlos fuera (snapshot de Azure o copia a otro almacenamiento).
