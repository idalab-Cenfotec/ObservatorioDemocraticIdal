# SCRUM-29 — Apagado de la escritura del corpus a CSV

Cierre de la Fase 1 (SCRUM-6). Cuando esta tarea se aplique, PostgreSQL será la única fuente del corpus y el
corpus CSV quedará congelado como respaldo.

**Estado:** el cambio está preparado pero **no activado**. Sin la variable `ESCRIBIR_CSV`, el pipeline se
comporta exactamente igual que hoy. Se activa al cierre, cuando el equipo confirme que todo ha corrido normal.

## 1. Flujo actual (escritura dual)

```
GitHub Actions, todos los días 07:00 UTC (01:00 en Costa Rica) — pipeline.yml
│
├─ Grupos de scrapers ──► output/*.csv ──► artifacts por grupo
│
└─ Job "Consolidar corpus maestro"
     1. Restaura del caché corpus/ (último corpus CSV) y contingencia/
     2. corpus_updater.py (o corpus_builder.py si no hay corpus previo)
          a. reintenta lo que haya en contingencia/
          b. compara output/ contra el último corpus CSV por url_key  ──► artículos nuevos
          c. guarda corpus/corpus_observatorio_v{N}_{fecha}.csv        ◄── escritura CSV
          d. envía los nuevos al webhook de N8N en lotes de 200        ◄── escritura PostgreSQL
             · si el envío falla: contingencia/{fecha}_nuevos.csv y se reintenta mañana
     3. verificar_escritura_dual.py: cuadra enviados/recibidos/insertados, revisa contingencia/
        y compara lo de hoy en el CSV contra PostgreSQL (informativo)
     4. Sube corpus/ como artifact "corpus-maestro" (30 días) y guarda corpus/ + contingencia/ en el caché

N8N (VM, contenedor observatorio_n8n) — workflow "ingesta_desde_pipeline"
     valida el lote ──► INSERT ... ON CONFLICT DO NOTHING en articles (observatorio_postgres)
     índices únicos: URL normalizada, y fuente + texto + título
```

Lo que verificó SCRUM-28 (6 al 9 de octubre de 2026): cuatro corridas seguidas sin desviación, sin lotes en
contingencia, `articles` con 129,146 registros y todo el corpus CSV presente en PostgreSQL (salvo duplicados de
contenido y filas sin título que la limpieza descarta a propósito).

## 2. El interruptor `ESCRIBIR_CSV`

| Valor de la variable del repo | Qué pasa |
|---|---|
| no existe, o `true` | Igual que hoy: se escribe el corpus CSV y se envía a PostgreSQL |
| `false` | No se crea una versión nueva del corpus CSV. Los artículos nuevos se siguen enviando a PostgreSQL |

Archivos que cambian: `escritura_csv.py` (nuevo), `corpus_builder.py`, `corpus_updater.py`,
`verificar_escritura_dual.py`, `.github/workflows/pipeline.yml` y `tests/test_escritura_csv.py` (nuevo).

**Salvaguarda:** si el webhook de N8N no está configurado, el CSV se escribe igual aunque la variable diga
`false`, para que los artículos no se pierdan.

## 3. Procedimiento de apagado

### 3.1 Antes de apagar

- [ ] La última corrida programada del pipeline terminó en verde (la verificación de escritura dual en OK).
- [ ] No hay lotes en `contingencia/` (lo dice el resumen de la corrida: "Lotes en contingencia: 0").
- [ ] SCRUM-26 (deduplicación en `corpus_updater.py`) está cerrada o se confirmó que no la afecta este cambio.
- [ ] Este cambio ya está en `main` (pull request revisado y fusionado).

### 3.2 Conservar el corpus original (criterio de aceptación)

El corpus CSV completo no es un solo archivo: es la **base histórica** (`corpus_observatorio_v7_clean_20260703.csv`)
más las versiones diarias del pipeline (ver "Consolidar el Corpus" en el README). Por eso el respaldo se hace con
`corpus_consolidator.py`, que las une sin repetir artículos.

1. En GitHub, abrir **Actions → la última corrida en verde del pipeline → Artifacts** y descargar `corpus-maestro`.
2. Con la base histórica y ese zip, generar el corpus consolidado:
   ```bash
   python corpus_consolidator.py \
       --base corpus_observatorio_v7_clean_20260703.csv \
       --pipeline corpus-maestro.zip \
       --output corpus_original_AAAA-MM-DD/
   ```
3. Copiar a la VM (desde PowerShell, en la carpeta donde quedó el resultado):
   ```powershell
   scp -r corpus_original_AAAA-MM-DD devidalab@172.203.250.80:~/backups/
   ```
4. En la VM, sellar la copia con hashes y dejarla de solo lectura:
   ```bash
   cd ~/backups/corpus_original_AAAA-MM-DD
   cp ~/ruta/a/corpus-maestro.zip .            # también se guarda el zip original del artifact
   sha256sum * > SHA256SUMS
   chmod 444 * && chmod 555 .
   sha256sum -c SHA256SUMS                     # todo debe decir OK
   ```
5. Subir la misma carpeta (con `SHA256SUMS`) a la carpeta compartida del equipo en Drive. La copia de la VM vive
   en el mismo disco que la base; la de Drive protege si se pierde la VM.
6. Anotar en SCRUM-29 la ruta de las dos copias, la cantidad de filas y el hash del corpus consolidado.

> El repositorio es público: el corpus **no** se sube a GitHub (ni como archivo ni como release).

### 3.3 Apagar

1. En GitHub: **Settings → Secrets and variables → Actions → pestaña Variables → New repository variable**.
2. Nombre `ESCRIBIR_CSV`, valor `false`. Guardar.
3. Correr el pipeline a mano (**Actions → pipeline → Run workflow**) o esperar la corrida de la noche.
4. Revisar el resumen del job "Consolidar corpus maestro":
   - el log del updater dice `Escritura del corpus a CSV apagada (ESCRIBIR_CSV=false)`;
   - la verificación dice `Escritura a CSV apagada ... PostgreSQL es la única fuente` y termina en OK;
   - "Lotes en contingencia: 0".
5. En PostgreSQL, el conteo de `articles` subió con lo de ese día:
   ```bash
   docker exec observatorio_postgres psql -U devidalab -d observatorio -c "SELECT COUNT(*) FROM articles;"
   ```
   (ajustar el nombre de la base si es otro).

## 4. Cómo revertir

1. En **Settings → Variables**, borrar `ESCRIBIR_CSV` o cambiarla a `true`. No hace falta tocar código.
2. La siguiente corrida vuelve a escribir el corpus CSV: parte del último CSV congelado y le suma lo de ese día.
3. **Ojo con el hueco:** los artículos que entraron mientras el CSV estuvo apagado están solo en PostgreSQL. Si se
   necesita el CSV completo, hay que exportarlos de la base (por ejemplo con `\copy` filtrando por fecha de carga)
   y unirlos con `corpus_consolidator.py`.
4. Si el problema es N8N o PostgreSQL (no el CSV), revertir primero, y después seguir el procedimiento de
   restauración de `db/respaldo/README.md`.

## 5. Qué pasa con cada pieza cuando ya no haya CSV

| Pieza | Con `ESCRIBIR_CSV=false` | Riesgo / pendiente |
|---|---|---|
| `corpus_updater.py` | Sigue usando el **último corpus CSV como referencia congelada** para descartar URLs viejas. Ya no escribe versiones nuevas | Lo que los scrapers vuelvan a traer después de la fecha del congelamiento se reenvía y PostgreSQL lo descarta (`ya_existian` en el reporte). Es poco volumen porque los scrapers son incrementales |
| `corpus_builder.py` | Solo corre si no hay corpus en el caché. Envía lo del día a PostgreSQL sin escribir CSV | Ninguno: la base descarta duplicados |
| `contingencia/` | **No cambia.** Sigue siendo el respaldo de un envío fallido y se reintenta en la corrida siguiente. La verificación sigue fallando si queda algo pendiente | Vive en el caché de Actions (ver abajo) |
| Caché `corpus/` + `contingencia/` | Se sigue restaurando y guardando cada corrida con el mismo corpus congelado | GitHub borra los cachés que no se usan en 7 días. Si el pipeline se detiene más de una semana, se pierde el corpus de referencia (corre el builder, sin consecuencias) **y lo que hubiera en contingencia** |
| Artifact `corpus-maestro` | Se sigue subiendo el corpus congelado (30 días) | Ocupa espacio sin aportar; se puede retirar en la limpieza posterior |
| `verificar_escritura_dual.py` | Criterios 1 (envío cuadrado) y 2 (contingencia) siguen igual. La comparación informativa contra el CSV se omite | El job sigue en rojo ante una desviación real |
| `corpus_consolidator.py` y análisis que lean el CSV | Ya no tendrán datos nuevos | Quien analice el corpus debe leer de PostgreSQL |

Lo que **no** se apaga: los CSV por sitio que generan los scrapers en `output/`. Son el transporte hacia el
webhook de N8N, no el corpus.

## 5b. Exportar el corpus desde PostgreSQL

Para quien necesite el corpus fuera de la base (Metodología, papers), se exporta desde la VM:

```bash
mkdir -p ~/exports
docker exec observatorio_postgres psql -U devidalab -d <base> \
  -c "\copy (SELECT * FROM articles ORDER BY publication_date) TO STDOUT WITH CSV HEADER" \
  | gzip > ~/exports/corpus_postgres_$(date +%F).csv.gz
zcat ~/exports/corpus_postgres_$(date +%F).csv.gz | wc -l     # filas + 1 (encabezado)
```

`<base>` es el nombre de la base del contenedor (`docker exec observatorio_postgres psql -U devidalab -l` las
lista). El respaldo de la base en sí es el `pg_dump` diario descrito en `db/respaldo/README.md`.

### Limpieza posterior (Fase 2, no es parte de esta tarea)

- Usar el endpoint de N8N de URLs conocidas (`N8N_URLS_CONOCIDAS_URL`, el mismo que usa la verificación) como
  referencia del updater en lugar del CSV congelado, y entonces quitar el caché de `corpus/`.
- Retirar del workflow el caché de `corpus/` y el artifact `corpus-maestro`. Sin caché, cada corrida usa
  `corpus_builder.py`, que con el CSV apagado envía todo lo del día y PostgreSQL descarta los repetidos
  (`ON CONFLICT DO NOTHING` + índices únicos). Ojo: el caché también guarda `contingencia/`, así que hay que
  conservar esa ruta o moverla a otro lado antes de quitarlo.
- Retirar `db/exclusiones_urls.txt` (solo sirve mientras exista el corpus CSV), la escritura CSV del builder y del
  updater, y la comparación informativa de la verificación.

## 6. Checklist de aceptación

- [ ] Corpus original consolidado, con `SHA256SUMS`, en la VM (solo lectura) y en Drive
- [ ] `sha256sum -c SHA256SUMS` en OK antes y después del apagado
- [ ] Variable `ESCRIBIR_CSV=false` creada y una corrida en verde con el CSV apagado
- [ ] `articles` en PostgreSQL creció con lo del día y no hay lotes en contingencia
- [ ] Rutas, conteo y hash anotados en SCRUM-29; tarea movida a "En revisión"
