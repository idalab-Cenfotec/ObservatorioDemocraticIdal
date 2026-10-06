# Observatorio Democrático — Pipeline de Datos

Sistema de scraping modular para la recolección de noticias costarricenses.

Cumple con el **Estándar de Estructura y Validación de Datos v1.0**.

---

# Estructura del Proyecto

```text
ScrapingObservatorio/
│
├── main.py
├── requirements.txt
├── Dockerfile
├── README.md
│
├── scrapers/
│   ├── __init__.py
│   ├── base_scraper.py
│   ├── elperiodicocr.py
│   ├── teletica.py
│   ├── repretel.py
│   ├── acontecer_cr.py
│   ├── observatorio.py
│   ├── observatorio_adapter.py
│   └── ...
│
├── output/
│   └── *.csv
│
└── logs/
    ├── *.log
    ├── *_discarded.csv
    └── execution_report_*.json
```

---

# Requisitos

## Ejecución Local

* Python 3.12+
* Git

Instalar dependencias:

```bash
pip install -r requirements.txt
```

Instalar Chromium para Playwright:

```bash
python -m playwright install chromium
```

---

# Ejecución con Docker Principal

## Instalar Docker Desktop

Verificar instalación:

```bash
docker --version
docker compose version
```

### Construir la imagen

```bash
docker build -t observatorio .
```

### Listar scrapers disponibles

```bash
docker run --rm observatorio --list
```

### Ejecutar todos los scrapers

```bash
docker run --rm observatorio
```

### Ejecutar un scraper específico

```bash
docker run --rm observatorio --only elperiodicocr
```

### Ejecutar varios scrapers

```bash
docker run --rm observatorio --only elperiodicocr acontecer_cr
```

### Ejecutar en modo prueba Solo para los scrapers de Ali

```bash
docker run --rm observatorio --only noticiasenlineacr --test
```

# Uso Local

## Ejecutar todos los scrapers

```bash
python main.py
```

## Ejecutar un scraper específico

```bash
python main.py --only elperiodicocr
```

## Ejecutar varios scrapers

```bash
python main.py --only elperiodicocr teletica repretel
```

## Listar scrapers disponibles

```bash
python main.py --list
```

## Modo prueba

```bash
python main.py --only noticiasenlineacr --test
```

---

# Schema Obligatorio (v1.0)

| Columna          | Tipo     | Descripción                  |
| ---------------- | -------- | ---------------------------- |
| source           | string   | Nombre normalizado del medio |
| url              | string   | URL única del artículo       |
| title            | string   | Título limpio                |
| publication_date | datetime | Fecha de publicación         |
| scraping_date    | datetime | Fecha de scraping            |
| section          | string   | Sección del artículo         |
| full_text        | string   | Texto completo               |
| language         | string   | Idioma detectado             |

### Formato CSV

* Separador: `|`
* Codificación: UTF-8
* Valores nulos: `NULL`

---

# Agregar un Nuevo Scraper

## Opción — Scraper 

Crear:

```text
scrapers/nuevomedio.py (iportante nombre junto y sin espacios extencion.py)
```

Registrar en `SCRAPERS_REGISTRY`:

```python
SCRAPERS_REGISTRY = {
    ...
    "nuevo_medio": (
        "scrapers.nuevo_medio",
        "NuevoMedioScraper"
    )
}
```

---

# Modo incremental, topes de tiempo y protecciones

Los scrapers no vuelven a abrir lo que ya está en PostgreSQL ni recorren todo el archivo de cada sitio todos los días:

* **Modo incremental:** al arrancar, `BaseScraper` pide a N8N (`GET /webhook/urls-conocidas?source=<fuente>`) las URLs ya cargadas. Omite esas notas y deja de paginar tras 3 páginas seguidas solo con URLs conocidas. Sin `N8N_URLS_CONOCIDAS_URL` y `N8N_WEBHOOK_TOKEN` (o con N8N caído) funciona como antes. `SCRAPER_MODO_COMPLETO=1` (entrada `modo_completo` del workflow) recorre todo el listado sin cortar, pero sigue abriendo solo lo nuevo, para traer lo atrasado.
* **Tope de tiempo por scraper:** `scrapers/topes.py` (`TOPES_MINUTOS`). Al agotarse, el scraper deja de visitar notas y devuelve lo reunido; el listado usa como máximo la mitad. Un scraper sin entrada usa `SCRAPER_MAX_MINUTOS` del grupo.
* **Límite duro por nota (2 min):** una página colgada se omite en vez de frenar todo (`BaseScraper.articulo_seguro`).
* **Límite por sección (20 min) y cierre seguro de página:** `seccion_segura` y `cerrar_pagina`; evitan que un `page.close()` o una sección que no vuelve detengan el job hasta su timeout.
* **Guardado parcial cada 100 notas:** `BaseScraper.checkpoint` deja el CSV del día en `output/`; si el job se cancela, la consolidación recoge lo ya reunido.

Para un scraper nuevo basta usar `self.debe_omitir(url)`, `CorteIncremental`, `self.tiempo_agotado()`, `await self.articulo_seguro(...)`, `await cerrar_pagina(page)` y `self.checkpoint(records)` (ver `scrapers/sinartdigital.py` como modelo) y agregar su tope en `scrapers/topes.py`.

* **Verificación diaria de la escritura dual:** `corpus_updater.py` deja `logs/envio_n8n_<fecha>.json` con lo que mandó a PostgreSQL y lo que N8N contestó; al final del pipeline `verificar_escritura_dual.py` exige que cuadre (recibidas = enviadas = insertadas + ya existentes + rechazadas) y que no haya lotes en `contingencia/`. Si no cuadra, el job `consolidar_corpus` queda en rojo y el resumen del run dice por qué. Las notas rechazadas por N8N son aviso, y el conteo por fuente de URLs del CSV sin fila en la base es solo informativo (incluye duplicados que los índices rechazan a propósito).
* **Notas eliminadas a propósito:** `db/exclusiones_urls.txt` lista las URLs que se borraron de PostgreSQL (duplicados, basura, fuentes extranjeras). `corpus_updater.py` las saca del corpus CSV y no las vuelve a enviar, para que CSV y base no se desvíen. Si se borran más notas de la base, se agregan ahí (`fuente<TAB>url_key`).
* **Recuperar atrasado de un sitio nuevo:** el modo incremental corta al ver páginas ya cargadas, así que un sitio cuya primera corrida quedó cortada por el tope no sigue hacia atrás solo. Se lanza el workflow a mano con `modo_completo = true` (los scrapers WordPress/Joomla usan 45 min en ese modo).

---


# Validaciones Automáticas

Realizadas por `BaseScraper`.

* source automático.
* scraping_date automático.
* language automático.
* eliminación de URLs duplicadas.
* descarte de artículos sin fecha.
* descarte de artículos vacíos.
* generación automática de logs de descartados.

Los registros descartados se almacenan en:

```text
logs/source_YYYYMMDD_discarded.csv
```

# Validación del Output Final

Para validar los CSV de salida generados por el pipeline, usa `output_cleaner.py`.

Comprueba:
* columnas obligatorias
* valores nulos o vacíos
* URLs duplicadas
* formato correcto de `publication_date` y `scraping_date`
* ausencia de HTML en `title`, `section` y `full_text`
* longitud mínima de `full_text`

Ejemplos:

```bash
python output_cleaner.py output/
python output_cleaner.py output/lareaccioncr_20260516.csv
```

El reporte se guarda automáticamente en el directorio `logs/`.

Para **corregir** un CSV (fechas, HTML, secciones vacías, filas sin título, duplicados) agrega `--fix`:

```bash
python output_cleaner.py corpus/corpus_observatorio_v7.csv --fix --inferred-out fechas_inferidas.csv
```

Reconoce fechas como `2026-05-30 10:15:00`, `2026-07-03T07:09:26.000Z` (se convierte a hora de Costa Rica),
`mayo 30, 2026`, `Ago 10, 2026`, `30 de mayo de 2026` y relativas (`Hace 53 minutos`, `Hace 2 horas`).
Si una fecha no se puede interpretar (por ejemplo `Sin fecha`) se usa `scraping_date`, y con
`--inferred-out` se guarda la lista de esas URLs para marcarlas como `date_source = 'inferido'`.

---

# Consolidar el Corpus (Fase 1)

Las versiones diarias del pipeline (`corpus_observatorio_v{N}_{YYYYMMDD}.csv`) **no son acumulativas**, así que
el corpus completo es la unión de todas más el corpus base histórico. `corpus_consolidator.py` la reconstruye,
sin repetir artículos, y le aplica `output_cleaner.py --fix`:

```bash
python corpus_consolidator.py \
    --base corpus_observatorio_v7_clean_20260703.csv \
    --pipeline corpus-maestro.zip \
    --output corpus_consolidado/
```

`--pipeline` acepta un directorio o el `.zip` del artefacto `corpus-maestro` de GitHub Actions.
Deja en `--output` el corpus limpio (`corpus_consolidado_clean_<fecha>.csv`), la lista de fechas inferidas y un
reporte JSON. Con `--dry-run` solo cuenta. Los CSV pesan cientos de MB y no se suben al repositorio.

---

# Archivos Generados

## Output

```text
output/
├── teletica_20260527.csv
├── repretel_20260527.csv
└── ...
```

## Logs

```text
logs/
├── teletica.log
├── teletica_20260527_discarded.csv
├── execution_report_20260527_140000.json
└── ...
```

---

# Notas Especiales por Scraper

## La Revista

Por defecto limita la cantidad de páginas por sección para evitar ejecuciones excesivamente largas.

Para obtener el corpus histórico completo:

```python
MAX_PAGES_PER_SECTION = 319
```

---

## Mundiario

Mundiario se maneja como una única sección.

El sitio prácticamente no publica contenido nuevo desde 2024, por lo que el scraper se utiliza principalmente para recolección histórica.

---

## NCR Noticias y Noticias En Línea

Poseen modo prueba debido al gran volumen de artículos.

Ejemplos:

```bash
python main.py --only ncrnoticias --test
```

```bash
python main.py --only noticiasenlineacr --test
```

---

## Repretel

Debido al gran volumen histórico de contenido, el scraper fue limitado para extraer artículos desde 2023 hasta la actualidad.

---

## GRE / Observatorio

Los periódicos regionales se encuentran agrupados dentro de un único archivo.

Para integrarlos al pipeline sin modificar la lógica original se implementó el patrón Adapter, permitiendo que el sistema los trate como scrapers independientes.

---

# Flujo de Trabajo Git

## Actualizar repositorio

```bash
git pull
```

## Crear rama de trabajo

```bash
git checkout -b feature/nuevo-scraper
```

## Guardar cambios

```bash
git add .
git commit -m "Agregar nuevo scraper"
git push origin feature/nuevo-scraper
```

## Clonar el repo 
```bash
git clone <URL_DEL_REPOSITORIO>
cd ScrapingObservatorio
```

## Construcción inicial (IMPORTANTE)

Primera vez que se ejecuta el proyecto o cuando hay cambios en requirements 

docker compose up -d --build

## Ejecución normal (modo servicio)

Después de la primera construcción:

docker compose up -d

Esto ejecuta el sistema en segundo plano.