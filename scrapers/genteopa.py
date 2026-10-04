"""
genteopa.py
Scraper para Gente OPA (https://genteopa.com/)

Secciones (5):
  - Nacionales : https://genteopa.com/central/categoria/nacionales/
  - Sucesos    : https://genteopa.com/central/categoria/sucesos/
  - Política   : https://genteopa.com/central/categoria/politica/
  - Tecnología : https://genteopa.com/central/categoria/tecnologia/
  - Economía   : https://genteopa.com/central/categoria/economia/

Paginación: /pagina/2/, /pagina/3/ (WordPress en español)

Estructura listado:
  - Título y URL: h5.wp-block-post-title > a  (texto + href)
  - Fecha       : div.wp-block-post-date > time[datetime]  (ISO 8601)

Estructura artículo:
  - Texto : div.entry-content  → todos los <p> y <h3>
            (excluye figuras, sharing, comentarios, navegación)
  - Fecha : del listado (ISO 8601); fallback meta tag

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
from scrapers.base_scraper import BaseScraper, CorteIncremental

CR_TZ = timezone(timedelta(hours=-6))

BASE_URL = "https://genteopa.com"

SECTIONS = [
    ("https://genteopa.com/central/categoria/nacionales/",  "Nacionales"),
    ("https://genteopa.com/central/categoria/sucesos/",     "Sucesos"),
    ("https://genteopa.com/central/categoria/politica/",    "Política"),
    ("https://genteopa.com/central/categoria/tecnologia/",  "Tecnología"),
    ("https://genteopa.com/central/categoria/economia/",    "Economía"),
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
    if not url or not url.startswith("https://genteopa.com/"):
        return False
    exclude = ["/categoria/", "/author/", "/tag/", "/pagina/",
               "/?", "/#", "/wp-", "/feed", "/central/$"]
    for pat in exclude:
        if pat in url:
            return False
    # Excluir URLs base de sección
    for sec_url, _ in SECTIONS:
        if url.rstrip("/") + "/" == sec_url or url == sec_url.rstrip("/"):
            return False
    path = url.replace("https://genteopa.com/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 2


class GenteOPAScraper(BaseScraper):
    """
    Scraper para Gente OPA.
    - 5 secciones con paginación /pagina/N/
    - Scroll al final antes de cambiar página
    - Deduplicación global entre secciones
    - Modo prueba limita secciones, páginas y artículos
    """

    SOURCE_NAME = "genteopa"
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
            links_list = [l for l in article_links.values() if not self.debe_omitir(l["url"])]
            if self.incremental:
                self.logger.info(
                    f"Artículos nuevos por visitar: {len(links_list)} "
                    f"({self.omitidos_conocidos} ya estaban en la base)"
                )

            if self.test_mode:
                max_arts = TEST_MAX_ARTICLES * TEST_MAX_SECTIONS
                links_list = links_list[:max_arts]
                self.logger.info(f"Modo prueba: procesando {len(links_list)} artículos")

            for i, link_data in enumerate(links_list):
                if self.tiempo_agotado():
                    break
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
        """
        Paginación en español: /pagina/2/, /pagina/3/ ...
        """
        collected = {}
        page_num = 1
        max_pages = TEST_MAX_PAGES if self.test_mode else 9999

        corte = CorteIncremental(self)

        while page_num <= max_pages:
            if self.tiempo_agotado(0.5):
                self.logger.warning(f"  [{section_name}] Tiempo del listado agotado, se pasa a los artículos")
                break
            url = (
                base_url
                if page_num == 1
                else base_url.rstrip("/") + f"/pagina/{page_num}/"
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

                n_antes = len(collected)
                found_on_page = await self._extract_cards(page, collected, section_name)
                # Varias páginas/rondas seguidas con solo URLs ya cargadas -> fin de sección
                if corte.pagina([l['url'] for l in list(collected.values())[n_antes:]]):
                    self.logger.info(f"  [{section_name}] Pág {page_num}: solo URLs ya conocidas, fin de sección")
                    break

                self.logger.debug(
                    f"  [{section_name}] Pág {page_num}: "
                    f"{found_on_page} nuevos | total: {len(collected)}"
                )

                if found_on_page == 0:
                    # Verificar si hay títulos en la página
                    all_titles = await page.query_selector_all(
                        "h5.wp-block-post-title, h5[class*='wp-block-post-title']"
                    )
                    if len(all_titles) == 0:
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
        Extrae artículos desde h5.wp-block-post-title > a.
        Fecha: busca el time[datetime] más cercano al h5 en el mismo bloque li.
        Un solo round-trip JS.
        """
        found_new = 0

        items = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // h5 con clase wp-block-post-title contiene el enlace
                document.querySelectorAll(
                    'h5.wp-block-post-title, h5[class*="wp-block-post-title"]'
                ).forEach(h5 => {
                    const anchor = h5.querySelector('a[href]');
                    if (!anchor) return;

                    const href  = anchor.getAttribute('href') || '';
                    const title = (anchor.innerText || '').replace(/\\s+/g, ' ').trim();

                    if (!href || !title || seen.has(href)) return;
                    seen.add(href);

                    // Buscar fecha en el li padre o en el bloque contenedor
                    let date = '';
                    const container = h5.closest('li, [class*="wp-block-post"], article');
                    if (container) {
                        const timeEl = container.querySelector('time[datetime]');
                        if (timeEl) date = timeEl.getAttribute('datetime') || '';
                    }

                    results.push({ href, title, date });
                });

                return results;
            }
        """)

        for item in items:
            href = item.get("href", "").strip()
            title = clean_text(item.get("title", ""))
            date_raw = item.get("date", "")

            if not href or not title:
                continue
            if not is_article_url(href):
                continue
            if href in collected:
                continue

            pub_date = parse_iso_date(date_raw) if date_raw else ""

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
            # Fecha: refinar con time[datetime] del artículo
            # La del listado ya viene en ISO; usar la del artículo
            # si está disponible (puede tener zona horaria exacta)
            # -----------------------------------------------------------
            publication_date = link_data.get("publication_date", "")

            # Buscar time[datetime] específico del artículo
            time_el = await page.query_selector(
                "div.wp-block-post-date time[datetime], "
                "[class*='post-date'] time[datetime], "
                "time.entry-date[datetime]"
            )
            if time_el:
                raw_dt = await time_el.get_attribute("datetime")
                if raw_dt:
                    publication_date = parse_iso_date(raw_dt)

            # Fallback: meta tag
            if not publication_date:
                meta_el = await page.query_selector(
                    'meta[property="article:published_time"]'
                )
                if meta_el:
                    raw_dt = await meta_el.get_attribute("content")
                    if raw_dt:
                        publication_date = parse_iso_date(raw_dt)

            # -----------------------------------------------------------
            # Sección: refinar desde categoría del artículo
            # -----------------------------------------------------------
            section = link_data.get("section", "")
            try:
                cat_el = await page.query_selector(
                    "div.taxonomy-category a, "
                    "[class*='taxonomy-category'] a, "
                    "a[rel='tag'][class*='category']"
                )
                if cat_el:
                    cat_text = clean_text(await cat_el.inner_text())
                    if cat_text:
                        section = cat_text
            except Exception:
                pass

            # -----------------------------------------------------------
            # Texto: div.entry-content → todos los <p> y <h3>
            # (clase WordPress estándar para contenido del artículo)
            # -----------------------------------------------------------
            content_div = await page.query_selector(
                "div.entry-content, "
                "div[class*='entry-content'], "
                "div.wp-block-post-content"
            )

            if not content_div:
                content_div = await page.query_selector("article main, main article")

            if not content_div:
                self.logger.warning(f"Sin contenedor: {link_data['url']}")
                return None

            full_text = await content_div.evaluate("""
                (el) => {
                    const clone = el.cloneNode(true);

                    const remove_sels = [
                        // Figuras e imágenes
                        'figure', 'figcaption',
                        '[class*="wp-block-image"]',
                        // Sharing (AddToAny)
                        '[class*="addtoany"]', '[class*="a2a_"]',
                        '.addtoany_share_save_container',
                        // Comentarios (wpDiscuz)
                        '[class*="wpdiscuz"]', '[id*="wpdiscuz"]',
                        '#comments', '.comments-area',
                        '[id*="wpd-"]', '[class*="wpd-"]',
                        // Navegación anterior/siguiente
                        '[class*="post-navigation"]',
                        'nav',
                        // Artículos relacionados
                        '[class*="related"]', '[id*="related"]',
                        // Rating / votos
                        '[id*="wpd-post-rating"]',
                        // Scripts, estilos, iframes
                        'script', 'style', 'iframe',
                        // Botones
                        'button',
                        // Redes sociales
                        '[class*="social"]',
                    ];

                    remove_sels.forEach(sel => {
                        try {
                            clone.querySelectorAll(sel).forEach(e => e.remove());
                        } catch(err) {}
                    });

                    const parts = [];
                    const seen = new Set();

                    // Párrafos y subtítulos del artículo
                    clone.querySelectorAll('p, h2, h3, h4, li').forEach(el => {
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

    parser = argparse.ArgumentParser(description="Scraper Gente OPA")
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

    scraper = GenteOPAScraper(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
