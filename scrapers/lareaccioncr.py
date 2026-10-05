"""
lareaccioncr.py
Scraper para La Reacción CR (https://lareaccioncr.com/2026/)

Características del sitio:
- WordPress con tema Mikado (mkd-*)
- NO tiene scroll infinito — tiene paginación por categorías y archivo mensual
- Dos fuentes de URLs:
    1. Todas las categorías del sitio (12 categorías conocidas)
    2. Archivo mensual (ul.wp-block-archives-list en sidebar)
- Tarjeta de listado: div.mkd-pt-two-content-top-holder-cell
    - Título y URL: h3.mkd-pt-two-title > a.mkd-pt-link
    - Fecha en listado: div.mkd-post-info-date (texto "7 de abril de 2026")
- Artículo individual:
    - Contenedor: div.mkd-blog-holder > article > div.mkd-post-content
    - Texto: div.mkd-post-text — extrae p, ul/li, blockquote, strong
    - Fecha: div.mkd-post-info-date[itemprop="dateCreated"]
    - Sección: div.mkd-post-info-category > a (primera categoría)
- Filtro: ignorar URLs que contengan /category/ (son páginas de listado, no artículos)
- Filtro: ignorar URLs con video embebido como único contenido
"""

import asyncio
import re
from datetime import datetime, timezone, timedelta

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.base_scraper import BaseScraper, CorteIncremental, cerrar_pagina

CR_TZ = timezone(timedelta(hours=-6))

# Sin el año: is_article_url exige año/mes/día/slug después de BASE_URL, y con el
# año incluido (…/2026/) todos los artículos quedaban con solo 3 segmentos y se rechazaban.
BASE_URL = "https://lareaccioncr.com/"

# Categorías conocidas del sitio
CATEGORIES = [
    "digalo",
    "aqui-hay-tema",
    "mando",
    "desafio",
    "destacados",
    "municipales",
    "geopolitica",
    "mercados",
    "politica",
    "protagonistas",
    "reacciones",
    "tendencias",
]

ARTICLE_TIMEOUT = 20_000
DELAY_BETWEEN_ARTICLES = 1.0
DELAY_BETWEEN_PAGES = 1.5

# Modo prueba: limita número de artículos procesados
TEST_MAX_ARTICLES = 5

# Meses en español para parsear fechas textuales
MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def parse_date_es(text: str) -> str:
    """
    Parsea fechas en español como '7 de abril de 2026'
    Retorna formato YYYY-MM-DD.
    """
    text = text.strip().lower()
    match = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", text)
    if match:
        day = int(match.group(1))
        month = MESES_ES.get(match.group(2), 0)
        year = int(match.group(3))
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return text


def clean_text(text: str) -> str:
    """Limpia espacios múltiples y saltos de línea innecesarios."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_article_url(url: str) -> bool:
    """Filtra URLs que NO son artículos (categorías, autores, páginas, etc.)."""
    if not url or not url.startswith(BASE_URL):
        return False
    # Excluir páginas de categoría, autor, archivo, tag
    exclude_patterns = [
        "/category/", "/author/", "/tag/", "/page/",
        "/#", "/?", "/wp-", "/feed",
    ]
    for pat in exclude_patterns:
        if pat in url:
            return False
    # URL de artículo tiene al menos año/mes/día/slug
    path = url.replace(BASE_URL, "")
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 4  # año/mes/día/slug


class LaReaccionCRScraper(BaseScraper):
    """
    Scraper para La Reacción CR.
    Estrategia de recolección:
      1. Visita cada categoría y pagina hasta el final
      2. Visita cada URL del archivo mensual y pagina hasta el final
      3. Deduplica URLs y visita cada artículo para extraer texto completo
    """

    SOURCE_NAME = "lareaccioncr"
    BASE_URL = BASE_URL


    def __init__(self, output_dir="output", log_dir="logs", test_mode: bool = False):
        super().__init__(output_dir=output_dir, log_dir=log_dir)
        self.test_mode = test_mode
        if self.test_mode:
            self.logger.info(f"*** MODO PRUEBA ACTIVO: limitando a {TEST_MAX_ARTICLES} artículos ***")

    def scrape(self) -> list[dict]:
        return asyncio.run(self._scrape_async())

    async def _scrape_async(self) -> list[dict]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="es-CR",
            )

            # -------------------------------------------------------
            # PASO 1: Recolectar URLs del archivo mensual desde home
            # -------------------------------------------------------
            archive_urls = await self._get_archive_urls(context)
            self.logger.info(f"Archivos mensuales encontrados: {len(archive_urls)}")

            # -------------------------------------------------------
            # PASO 2: Recolectar artículos de categorías y archivos
            # -------------------------------------------------------
            article_links = {}  # url -> {url, title, section, publication_date}

            # Desde categorías
            for cat in CATEGORIES:
                cat_url = f"{BASE_URL}category/{cat}/"
                self.logger.info(f"Scrapendo categoría: {cat}")
                links = await self.seccion_segura(self._collect_from_listing(context, cat_url, section_hint=cat), "_collect_from_listing")
                for link in links:
                    if link["url"] not in article_links:
                        article_links[link["url"]] = link
                await asyncio.sleep(DELAY_BETWEEN_PAGES)

            # Desde archivo mensual
            for arch_url in archive_urls:
                self.logger.info(f"Scrapendo archivo: {arch_url}")
                links = await self.seccion_segura(self._collect_from_listing(context, arch_url, section_hint=""), "_collect_from_listing")
                for link in links:
                    if link["url"] not in article_links:
                        article_links[link["url"]] = link
                await asyncio.sleep(DELAY_BETWEEN_PAGES)

            self.logger.info(f"Total URLs únicas recolectadas: {len(article_links)}")

            # -------------------------------------------------------
            # PASO 3: Visitar cada artículo y extraer texto completo
            # -------------------------------------------------------
            records = []
            links_list = [l for l in article_links.values() if not self.debe_omitir(l["url"])]
            if self.incremental:
                self.logger.info(
                    f"Artículos nuevos por visitar: {len(links_list)} "
                    f"({self.omitidos_conocidos} ya estaban en la base)"
                )
            if self.test_mode:
                links_list = links_list[:TEST_MAX_ARTICLES]

            for i, link_data in enumerate(links_list):
                if self.tiempo_agotado():
                    break
                self.logger.debug(f"[{i+1}/{len(links_list)}] {link_data['url']}")
                record = await self.articulo_seguro(context, link_data)
                if record:
                    records.append(record)
                    self.checkpoint(records)
                await asyncio.sleep(DELAY_BETWEEN_ARTICLES)

            await browser.close()
            return records

    # ------------------------------------------------------------------
    # Recolección de URLs del archivo mensual
    # ------------------------------------------------------------------

    async def _get_archive_urls(self, context) -> list[str]:
        """
        Visita la home y extrae los enlaces del bloque de archivo mensual.
        Selector: ul.wp-block-archives-list li a
        """
        page = await context.new_page()
        archive_urls = []
        try:
            # El widget de archivo mensual no está en la portada; sí en las páginas /AAAA/
            await page.goto(f"{BASE_URL}{datetime.now().year}/", wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2000)

            links = await page.query_selector_all("ul.wp-block-archives-list li a")
            for link in links:
                href = await link.get_attribute("href")
                if href:
                    archive_urls.append(href.strip())
        except Exception as e:
            self.logger.error(f"Error obteniendo archivo mensual: {e}")
        finally:
            await cerrar_pagina(page)
        return archive_urls

    # ------------------------------------------------------------------
    # Recolección de URLs desde una página de listado (categoría o archivo)
    # Con paginación (page/2/, page/3/, ...)
    # ------------------------------------------------------------------

    async def _collect_from_listing(
        self, context, base_listing_url: str, section_hint: str
    ) -> list[dict]:
        """
        Navega una URL de listado (categoría o archivo mensual) y todas sus páginas.
        Extrae: URL, título, fecha (texto), sección del listado.
        
        Estructura actual (tema ColorMag):
        - article (cada artículo en el listado)
          - link (primera URL del artículo)
          - generic (metadata)
            - generic (categorías)
              - link (categoría)
            - generic (fecha y autor)
              - link (fecha con time element)
              - time: fecha textual
          - heading h2 (título)
            - link (URL)
          - generic (resumen)
            - paragraph (texto)
        """
        collected = []
        page_num = 1
        corte = CorteIncremental(self)

        while True:
            if self.tiempo_agotado(0.5):
                self.logger.warning(f"Tiempo del listado agotado en {base_listing_url}, se pasa a los artículos")
                break
            if page_num == 1:
                url = base_listing_url
            else:
                # WordPress usa /page/N/ para paginación
                url = base_listing_url.rstrip("/") + f"/page/{page_num}/"

            page = await context.new_page()
            found_on_page = 0

            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=25_000)

                # Si la página no existe (404), detenemos la paginación
                if resp and resp.status == 404:
                    self.logger.debug(f"Página {page_num} no existe ({url}), fin paginación")
                    break

                await page.wait_for_timeout(1500)

                # -----------------------------------------------------------
                # Extraer desde artículos (tema ColorMag)
                # Selector: article (elementos principales)
                # -----------------------------------------------------------
                n_antes = len(collected)
                articles = await page.query_selector_all("article")
                for art in articles:
                    try:
                        # Extraer URL: primer link dentro del artículo
                        # O desde el heading h2 > link
                        # Tema actual (mkd-*): h3.entry-title; el tema anterior usaba h2/h1
                        url_anchor = await art.query_selector("h1 a, h2 a, h3.entry-title a, h3 a")
                        if not url_anchor:
                            # Sin título: no es una nota (p. ej. los <article> de
                            # "comentarios recientes" del sidebar)
                            continue
                            
                        href = await url_anchor.get_attribute("href")
                        title = await url_anchor.inner_text()

                        if not href or not is_article_url(href):
                            continue

                        # Fecha: buscar time element
                        pub_date = ""
                        time_el = await art.query_selector("time, .mkd-post-info-date, .entry-date")
                        if time_el:
                            date_text = await time_el.inner_text()
                            pub_date = parse_date_es(date_text)

                        # Sección: desde los links de categoría (primer link)
                        section = section_hint
                        cat_link = await art.query_selector("a.category, a[href*='/category/']")
                        if cat_link:
                            section = clean_text(await cat_link.inner_text())

                        collected.append({
                            "url": href.strip(),
                            "title": clean_text(title),
                            "section": section,
                            "publication_date": pub_date,
                        })
                        found_on_page += 1

                    except Exception as e:
                        self.logger.debug(f"Error en article: {e}")
                        continue

                self.logger.debug(
                    f"  Página {page_num} de {base_listing_url}: {found_on_page} artículos"
                )

                # Si no encontramos nada en esta página, terminamos
                if found_on_page == 0:
                    break

                # Varias páginas seguidas con solo URLs ya cargadas -> fin del listado
                if corte.pagina([l["url"] for l in collected[n_antes:]]):
                    self.logger.info(f"  Página {page_num} de {base_listing_url}: solo URLs ya conocidas, fin del listado")
                    break

                page_num += 1

            except PlaywrightTimeoutError:
                self.logger.warning(f"Timeout en listado: {url}")
                break
            except Exception as e:
                self.logger.error(f"Error en listado {url}: {e}")
                break
            finally:
                await cerrar_pagina(page)

            await asyncio.sleep(DELAY_BETWEEN_PAGES)

        return collected

    # ------------------------------------------------------------------
    # Scraping del artículo individual
    # ------------------------------------------------------------------

    async def _scrape_article(self, context, link_data: dict) -> dict | None:
        """
        Visita un artículo individual (estructura ColorMag).
        Extrae:
        - full_text desde párrafos dentro del artículo
        - publication_date desde time element
        - section desde categoría del artículo
        """
        page = await context.new_page()

        try:
            await page.goto(link_data["url"], wait_until="domcontentloaded", timeout=ARTICLE_TIMEOUT)
            await page.wait_for_timeout(1500)

            # -----------------------------------------------------------
            # Extraer contenido: buscar todos los párrafos dentro del artículo
            # En ColorMag, el contenido está en múltiples `p` dentro de article
            # -----------------------------------------------------------
            article = await page.query_selector("article")
            if not article:
                self.logger.warning(f"Sin article tag: {link_data['url']}")
                return None

            full_text = await self._extract_text_from_block(article)

            if not full_text or len(full_text) < 50:
                self.logger.warning(f"Sin texto válido: {link_data['url']}")
                return None

            # -----------------------------------------------------------
            # Fecha de publicación (desde el artículo)
            # -----------------------------------------------------------
            publication_date = link_data.get("publication_date", "")

            time_el = await article.query_selector("time, .mkd-post-info-date, .entry-date")
            if time_el:
                date_text = await time_el.inner_text()
                parsed = parse_date_es(date_text)
                if parsed and len(parsed) == 10:  # YYYY-MM-DD
                    publication_date = parsed

            # -----------------------------------------------------------
            # Sección: primera categoría del artículo
            # -----------------------------------------------------------
            section = link_data.get("section", "")
            cat_link = await article.query_selector("a.category, a[href*='/category/']")
            if cat_link:
                section = clean_text(await cat_link.inner_text())

            return {
                "url": link_data["url"],
                "title": link_data["title"],
                "section": section,
                "publication_date": publication_date,
                "full_text": full_text,
            }

        except PlaywrightTimeoutError:
            self.logger.warning(f"Timeout: {link_data['url']}")
            return None
        except Exception as e:
            self.logger.error(f"Error scrapeando {link_data['url']}: {e}", exc_info=True)
            return None
        finally:
            await cerrar_pagina(page)

    async def _extract_text_from_block(self, block) -> str:
        """
        Extrae texto limpio de un bloque HTML (artículo).
        Incluye: p, li (de ul/ol), blockquote, texto de strong/em inline.
        Excluye: scripts, estilos, comentarios, navegación, anuncios.
        """
        text_content = await block.evaluate("""
            (el) => {
                // Clonar para no modificar el DOM original
                const clone = el.cloneNode(true);

                // Eliminar elementos no deseados (ColorMag specific y generales)
                const remove_selectors = [
                    '.wp-block-embed',
                    '.sharedaddy',
                    '#jp-post-flair',
                    '.comment',
                    '.comments-area',
                    '.post-navigation',
                    '.related',
                    '.sidebar',
                    'nav',
                    'script',
                    'style',
                    'iframe',
                    'figure:has(iframe)',
                ];
                
                remove_selectors.forEach(sel => {
                    try {
                        clone.querySelectorAll(sel).forEach(el => el.remove());
                    } catch (e) {}
                });

                // Extraer texto de elementos relevantes
                const results = [];
                const elements = clone.querySelectorAll(
                    'p:not(:empty), li:not(:empty), blockquote:not(:empty)'
                );

                elements.forEach(el => {
                    const text = el.innerText || el.textContent || '';
                    const cleaned = text
                        .replace(/\\s+/g, ' ')
                        .replace(/\\n+/g, '\\n')
                        .trim();
                    
                    if (cleaned.length > 10) {
                        results.push(cleaned);
                    }
                });

                return results.join('\\n\\n');
            }
        """)

        return clean_text(text_content) if text_content else ""


# Ejecución directa para pruebas
if __name__ == "__main__":
    scraper = LaReaccionCRScraper()
    summary = scraper.run()
    print(summary)
