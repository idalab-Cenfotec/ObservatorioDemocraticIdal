"""
rumboeconomico.py
Scraper para Rumbo Económico (https://rumboeconomico.net/)

Secciones (7):
  - Finanzas    : https://rumboeconomico.net/category/finanzas/
  - Negocios    : https://rumboeconomico.net/category/negocios/
  - Economía    : https://rumboeconomico.net/category/economia/
  - Pymes       : https://rumboeconomico.net/category/pymes/
  - Tendencias  : https://rumboeconomico.net/category/tendencias/
  - Entrevistas : https://rumboeconomico.net/category/entrevistas/
  - Opinión     : https://rumboeconomico.net/category/opinion/

Paginación: /page/N/ (WordPress + Elementor)

Estructura listado:
  - Contenedor : article[class*='elementor-post'][role='listitem']
  - Título y URL: h2[class*='elementor-post__title'] > a
  - Fecha listado: span[class*='elementor-post-date'] ("22 May, 2026")

Estructura artículo:
  - Fecha : li[itemprop='datePublished'] time  ("22 May, 2026")
  - Texto : div[class*='elementor-widget-container'] → p[class*='wp-block'] y p

Deduplicación global entre secciones.
Modo prueba: --test limita secciones, páginas y artículos.
"""

import asyncio
import re
import random
from datetime import datetime, timezone, timedelta

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.base_scraper import BaseScraper

CR_TZ = timezone(timedelta(hours=-6))

BASE_URL = "https://rumboeconomico.net"

SECTIONS = [
    ("https://rumboeconomico.net/category/finanzas/",    "Finanzas"),
    ("https://rumboeconomico.net/category/negocios/",    "Negocios"),
    ("https://rumboeconomico.net/category/economia/",    "Economía"),
    ("https://rumboeconomico.net/category/pymes/",       "Pymes"),
    ("https://rumboeconomico.net/category/tendencias/",  "Tendencias"),
    ("https://rumboeconomico.net/category/entrevistas/", "Entrevistas"),
    ("https://rumboeconomico.net/category/opinion/",     "Opinión"),
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

MESES_ABR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    # Español abreviado
    "ene": 1, "abr": 4, "ago": 8,
}

MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def parse_rumbo_date(text: str) -> str:
    """
    Parsea varios formatos de fecha de Rumbo Económico:
      - "22 May, 2026"     → "2026-05-22"
      - "22 mayo, 2026"    → "2026-05-22"
      - "22 de mayo, 2026" → "2026-05-22"
      - ISO 8601 parcial   → "2026-05-22"
    """
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)

    # Formato "DD Mes, YYYY" o "DD Mes YYYY"  (inglés o español)
    match = re.search(r"(\d{1,2})\s+(\w+),?\s+(\d{4})", text)
    if match:
        day = int(match.group(1))
        mes = match.group(2)
        year = int(match.group(3))
        month = MESES_ABR.get(mes[:3], 0) or MESES_ES.get(mes, 0)
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # Formato "DD de Mes de YYYY"
    match2 = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", text)
    if match2:
        day = int(match2.group(1))
        month = MESES_ES.get(match2.group(2), 0)
        year = int(match2.group(3))
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # ISO parcial "YYYY-MM-DD"
    match3 = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match3:
        return f"{match3.group(1)}-{match3.group(2)}-{match3.group(3)}"

    return ""


def parse_iso_date(raw: str) -> str:
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        return dt.astimezone(CR_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return raw[:10] if raw else ""


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_article_url(url: str) -> bool:
    if not url or not url.startswith("https://rumboeconomico.net/"):
        return False
    exclude = ["/category/", "/author/", "/tag/", "/page/",
               "/?", "/#", "/wp-", "/feed"]
    for pat in exclude:
        if pat in url:
            return False
    path = url.replace("https://rumboeconomico.net/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 2  # seccion/slug


class RumboEconomicoScraper(BaseScraper):
    """
    Scraper para Rumbo Económico.
    - 7 secciones con paginación /page/N/
    - Scroll al final antes de cambiar página
    - Deduplicación global entre secciones
    - Modo prueba: limita secciones, páginas y artículos
    """

    SOURCE_NAME = "rumboeconomico"
    BASE_URL = BASE_URL + "/"

    def __init__(self, output_dir="output", log_dir="logs", test_mode=False):
        super().__init__(output_dir=output_dir, log_dir=log_dir)
        self.test_mode = test_mode
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
                locale="es-CR",
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
                links = await self._collect_section(context, section_url, section_name)

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
                record = await self._scrape_article(context, link_data)
                if record:
                    records.append(record)
                await asyncio.sleep(DELAY_BETWEEN_ARTICLES + random.uniform(0.1, 0.4))

            await browser.close()
            return records

    # ------------------------------------------------------------------
    # Recolección paginada
    # ------------------------------------------------------------------

    async def _collect_section(
        self, context, base_url: str, section_name: str
    ) -> list[dict]:
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
                    # Verificar si hay artículos en la página aunque todos ya estén en collected
                    all_articles = await page.query_selector_all(
                        "article[class*='elementor-post'], "
                        "h2[class*='elementor-post__title']"
                    )
                    if len(all_articles) == 0:
                        self.logger.debug(f"  [{section_name}] Sin artículos, terminando")
                        break

                page_num += 1

            except PlaywrightTimeoutError:
                self.logger.warning(f"  Timeout: {url}")
                break
            except Exception as e:
                self.logger.error(f"  Error en {url}: {e}", exc_info=True)
                break
            finally:
                await page.close()

            await asyncio.sleep(DELAY_BETWEEN_PAGES)

        return list(collected.values())

    async def _scroll_to_bottom(self, page) -> None:
        prev_height = -1
        for _ in range(6):
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
        Extrae tarjetas de artículos.
        Estrategia JS en un solo round-trip:
          1. Busca article[class*='elementor-post'][role='listitem']
          2. Dentro de cada uno: h2[class*='elementor-post__title'] > a
          3. Fecha: span[class*='elementor-post-date']
        """
        found_new = 0

        items = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // Buscar artículos Elementor en el listado
                const articles = document.querySelectorAll(
                    'article[class*="elementor-post"][role="listitem"], '
                    + 'article[class*="elementor-post"]'
                );

                articles.forEach(art => {
                    // Título y URL
                    const titleAnchor = art.querySelector(
                        'h2[class*="elementor-post__title"] a, '
                        + 'h3[class*="elementor-post__title"] a'
                    );
                    if (!titleAnchor) return;

                    const href  = titleAnchor.getAttribute('href') || '';
                    const title = (titleAnchor.innerText || '').replace(/\\s+/g, ' ').trim();

                    if (!href || !title || seen.has(href)) return;
                    seen.add(href);

                    // Fecha del listado
                    let date = '';
                    const dateEl = art.querySelector(
                        'span[class*="elementor-post-date"], '
                        + '[class*="post-date"]'
                    );
                    if (dateEl) date = (dateEl.innerText || '').trim();

                    results.push({ href, title, date });
                });

                return results;
            }
        """)

        for item in items:
            href = item.get("href", "").strip()
            title = clean_text(item.get("title", ""))
            date_text = item.get("date", "")

            if not href or not is_article_url(href):
                continue
            if href in collected:
                continue
            if not title:
                continue

            pub_date = parse_rumbo_date(date_text) if date_text else ""

            collected[href] = {
                "url": href,
                "title": title,
                "section": section_name,
                "publication_date": pub_date,
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
            # Fecha: li[itemprop='datePublished'] time
            # Buscar sin jerarquía
            # -----------------------------------------------------------
            publication_date = link_data.get("publication_date", "")

            date_li = await page.query_selector(
                "li[itemprop='datePublished'] time, "
                "[itemprop='datePublished'] time"
            )
            if date_li:
                date_text = await date_li.inner_text()
                parsed = parse_rumbo_date(date_text)
                if parsed:
                    publication_date = parsed

            # Fallback: meta tag (puede tener hora exacta)
            if not publication_date:
                meta_el = await page.query_selector(
                    'meta[property="article:published_time"]'
                )
                if meta_el:
                    raw_dt = await meta_el.get_attribute("content")
                    if raw_dt:
                        publication_date = parse_iso_date(raw_dt)

            # -----------------------------------------------------------
            # Sección: refinar desde li[itemprop='about'] si existe
            # -----------------------------------------------------------
            section = link_data.get("section", "")
            try:
                cat_li = await page.query_selector(
                    "li[itemprop='about'] a, "
                    "[class*='terms-list-item']"
                )
                if cat_li:
                    cat_text = clean_text(await cat_li.inner_text())
                    if cat_text:
                        section = cat_text
            except Exception:
                pass

            # -----------------------------------------------------------
            # Texto: div[class*='elementor-widget-container']
            # que contenga párrafos de artículo
            # Estrategia: buscar el contenedor con más párrafos
            # -----------------------------------------------------------
            full_text = await page.evaluate("""
                () => {
                    // Buscar todos los elementor-widget-container
                    const containers = document.querySelectorAll(
                        'div[class*="elementor-widget-container"]'
                    );

                    // Primero el widget de contenido de la nota. "El que tenga más párrafos"
                    // falla: la barra lateral "Últimas Noticias" tiene tantos o más párrafos
                    // que el cuerpo y en ~28% de las notas ganaba y se guardaba como texto.
                    let bestContainer = document.querySelector(
                        '.elementor-widget-theme-post-content .elementor-widget-container'
                    );
                    let bestCount = bestContainer ? bestContainer.querySelectorAll('p').length : 0;

                    // Respaldo: el contenedor con más párrafos
                    if (bestCount < 1) {
                        bestContainer = null;
                        containers.forEach(c => {
                            const pCount = c.querySelectorAll('p').length;
                            if (pCount > bestCount) {
                                bestCount = pCount;
                                bestContainer = c;
                            }
                        });
                    }

                    if (!bestContainer || bestCount < 2) return '';

                    const clone = bestContainer.cloneNode(true);

                    // Limpiar elementos no deseados
                    const remove_sels = [
                        'figure', 'figcaption',
                        '[class*="sharedaddy"]', '[class*="sd-block"]',
                        '[class*="jetpack"]',
                        'nav', '[class*="navigation"]',
                        '#comments', '[class*="comments"]',
                        '[class*="related"]',
                        'script', 'style', 'iframe',
                        '[class*="ad-"]', '[id*="ad"]',
                        // Bloque "Más columnas" al final
                        'a[href*="/category/"]',
                    ];

                    remove_sels.forEach(sel => {
                        try {
                            clone.querySelectorAll(sel).forEach(e => e.remove());
                        } catch(err) {}
                    });

                    const parts = [];
                    const seen = new Set();

                    // Párrafos + ítems de lista
                    clone.querySelectorAll('p, li').forEach(el => {
                        const text = (el.innerText || el.textContent || '')
                            .replace(/\\s+/g, ' ').trim();
                        if (text.length < 15) return;
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
                "section": section,
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
            await page.close()


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scraper Rumbo Económico")
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

    scraper = RumboEconomicoScraper(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
