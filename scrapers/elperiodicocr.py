"""
elperiodicocr.py
Scraper para El Periódico CR (https://www.elperiodicocr.com/)

Características del sitio:
- Contenido dinámico (JavaScript)
- Scroll infinito (sin paginación numérica)
- Estructura basada en temas de WordPress (Tagdiv)
- Contenedor principal: div con clase que contiene 'td_block'
- Cada noticia: div.td-module-container
- Título y URL: h2.entry-title a
- Sección: a.td-post-category
- Fecha: time.entry-date[datetime]
- Texto artículo: div.tdb-block-inner > p (ignorando ads)
"""

import asyncio
import re
from datetime import datetime, timezone, timedelta

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.base_scraper import BaseScraper

CR_TZ = timezone(timedelta(hours=-6))

# URL de últimas noticias (scroll infinito)
LISTING_URL = "https://www.elperiodicocr.com/ultimas-noticias/"

# Número máximo de scrolls en la página de listado
MAX_SCROLLS = 8

# Timeout por página de artículo (ms)
ARTICLE_TIMEOUT = 15_000

# Pausa entre artículos para no sobrecargar el servidor (segundos)
DELAY_BETWEEN_ARTICLES = 0.1

# Modo prueba: limita número de artículos procesados
TEST_MAX_ARTICLES = 5


def clean_text(text: str) -> str:
    """Limpia espacios múltiples y saltos de línea innecesarios."""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_date(raw_datetime: str) -> str:
    """
    Parsea el atributo datetime de <time> a formato YYYY-MM-DD HH:MM:SS
    Ejemplo entrada: '2026-04-24T15:24:24-06:00'
    """
    try:
        dt = datetime.fromisoformat(raw_datetime)
        # Normalizar a UTC-6
        dt_cr = dt.astimezone(CR_TZ)
        return dt_cr.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return raw_datetime  # retorna tal cual si no se puede parsear


class ElPeriodicoCRScraper(BaseScraper):
    """
    Scraper para El Periódico CR.
    Navega la página de últimas noticias con scroll infinito,
    recolecta URLs de artículos y visita cada uno para extraer el texto completo.
    """

    SOURCE_NAME = "elperiodicocr"
    BASE_URL = "https://www.elperiodicocr.com/"


    def __init__(self, output_dir="output", log_dir="logs", test_mode: bool = False):
        super().__init__(output_dir=output_dir, log_dir=log_dir)
        self.test_mode = test_mode
        if self.test_mode:
            self.logger.info(f"*** MODO PRUEBA ACTIVO: limitando a {TEST_MAX_ARTICLES} artículos ***")

    def scrape(self) -> list[dict]:
        """Punto de entrada sincrónico que lanza el scraper async."""
        return asyncio.run(self._scrape_async())

    async def _scrape_async(self) -> list[dict]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="es-CR",
                timezone_id="America/Costa_Rica",
                viewport={"width": 1920, "height": 1080},
                extra_http_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "es-CR,es;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept-Encoding": "gzip, deflate, br",
                    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                }
            )

            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                Object.defineProperty(navigator, 'languages', {get: () => ['es-CR', 'es', 'en']});
                window.chrome = { runtime: {} };
            """)

            # -------------------------------------------------------
            # PASO 1: Recolectar URLs desde la página de listado
            # -------------------------------------------------------
            article_links = await self._collect_article_links(context)
            # Limitar listado total por seguridad
            article_links = article_links[:150]
            if self.test_mode:
                article_links = article_links[:TEST_MAX_ARTICLES]
            self.logger.info(
                f"Total URLs recolectadas: {len(article_links)}"
            )
            # -------------------------------------------------------
            # PASO 2: Visitar cada artículo y extraer datos
            # -------------------------------------------------------
            records = []
            for i, link_data in enumerate(article_links):
                self.logger.info(f"[{i+1}/{len(article_links)}] Procesando: {link_data['url']}")
                record = await self._scrape_article(context, link_data)
                if record:
                    records.append(record)
                    self.logger.info(f"   ✓ Artículo extraído exitosamente")
                await asyncio.sleep(DELAY_BETWEEN_ARTICLES)

            await browser.close()
            return records

    async def _collect_article_links(self, context) -> list[dict]:
        """
        Navega la página de listado con scroll infinito.
        Recolecta URL, título y sección de cada tarjeta de noticia.
        """

        page = await context.new_page()
        collected = {}

        try:
            await page.goto(
                LISTING_URL,
                wait_until="domcontentloaded",
                timeout=30_000
            )

            await page.wait_for_timeout(3000)

            for scroll_num in range(MAX_SCROLLS):

                articles = await page.evaluate("""
            () => {
                return Array.from(
                    document.querySelectorAll('div.td-module-container')
                ).map(card => {
                    const titleA =
                        card.querySelector('h2.entry-title a');

                    const sectionA =
                        card.querySelector('a.td-post-category');

                    return {
                        url: titleA?.href || '',
                        title: titleA?.innerText || '',
                        section: sectionA?.innerText || ''
                    };
                });
            }
            """)

                previous_count = len(collected)

                for article in articles:

                    url = article.get("url", "").strip()

                    if not url:
                        continue

                    if url in collected:
                        continue

                    collected[url] = {
                        "url": url,
                        "title": clean_text(
                            article.get("title", "")
                        ),
                        "section": clean_text(
                            article.get("section", "")
                        )
                    }

                self.logger.info(
                    f"Scroll {scroll_num + 1}/{MAX_SCROLLS} | "
                    f"Artículos acumulados: {len(collected)}"
                )

                await page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )

                await page.wait_for_timeout(5000)

                if len(collected) == previous_count and scroll_num >= 3:
                    self.logger.info(
                        "No se detectaron nuevos artículos. "
                        "Finalizando scroll."
                    )
                    break

        except Exception as e:
            self.logger.error(
                f"Error en recolección de listado: {e}",
                exc_info=True
            )

        finally:
            await page.close()

        return list(collected.values())

    async def _scrape_article(self, context, link_data: dict) -> dict | None:
        """
        Visita un artículo individual y extrae:
        - full_text (todos los <p> del contenido, sin ads)
        - publication_date (desde <time class='entry-date'>)
        Combina con los datos del listado (url, title, section).
        """
        page = await context.new_page()

        try:
            await page.goto(link_data["url"], wait_until="domcontentloaded", timeout=ARTICLE_TIMEOUT)
            try:
                await page.wait_for_selector(
                    "div.td-post-content, div.tdb-block-inner",
                    timeout=8_000
                )
            except Exception:
                pass  # si no aparece, el scraper lo maneja con el fallback existente

            # -----------------------------------------------------------
            # Extraer fecha de publicación
            # -----------------------------------------------------------
            publication_date = None
            time_el = await page.query_selector("time.entry-date")
            if time_el:
                raw_dt = await time_el.get_attribute("datetime")
                if raw_dt:
                    publication_date = parse_date(raw_dt)

            # Si no se encontró en el artículo, buscar en meta tags
            if not publication_date:
                meta_el = await page.query_selector('meta[property="article:published_time"]')
                if meta_el:
                    raw_dt = await meta_el.get_attribute("content")
                    if raw_dt:
                        publication_date = parse_date(raw_dt)

            # -----------------------------------------------------------
            # Extraer texto completo
            # Busca en div.td-post-content (contenedor principal) o
            # div.tdb-block-inner como fallback
            # -----------------------------------------------------------
            full_text = ""
            paragraphs_collected = []

            # Intentar primero con td-post-content (contenedor principal)
            main_content = await page.query_selector("div.td-post-content")
            
            if main_content:
                p_elements = await main_content.query_selector_all("p")
                
                for p in p_elements:
                    p_text = await p.inner_text()
                    p_text = clean_text(p_text)

                    # Filtrar párrafos vacíos, muy cortos o de publicidad
                    if not p_text or len(p_text) < 10:
                        continue
                    if "adsbygoogle" in p_text.lower():
                        continue
                    if "leer más" in p_text.lower():
                        continue

                    paragraphs_collected.append(p_text)

            # Si no encontramos párrafos en td-post-content, intentar con tdb-block-inner
            if not paragraphs_collected:
                content_blocks = await page.query_selector_all("div.tdb-block-inner")

                for block in content_blocks:
                    block_html = await block.inner_html()

                    # Saltar bloques que solo tienen publicidad
                    if "adsbygoogle" in block_html and "<p>" not in block_html:
                        continue

                    p_elements = await block.query_selector_all("p")

                    for p in p_elements:
                        p_text = await p.inner_text()
                        p_text = clean_text(p_text)

                        if not p_text or len(p_text) < 10:
                            continue
                        if "adsbygoogle" in p_text.lower():
                            continue

                        paragraphs_collected.append(p_text)

            # Deduplicar párrafos manteniendo orden
            seen = set()
            unique_paragraphs = []
            for p in paragraphs_collected:
                if p not in seen:
                    seen.add(p)
                    unique_paragraphs.append(p)

            full_text = "\n\n".join(unique_paragraphs)

            # -----------------------------------------------------------
            # Construir registro
            # -----------------------------------------------------------
            if not full_text:
                self.logger.warning(f"Sin texto extraído: {link_data['url']} (título de la página: {await page.title()!r})")
                return None

            return {
                "url": link_data["url"],
                "title": link_data["title"],
                "section": link_data["section"],
                "publication_date": publication_date,
                "full_text": full_text,
                # source, scraping_date y language se inyectan en BaseScraper
            }

        except PlaywrightTimeoutError:
            self.logger.warning(f"Timeout al cargar artículo: {link_data['url']}")
            return None
        except Exception as e:
            self.logger.error(f"Error scrapeando {link_data['url']}: {e}", exc_info=True)
            return None
        finally:
            await page.close()


# Permite ejecutar el scraper directamente para pruebas
if __name__ == "__main__":
    scraper = ElPeriodicoCRScraper()
    summary = scraper.run()
    print(summary)
