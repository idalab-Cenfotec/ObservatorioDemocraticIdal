"""
base_scraper.py
Clase base abstracta para todos los scrapers del Observatorio Democrático.
Implementa la lógica común: exportación CSV, validación de schema, logs.
"""

import os
import csv
import time
import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from langdetect import detect, LangDetectException

from output_cleaner import url_key
from scrapers.topes import (
    TOPES_MINUTOS, LIMITE_ARTICULO_SEG, LIMITE_SECCION_SEG, LIMITE_CIERRE_SEG, GUARDADO_PARCIAL_CADA,
)


async def cerrar_pagina(page, limite: int = LIMITE_CIERRE_SEG) -> None:
    """
    Cierra una página de Playwright sin poder colgarse: si la página falló
    ("Execution context was destroyed") page.close() puede no volver nunca y
    detenía el scraper hasta el timeout del job (puroperiodismo, 5-oct-2026).
    """
    try:
        await asyncio.wait_for(page.close(), timeout=limite)
    except Exception:
        pass


# Zona horaria Costa Rica (UTC-6)
CR_TZ = timezone(timedelta(hours=-6))

# Columnas obligatorias según el estándar v1.0
SCHEMA_COLUMNS = [
    "source",
    "url",
    "title",
    "publication_date",
    "scraping_date",
    "section",
    "full_text",
    "language",
]

# Longitud mínima de full_text
MIN_TEXT_LENGTH = 300


def setup_logger(source_name: str, log_dir: str = "logs") -> logging.Logger:
    """Configura un logger por scraper con archivo propio."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(source_name)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        # Handler consola
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(name)s: %(message)s"))

        # Handler archivo
        log_path = os.path.join(log_dir, f"{source_name}.log")
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(name)s: %(message)s"))

        logger.addHandler(ch)
        logger.addHandler(fh)

    return logger


def detect_language(text: str) -> str:
    """Detecta el idioma del texto. Retorna código ISO de 2 letras."""
    try:
        return detect(text[:2000])  # Usa solo los primeros 2000 chars para velocidad
    except LangDetectException:
        return "es"  # Default a español para medios costarricenses


def get_scraping_date() -> str:
    """Retorna la fecha/hora actual de scraping en formato estándar (UTC-6)."""
    now = datetime.now(CR_TZ)
    return now.strftime("%Y-%m-%d %H:%M:%S")


class CorteIncremental:
    """
    Decide cuándo dejar de paginar una sección en modo incremental: después de
    `limite` páginas seguidas cuyas URLs ya son todas conocidas (los listados
    van de lo más nuevo a lo más viejo). En modo completo nunca corta.

    Uso en el ciclo de paginación de un scraper:
        corte = CorteIncremental(self)
        while ...:
            urls_pagina = [...]
            if corte.pagina(urls_pagina):
                break
    """

    def __init__(self, scraper: "BaseScraper", limite: int = 3):
        self.scraper = scraper
        self.limite = limite
        self.seguidas = 0

    def pagina(self, urls: list[str]) -> bool:
        """Registra las URLs de una página y devuelve True si hay que dejar de paginar."""
        if not self.scraper.cortar_paginacion or not urls:
            return False
        if all(self.scraper.es_conocida(u) for u in urls):
            self.seguidas += 1
        else:
            self.seguidas = 0
        return self.seguidas >= self.limite


class BaseScraper(ABC):
    """
    Clase base abstracta para todos los scrapers del Observatorio Democrático.

    Cada scraper concreto debe implementar:
        - SOURCE_NAME: str  -> nombre normalizado del medio (snake_case, sin tildes)
        - BASE_URL: str     -> URL base del medio
        - scrape() -> list[dict]  -> lógica de scraping, retorna lista de registros

    La clase base se encarga de:
        - Generar scraping_date automáticamente
        - Detectar language del full_text
        - Validar el schema
        - Filtrar registros con full_text < 300 chars
        - Deduplicar por URL
        - Exportar CSV con separador pipe en UTF-8
        - Escribir logs de descartados
    """

    SOURCE_NAME: str = ""  # Debe definirse en cada subclase
    BASE_URL: str = ""

    def __init__(self, output_dir: str = "output", log_dir: str = "logs"):
        self.output_dir = output_dir
        self.log_dir = log_dir
        self.logger = setup_logger(self.SOURCE_NAME, log_dir)
        self.scraping_date = get_scraping_date()

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        Path(log_dir).mkdir(parents=True, exist_ok=True)

        # Modo incremental: URLs que ya están en PostgreSQL (ver _cargar_urls_conocidas)
        self.known_keys: set[str] = set()
        self.incremental = False          # hay lista de URLs conocidas -> se omiten los artículos ya cargados
        self.cortar_paginacion = False    # además, se deja de paginar al llegar a lo ya conocido
        self.omitidos_conocidos = 0

        # Tope de tiempo (SCRAPER_MAX_MINUTOS): al agotarse, el scraper deja de
        # visitar artículos y devuelve lo reunido hasta ese momento.
        # El tope propio del scraper (scrapers/topes.py) manda sobre el del grupo (variable de entorno)
        self.max_minutos = (TOPES_MINUTOS.get(self.SOURCE_NAME)
                            or float(os.environ.get("SCRAPER_MAX_MINUTOS") or 0) or None)
        self._t0 = time.monotonic()
        self.tope_alcanzado = False
        self._cp_validos: list[dict] = []   # guardado parcial: registros ya procesados
        self._cp_n = 0                      # cuántos registros de la lista del scraper ya se procesaron

        self._cargar_urls_conocidas()

    # ------------------------------------------------------------------
    # Modo incremental
    # ------------------------------------------------------------------

    def _cargar_urls_conocidas(self) -> None:
        """
        Pide a N8N (GET ?source=<SOURCE_NAME>) las URLs normalizadas de esta
        fuente que ya están en la base de datos. Con ellas el scraper puede
        saltarse los artículos ya cargados y dejar de paginar (es_conocida,
        debe_omitir, CorteIncremental).

        Variables de entorno: N8N_URLS_CONOCIDAS_URL y N8N_WEBHOOK_TOKEN.
        Cualquier problema (sin variables, N8N caído, respuesta inválida) deja
        el scraper como siempre: recorre todo y abre todos los artículos.
        SCRAPER_MODO_COMPLETO=1 recorre el listado completo (no corta la
        paginación, útil para traer lo atrasado) pero sigue abriendo solo los
        artículos que no están en la base.
        """
        url = os.environ.get("N8N_URLS_CONOCIDAS_URL")
        token = os.environ.get("N8N_WEBHOOK_TOKEN")
        if not url or not token:
            self.logger.info("Sin lista de URLs conocidas (falta N8N_URLS_CONOCIDAS_URL / N8N_WEBHOOK_TOKEN): se abren todos los artículos")
            return
        try:
            resp = requests.get(url, params={"source": self.SOURCE_NAME},
                                headers={"X-Webhook-Token": token}, timeout=(10, 120))
            resp.raise_for_status()
            urls = resp.json()["urls"]
            if not isinstance(urls, list):
                raise ValueError("'urls' no es una lista")
        except Exception as e:
            self.logger.warning(f"No se pudo obtener la lista de URLs conocidas, se abren todos los artículos ({e})")
            return
        self.known_keys = set(urls)
        self.incremental = True
        self.cortar_paginacion = os.environ.get("SCRAPER_MODO_COMPLETO") != "1"
        self.logger.info(
            f"Modo {'incremental' if self.cortar_paginacion else 'completo (sin cortar paginación)'}: "
            f"{len(self.known_keys):,} URLs ya conocidas de {self.SOURCE_NAME}")

    def tiempo_agotado(self, fraccion: float = 1.0) -> bool:
        """
        True si ya se gastó `fraccion` del tope de tiempo (SCRAPER_MAX_MINUTOS).
        Sin tope configurado nunca es True. Los scrapers lo consultan antes de
        cada página del listado (con fraccion=0.5, para dejar tiempo a los
        artículos) y antes de cada artículo (fraccion=1.0).
        """
        if not self.max_minutos:
            return False
        if time.monotonic() - self._t0 >= self.max_minutos * 60 * fraccion:
            if fraccion >= 1.0 and not self.tope_alcanzado:
                self.tope_alcanzado = True
                self.logger.warning(f"Tope de tiempo alcanzado ({self.max_minutos:g} min): se devuelve lo reunido hasta ahora")
            return True
        return False

    def es_conocida(self, url: str) -> bool:
        """True si el artículo ya está en la base (compara por url_key, no por texto exacto)."""
        return self.incremental and url_key(url) in self.known_keys

    # ------------------------------------------------------------------
    # Protecciones contra páginas colgadas y pérdida de lo ya reunido
    # ------------------------------------------------------------------

    async def articulo_seguro(self, context, link_data: dict, limite: int = LIMITE_ARTICULO_SEG):
        """
        Llama a _scrape_article con un límite duro de tiempo. Una nota que no termina
        en `limite` segundos se omite (se cancela y se registra) en vez de frenar todo
        el scraper; el tope por scraper solo se revisa entre notas y no cubre este caso.
        """
        try:
            return await asyncio.wait_for(self._scrape_article(context, link_data), timeout=limite)
        except asyncio.TimeoutError:
            self.logger.warning(f"Límite de {limite}s por artículo agotado, se omite: {link_data.get('url')}")
            return None

    async def seccion_segura(self, corrutina, nombre: str = "", limite: int = LIMITE_SECCION_SEG):
        """
        Espera la recolección de enlaces de una sección con un límite duro. Si se agota,
        devuelve [] y el scraper sigue con la siguiente sección (esa se reintenta en la
        próxima corrida, porque sus URLs no quedaron cargadas).
        """
        try:
            return await asyncio.wait_for(corrutina, timeout=limite)
        except asyncio.TimeoutError:
            self.logger.warning(f"Límite de {limite // 60} min agotado recolectando la sección {nombre!r}, se continúa con la siguiente")
            return []

    def checkpoint(self, registros: list[dict], cada: int = GUARDADO_PARCIAL_CADA) -> None:
        """
        Guarda en el CSV final de la corrida lo reunido hasta ahora, cada `cada` artículos.
        Si el job se cancela por timeout, el CSV parcial ya existe y la consolidación lo
        recoge (el 5-oct-2026 se perdieron 3,678 notas de puntarenasseoye porque el CSV
        solo se escribía al terminar). run() lo reescribe completo al final.
        """
        if not registros or len(registros) % cada:
            return
        try:
            nuevos = registros[self._cp_n:]
            validos, _ = self._process([dict(r) for r in nuevos])
            self._cp_validos.extend(validos)
            self._cp_n = len(registros)
            self._export_csv(self._cp_validos, self._get_output_filename(), SCHEMA_COLUMNS)
            self.logger.info(f"Guardado parcial: {len(self._cp_validos)} artículos en {self._get_output_filename()}")
        except Exception as e:
            self.logger.warning(f"No se pudo guardar el parcial: {e}")

    def debe_omitir(self, url: str) -> bool:
        """es_conocida + cuenta el artículo como omitido, para el resumen de la corrida."""
        if self.es_conocida(url):
            self.omitidos_conocidos += 1
            return True
        return False

    @abstractmethod
    def scrape(self) -> list[dict]:
        """
        Implementa la lógica de scraping del medio.
        Debe retornar una lista de dicts con al menos las claves:
            url, title, publication_date, section, full_text
        Los campos source, scraping_date y language son inyectados automáticamente.
        """
        pass

    # ------------------------------------------------------------------
    # Pipeline de procesamiento
    # ------------------------------------------------------------------

    def _enrich_record(self, record: dict) -> dict:
        """Inyecta los campos genéricos en cada registro."""
        record["source"] = self.SOURCE_NAME
        record["scraping_date"] = self.scraping_date
        # Detectar idioma si no viene definido
        if not record.get("language") and record.get("full_text"):
            record["language"] = detect_language(record["full_text"])
        elif not record.get("language"):
            record["language"] = "es"
        return record

    def _validate_record(self, record: dict) -> tuple[bool, str]:
        """
        Valida un registro contra el schema.
        Retorna (True, "") si es válido o (False, motivo) si debe descartarse.
        """
        # Verificar campos obligatorios
        for col in SCHEMA_COLUMNS:
            if col not in record or record[col] is None or record[col] == "":
                if col == "full_text":
                    return False, f"Campo obligatorio vacío: {col}"
                if col == "publication_date":
                    return False, f"Campo obligatorio nulo: {col}"

        # Validar longitud mínima de full_text
        full_text = record.get("full_text", "") or ""
        if len(full_text) < MIN_TEXT_LENGTH:
            return False, f"full_text demasiado corto ({len(full_text)} chars < {MIN_TEXT_LENGTH})"

        return True, ""

    def _deduplicate(self, records: list[dict]) -> tuple[list[dict], list[dict]]:
        """Elimina duplicados por URL. Retorna (válidos, descartados)."""
        seen_urls = set()
        unique = []
        discarded = []

        for r in records:
            url = r.get("url", "")
            if url in seen_urls:
                discarded.append({**r, "_discard_reason": "URL duplicada"})
            else:
                seen_urls.add(url)
                unique.append(r)

        return unique, discarded

    def _process(self, raw_records: list[dict]) -> tuple[list[dict], list[dict]]:
        """Pipeline completo: enrich → validate → deduplicate."""
        enriched = [self._enrich_record(r) for r in raw_records]

        valid = []
        discarded = []

        for r in enriched:
            ok, reason = self._validate_record(r)
            if ok:
                valid.append(r)
            else:
                discarded.append({**r, "_discard_reason": reason})

        valid_deduped, dupes = self._deduplicate(valid)
        discarded.extend(dupes)

        return valid_deduped, discarded

    # ------------------------------------------------------------------
    # Exportación
    # ------------------------------------------------------------------

    def _get_output_filename(self) -> str:
        """Genera el nombre de archivo según estándar: source_YYYYMMDD.csv"""
        date_str = datetime.now(CR_TZ).strftime("%Y%m%d")
        return os.path.join(self.output_dir, f"{self.SOURCE_NAME}_{date_str}.csv")

    def _get_log_filename(self) -> str:
        """Nombre del archivo de log de descartados."""
        date_str = datetime.now(CR_TZ).strftime("%Y%m%d")
        return os.path.join(self.log_dir, f"{self.SOURCE_NAME}_{date_str}_discarded.csv")

    def _export_csv(self, records: list[dict], filepath: str, columns: list[str]):
        """Exporta lista de dicts a CSV con separador pipe, UTF-8, sin índice."""
        with open(filepath, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=columns,
                delimiter="|",
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            for row in records:
                # Reemplazar None por NULL según estándar
                cleaned = {k: ("NULL" if v is None else v) for k, v in row.items()}
                writer.writerow(cleaned)

    # ------------------------------------------------------------------
    # Método principal de ejecución
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        Ejecuta el scraper completo.
        Retorna un resumen con conteos de artículos procesados/descartados.
        """
        self.logger.info(f"=== Iniciando scraper: {self.SOURCE_NAME} ===")
        self.logger.info(f"URL base: {self.BASE_URL}")
        self._t0 = time.monotonic()   # el tope de tiempo cuenta desde que arranca la corrida, no desde el __init__

        try:
            raw = self.scrape()
            self.logger.info(f"Artículos crudos recolectados: {len(raw)}")
        except Exception as e:
            self.logger.error(f"Error crítico durante scraping: {e}", exc_info=True)
            return {"source": self.SOURCE_NAME, "status": "ERROR", "error": str(e)}

        valid, discarded = self._process(raw)

        # Exportar válidos
        output_file = self._get_output_filename()
        self._export_csv(valid, output_file, SCHEMA_COLUMNS)
        self.logger.info(f"Exportados {len(valid)} registros válidos → {output_file}")

        # Exportar descartados al log
        if discarded:
            log_file = self._get_log_filename()
            log_cols = SCHEMA_COLUMNS + ["_discard_reason"]
            self._export_csv(discarded, log_file, log_cols)
            self.logger.warning(f"{len(discarded)} registros descartados → {log_file}")

        summary = {
            "source": self.SOURCE_NAME,
            "status": "OK",
            "total_raw": len(raw),
            "total_valid": len(valid),
            "total_discarded": len(discarded),
            "output_file": output_file,
            "modo": "incremental" if self.cortar_paginacion else ("completo" if self.incremental else "sin_lista"),
            "omitidos_conocidos": self.omitidos_conocidos,
            "tope_alcanzado": self.tope_alcanzado,
        }

        self.logger.info(f"=== Scraper finalizado: {self.SOURCE_NAME} | Resumen: {summary} ===")
        return summary
