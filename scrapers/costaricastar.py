"""
costaricastar.py
Scraper para The Costa Rica Star (https://news.co.cr/)

Secciones (9):
  - National News : https://news.co.cr/costa-rica/
  - Crime         : https://news.co.cr/crime-safety-costa-rica/
  - Business      : https://news.co.cr/business/
  - Technology    : https://news.co.cr/technology/
  - Science       : https://news.co.cr/science/
  - Politics      : https://news.co.cr/politics/
  - Education     : https://news.co.cr/education/
  - Real Estate   : https://news.co.cr/real-estate/
  - Immigration   : https://news.co.cr/immigration/

Paginación: /page/2/, /page/3/ (WordPress estándar)

Estructura listado:
  - Título y URL : div.titulcate > a.post-title  (texto + href absoluto)

Estructura artículo:
  - Fecha : script[type='application/ld+json'].yoast-schema-graph
            → WebPage.datePublished  ("2022-01-31T16:27:26+00:00")
            Si no se encuentra → publication_date = "Not Found"
            Se registra en log cuántos artículos sin fecha por sesión.
  - Texto : div[class*='pf-content'] o div[class*='entry-content']
            → todos los <p>  (excluye ads, banners, print button, relacionados)

Deduplicación global entre secciones.
Modo prueba: --test limita secciones, páginas y artículos.

Nota: el sitio está en inglés → language será detectado como 'en' por langdetect.
"""

import asyncio
import json
import re
import random
from datetime import datetime, timezone, timedelta

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.base_scraper import BaseScraper, cerrar_pagina

CR_TZ = timezone(timedelta(hours=-6))
UTC   = timezone.utc

BASE_URL = "https://news.co.cr"

SECTIONS = [
    ("https://news.co.cr/costa-rica/",              "National News"),
    ("https://news.co.cr/crime-safety-costa-rica/", "Crime"),
    ("https://news.co.cr/business/",                "Business"),
    ("https://news.co.cr/technology/",              "Technology"),
    ("https://news.co.cr/science/",                 "Science"),
    ("https://news.co.cr/politics/",                "Politics"),
    ("https://news.co.cr/education/",               "Education"),
    ("https://news.co.cr/real-estate/",             "Real Estate"),
    ("https://news.co.cr/immigration/",             "Immigration"),
]

ARTICLE_TIMEOUT = 22_000
DELAY_BETWEEN_ARTICLES = 1.0
DELAY_BETWEEN_PAGES = 1.5
DELAY_BETWEEN_SECTIONS = 2.5
DELAY_SCROLL = 1800

# Modo prueba
TEST_MAX_SECTIONS = 2
TEST_MAX_PAGES = 2
TEST_MAX_ARTICLES = 8

# Contador global de artículos sin fecha (para reporte al finalizar)
_no_date_count = 0
_no_date_urls: list[str] = []


def parse_iso_date(raw: str) -> str:
    """Parsea ISO 8601 a 'YYYY-MM-DD HH:MM:SS' en UTC."""
    try:
        raw = raw.strip().replace(" ", "")  # limpiar espacios raros
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def extract_date_from_jsonld(jsonld_text: str) -> str:
    """
    Extrae datePublished del JSON-LD de Yoast.
    Busca el objeto con @type=WebPage y retorna datePublished.
    Si no lo encuentra retorna "".
    """
    try:
        data = json.loads(jsonld_text)
        graph = data.get("@graph", [])
        for node in graph:
            if node.get("@type") == "WebPage":
                raw = node.get("datePublished", "")
                if raw:
                    parsed = parse_iso_date(raw)
                    if parsed:
                        return parsed
        # Fallback: buscar datePublished en cualquier nodo
        for node in graph:
            raw = node.get("datePublished", "")
            if raw:
                parsed = parse_iso_date(raw)
                if parsed:
                    return parsed
    except Exception:
        pass
    return ""


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_article_url(url: str) -> bool:
    if not url or not url.startswith("https://news.co.cr/"):
        return False
    exclude = ["/page/", "/author/", "/tag/", "/category/",
               "/?", "/#", "/wp-", "/feed", "/search"]
    for pat in exclude:
        if pat in url:
            return False
    path = url.replace("https://news.co.cr/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    # Artículos tienen formato /slug/ID/ → al menos 2 segmentos
    return len(parts) >= 2


class CostaRicaStarScraper(BaseScraper):
    """
    Scraper para The Costa Rica Star.
    - 9 secciones en inglés con paginación /page/N/
    - Fecha extraída de JSON-LD (Yoast schema)
    - Si no hay fecha → 'Not Found' + log de URLs sin fecha
    - Deduplicación global entre secciones
    - Modo prueba limita secciones, páginas y artículos
    """

    SOURCE_NAME = "costaricastar"
    BASE_URL = BASE_URL + "/"

    def __init__(self, output_dir="output", log_dir="logs", test_mode=False):
        super().__init__(output_dir=output_dir, log_dir=log_dir)
        self.test_mode = test_mode
        self._no_date_count = 0
        self._no_date_urls: list[str] = []
        if test_mode:
            self.logger.info(
                f"*** MODO PRUEBA: {TEST_MAX_SECTIONS} secciones | "
                f"{TEST_MAX_PAGES} páginas | "
                f"{TEST_MAX_ARTICLES} artículos/sección ***"
            )

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
                locale="en-US",
                viewport={"width": 1280, "height": 900},
            )

            # -------------------------------------------------------
            # PASO 1: Recolectar URLs con deduplicación global
            # -------------------------------------------------------
            article_links = {}

            sections_to_run = (
                SECTIONS[:TEST_MAX_SECTIONS] if self.test_mode else SECTIONS
            )

            for section_url, section_name in sections_to_run:
                self.logger.info(f"Recolectando: {section_name} ({section_url})")
                links = await self.seccion_segura(self._collect_section(context, section_url, section_name), "_collect_section")

                new_count = 0
                for link in links:
                    if link["url"] not in article_links:
                        article_links[link["url"]] = link
                        new_count += 1

                self.logger.info(
                    f"  → {len(links)} encontrados | {new_count} nuevos | "
                    f"Total global: {len(article_links)}"
                )
                await asyncio.sleep(DELAY_BETWEEN_SECTIONS)

            self.logger.info(f"Total URLs únicas: {len(article_links)}")

            # -------------------------------------------------------
            # PASO 2: Visitar cada artículo
            # -------------------------------------------------------
            records = []
            links_list = list(article_links.values())

            if self.test_mode:
                max_arts = TEST_MAX_ARTICLES * TEST_MAX_SECTIONS
                links_list = links_list[:max_arts]
                self.logger.info(f"Modo prueba: procesando {len(links_list)} artículos")

            for i, link_data in enumerate(links_list):
                self.logger.debug(f"[{i+1}/{len(links_list)}] {link_data['url']}")
                record = await self.articulo_seguro(context, link_data)
                if record:
                    records.append(record)
                    self.checkpoint(records)
                await asyncio.sleep(DELAY_BETWEEN_ARTICLES + random.uniform(0.1, 0.4))

            # -------------------------------------------------------
            # Reporte final de artículos sin fecha
            # -------------------------------------------------------
            self._log_no_date_report()

            await browser.close()
            return records

    def _log_no_date_report(self):
        """Imprime en consola y log el resumen de artículos sin fecha."""
        if self._no_date_count == 0:
            self.logger.info("✓ Todos los artículos tienen fecha de publicación.")
            return

        self.logger.warning(
            f"⚠ {self._no_date_count} artículo(s) sin fecha (publication_date = 'Not Found'):"
        )
        # Mostrar hasta 10 URLs en el log
        sample = self._no_date_urls[:10]
        for url in sample:
            self.logger.warning(f"  - {url}")
        if len(self._no_date_urls) > 10:
            self.logger.warning(
                f"  ... y {len(self._no_date_urls) - 10} más (ver log completo)"
            )

    # ------------------------------------------------------------------
    # Recolección paginada
    # ------------------------------------------------------------------

    async def _collect_section(
        self, context, base_url: str, section_name: str
    ) -> list[dict]:
        """Recorre todas las páginas de la sección con paginación /page/N/."""
        collected = {}
        page_num = 1
        max_pages = TEST_MAX_PAGES if self.test_mode else 9999

        while page_num <= max_pages:
            url = (
                base_url
                if page_num == 1
                else base_url.rstrip("/") + f"/page/{page_num}/"
            )
            page = await context.new_page()
            found_on_page = 0

            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=25_000)

                if resp and resp.status == 404:
                    self.logger.debug(f"  [{section_name}] Pág {page_num} → 404, fin")
                    break

                await page.wait_for_timeout(1500)
                await self._scroll_to_bottom(page)

                found_on_page = await self._extract_cards(page, collected, section_name)

                self.logger.debug(
                    f"  [{section_name}] Pág {page_num}: "
                    f"{found_on_page} nuevos | total: {len(collected)}"
                )

                if found_on_page == 0:
                    all_titles = await page.query_selector_all(
                        "div.category-post-item, a.post-title"
                    )
                    if len(all_titles) == 0:
                        self.logger.debug(f"  [{section_name}] Sin tarjetas, terminando")
                        break

                page_num += 1

            except PlaywrightTimeoutError:
                self.logger.warning(f"  Timeout: {url}")
                break
            except Exception as e:
                self.logger.error(f"  Error en {url}: {e}", exc_info=True)
                break
            finally:
                await cerrar_pagina(page)

            await asyncio.sleep(DELAY_BETWEEN_PAGES)

        return list(collected.values())

    async def _scroll_to_bottom(self, page) -> None:
        prev_height = -1
        for _ in range(5):
            h = await page.evaluate("document.body.scrollHeight")
            if h == prev_height:
                break
            prev_height = h
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(DELAY_SCROLL)

    async def _extract_cards(
        self, page, collected: dict, section_name: str
    ) -> int:
        """
        Extrae artículos desde div.titulcate > a.post-title.
        Un solo round-trip JS para eficiencia.
        """
        found_new = 0

        items = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                document.querySelectorAll('div.category-post-item a.post-title[href]').forEach(a => {
                    const href  = a.getAttribute('href') || '';
                    const title = (
                        a.getAttribute('title') ||
                        a.innerText ||
                        ''
                    ).replace(/\\s+/g, ' ').trim();

                    if (!href || !title || seen.has(href)) return;
                    seen.add(href);
                    results.push({ href, title });
                });

                return results;
            }
        """)

        for item in items:
            href = item.get("href", "").strip()
            title = clean_text(item.get("title", ""))

            if not href or not title:
                continue
            if not is_article_url(href):
                continue
            if href in collected:
                continue

            collected[href] = {
                "url": href,
                "title": title,
                "section": section_name,
                "publication_date": "",
            }
            found_new += 1

        return found_new

    # ------------------------------------------------------------------
    # Scraping del artículo individual
    # ------------------------------------------------------------------

    async def _scrape_article(self, context, link_data: dict) -> dict | None:
        page = await context.new_page()

        try:
            await page.goto(
                link_data["url"], wait_until="domcontentloaded", timeout=ARTICLE_TIMEOUT
            )
            await page.wait_for_timeout(1500)

            # -----------------------------------------------------------
            # Fecha: JSON-LD (Yoast schema)
            # script[type='application/ld+json'].yoast-schema-graph
            # -----------------------------------------------------------
            publication_date = ""

            jsonld_el = await page.query_selector(
                "script[type='application/ld+json'].yoast-schema-graph, "
                "script[type='application/ld+json'][class*='yoast']"
            )
            if jsonld_el:
                jsonld_text = await jsonld_el.inner_text()
                publication_date = extract_date_from_jsonld(jsonld_text)

            # Fallback: meta tag article:published_time
            if not publication_date:
                meta_el = await page.query_selector(
                    'meta[property="article:published_time"]'
                )
                if meta_el:
                    raw_dt = await meta_el.get_attribute("content")
                    if raw_dt:
                        publication_date = parse_iso_date(raw_dt)

            # Si no se encontró → marcar como "Not Found" y registrar
            if not publication_date:
                publication_date = "Not Found"
                self._no_date_count += 1
                self._no_date_urls.append(link_data["url"])
                self.logger.debug(f"  Sin fecha: {link_data['url']}")

            # -----------------------------------------------------------
            # Texto: div[class*='pf-content'] o div[class*='entry-content']
            # → todos los <p>  (excluye ads, banners, print button)
            # -----------------------------------------------------------
            content_div = await page.query_selector(
                "div.pf-content, div[class*='pf-content']"
            )

            if not content_div:
                content_div = await page.query_selector(
                    "div.entry-content, div[class*='entry-content'], "
                    "div[class*='post-content']"
                )

            if not content_div:
                content_div = await page.query_selector("article")

            if not content_div:
                self.logger.warning(f"Sin contenedor: {link_data['url']}")
                return None

            full_text = await content_div.evaluate("""
                (el) => {
                    const clone = el.cloneNode(true);

                    const remove_sels = [
                        // Publicidad / AdSense
                        '.adsensemedio', '[class*="adsensemedio"]',
                        '.alignleft.adsensemedio',
                        'ins.adsbygoogle', '.adsbygoogle',
                        '[id*="google_ads"]',
                        // Banners de anuncios inline (class "a-single")
                        '.a-single', '[class*="a-single"]',
                        // Botón print/PDF
                        '.printfriendly', '[class*="printfriendly"]',
                        '.pf-button', '[class*="pf-button"]',
                        // Artículos relacionados / notas recomendadas
                        '[class*="related"]', '[id*="related"]',
                        // Redes sociales y sharing
                        '[class*="share"]', '[class*="social"]',
                        // Scripts, estilos, figuras
                        'script', 'style', 'iframe',
                        'figure', 'figcaption',
                        // Navegación y comentarios
                        'nav', '#comments', '.comments-area',
                        // Imágenes de banner de secciones
                        'img[alt*="WhatsApp"]',
                        'a[href*="whatsapp"]',
                    ];

                    remove_sels.forEach(sel => {
                        try {
                            clone.querySelectorAll(sel).forEach(e => e.remove());
                        } catch(err) {}
                    });

                    const parts = [];
                    const seen = new Set();

                    clone.querySelectorAll('p').forEach(p => {
                        const text = (p.innerText || p.textContent || '')
                            .replace(/\\s+/g, ' ').trim();
                        // Filtrar párrafos muy cortos, vacíos o de espacio nbsp
                        if (text.length < 15 || text === '\\u00a0') return;
                        if (seen.has(text)) return;
                        seen.add(text);
                        parts.push(text);
                    });

                    return parts.join('\\n\\n');
                }
            """)

            full_text = clean_text(full_text) if full_text else ""

            if not full_text:
                self.logger.warning(f"Sin texto: {link_data['url']}")
                return None

            return {
                "url": link_data["url"],
                "title": link_data["title"],
                "section": link_data.get("section", ""),
                "publication_date": publication_date,
                "full_text": full_text,
            }

        except PlaywrightTimeoutError:
            self.logger.warning(f"Timeout: {link_data['url']}")
            return None
        except Exception as e:
            self.logger.error(f"Error en {link_data['url']}: {e}", exc_info=True)
            return None
        finally:
            await cerrar_pagina(page)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scraper The Costa Rica Star")
    parser.add_argument(
        "--test", action="store_true",
        help=(
            f"Modo prueba: {TEST_MAX_SECTIONS} secciones, "
            f"{TEST_MAX_PAGES} páginas, "
            f"{TEST_MAX_ARTICLES} artículos/sección"
        )
    )
    parser.add_argument("--output", default="output")
    parser.add_argument("--logs", default="logs")
    args = parser.parse_args()

    scraper = CostaRicaStarScraper(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
