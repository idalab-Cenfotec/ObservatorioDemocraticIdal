"""
theglobalcr.py
Scraper para The Global CR (https://theglobalcr.com/)

Secciones (6):
  - El País    : https://theglobalcr.com/el-pais/
  - El Mundo   : https://theglobalcr.com/el-mundo/
  - Economía   : https://theglobalcr.com/economia/
  - Política   : https://theglobalcr.com/politica/
  - Tecnología : https://theglobalcr.com/tecnologia/
  - Negocios   : https://theglobalcr.com/negocios/

Carga dinámica:
  - NO hay botón explícito — al llegar al final de la página,
    esperar unos segundos y los artículos se cargan automáticamente
  - Repetir scroll hasta que no aparezcan artículos nuevos

Estructura listado:
  - Contenedor : div[class*='td-module-meta-info']
  - Título y URL: h3[class*='entry-title'] > a  (title + href)

Estructura artículo:
  - Fecha : time[class*='td-module-date'][datetime]  (ISO 8601)
            Buscado sin jerarquía en el artículo
  - Texto : div[class*='tdb_single_content'] o div[class*='single_content']
            → p[class*='wp-block-paragraph']  y p genéricos

Deduplicación global entre secciones.
Modo prueba: --test limita secciones, scrolls y artículos.
"""

import asyncio
import re
import random
from datetime import datetime, timezone, timedelta

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.base_scraper import BaseScraper, CorteIncremental, cerrar_pagina

CR_TZ = timezone(timedelta(hours=-6))

BASE_URL = "https://theglobalcr.com"

SECTIONS = [
    ("https://theglobalcr.com/el-pais/",    "El País"),
    ("https://theglobalcr.com/el-mundo/",   "El Mundo"),
    ("https://theglobalcr.com/economia/",   "Economía"),
    ("https://theglobalcr.com/politica/",   "Política"),
    ("https://theglobalcr.com/tecnologia/", "Tecnología"),
    ("https://theglobalcr.com/negocios/",   "Negocios"),
]

ARTICLE_TIMEOUT = 22_000
DELAY_BETWEEN_ARTICLES = 1.0
DELAY_BETWEEN_SECTIONS = 2.5
# Espera después de llegar al fondo (auto-carga)
DELAY_AFTER_SCROLL = 3500   # ms — tiempo que tarda en cargar más artículos
DELAY_SCROLL_STEP = 800     # ms entre scrolls parciales

# Máximo de rondas de scroll por sección (cada ronda = scroll+espera)
MAX_SCROLL_ROUNDS = 40

# Modo prueba
TEST_MAX_SECTIONS = 2
TEST_MAX_SCROLL_ROUNDS = 3
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
    if not url or not url.startswith("https://theglobalcr.com/"):
        return False
    exclude = ["/category/", "/author/", "/tag/", "/page/",
               "/?", "/#", "/wp-", "/feed"]
    for pat in exclude:
        if pat in url:
            return False
    # Excluir URLs base de sección
    section_bases = [
        "theglobalcr.com/el-pais/", "theglobalcr.com/el-mundo/",
        "theglobalcr.com/economia/", "theglobalcr.com/politica/",
        "theglobalcr.com/tecnologia/", "theglobalcr.com/negocios/",
    ]
    for base in section_bases:
        if url.rstrip("/") + "/" == f"https://{base}":
            return False
    path = url.replace("https://theglobalcr.com/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 2


class TheGlobalCRScraper(BaseScraper):
    """
    Scraper para The Global CR.
    - 6 secciones con auto-carga al llegar al fondo (sin botón explícito)
    - Scroll progresivo → espera DELAY_AFTER_SCROLL → detecta nuevos artículos
    - Deduplicación global entre secciones
    - Modo prueba limita secciones, scrolls y artículos
    """

    SOURCE_NAME = "theglobalcr"
    BASE_URL = BASE_URL + "/"

    def __init__(self, output_dir="output", log_dir="logs", test_mode=False):
        super().__init__(output_dir=output_dir, log_dir=log_dir)
        self.test_mode = test_mode
        if test_mode:
            self.logger.info(
                f"*** MODO PRUEBA: {TEST_MAX_SECTIONS} secciones | "
                f"{TEST_MAX_SCROLL_ROUNDS} scrolls | "
                f"{TEST_MAX_ARTICLES} artículos/sección ***"
            )

    def scrape(self) -> list[dict]:
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
                record = await self.articulo_seguro(context, link_data)
                if record:
                    records.append(record)
                    self.checkpoint(records)
                await asyncio.sleep(DELAY_BETWEEN_ARTICLES + random.uniform(0.1, 0.4))

            await browser.close()
            return records

    # ------------------------------------------------------------------
    # Recolección con auto-carga al fondo
    # ------------------------------------------------------------------

    async def _collect_section(
        self, context, section_url: str, section_name: str
    ) -> list[dict]:
        """
        Navega la sección con scroll infinito auto-cargable.
        Estrategia por ronda:
          1. Scroll al final de la página
          2. Esperar DELAY_AFTER_SCROLL ms (tiempo de carga automática)
          3. Extraer artículos visibles
          4. Si no llegaron nuevos artículos tras 2 rondas seguidas → terminar
        """
        page = await context.new_page()
        collected = {}
        max_rounds = TEST_MAX_SCROLL_ROUNDS if self.test_mode else MAX_SCROLL_ROUNDS
        no_new_streak = 0  # rondas consecutivas sin artículos nuevos

        try:
            await page.goto(section_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2500)

            # Extracción inicial antes del primer scroll
            await self._extract_cards(page, collected, section_name)

            corte = CorteIncremental(self)
            for round_num in range(max_rounds):
                if self.tiempo_agotado(0.5):
                    self.logger.warning(f"  [{section_name}] Tiempo del listado agotado, se pasa a los artículos")
                    break
                prev_count = len(collected)

                # Scroll progresivo hasta el fondo
                await self._scroll_to_bottom_gradual(page)

                # Esperar la auto-carga de nuevos artículos
                await page.wait_for_timeout(DELAY_AFTER_SCROLL)

                # Extraer artículos ahora visibles
                await self._extract_cards(page, collected, section_name)
                new_this_round = len(collected) - prev_count

                self.logger.debug(
                    f"  [{section_name}] Ronda {round_num + 1}: "
                    f"+{new_this_round} | total: {len(collected)}"
                )

                # Varias rondas seguidas con solo URLs ya cargadas -> fin de sección
                if corte.pagina([l["url"] for l in list(collected.values())[prev_count:]]):
                    self.logger.info(f"  [{section_name}] Ronda {round_num + 1}: solo URLs ya conocidas, fin de sección")
                    break

                if new_this_round == 0:
                    no_new_streak += 1
                    if no_new_streak >= 2:
                        self.logger.debug(
                            f"  [{section_name}] Sin nuevos artículos en 2 rondas, "
                            "sección completa"
                        )
                        break
                else:
                    no_new_streak = 0

        except PlaywrightTimeoutError:
            self.logger.warning(f"Timeout en sección: {section_url}")
        except Exception as e:
            self.logger.error(f"Error en {section_url}: {e}", exc_info=True)
        finally:
            await cerrar_pagina(page)

        return list(collected.values())

    async def _scroll_to_bottom_gradual(self, page) -> None:
        """
        Scroll gradual hasta el final — importante para trigger de auto-carga.
        Hace pasos de 600px para que el IntersectionObserver de la página se active.
        """
        total_height = await page.evaluate("document.body.scrollHeight")
        current_pos = await page.evaluate("window.scrollY")
        step = 600

        while current_pos < total_height:
            current_pos = min(current_pos + step, total_height)
            await page.evaluate(f"window.scrollTo(0, {current_pos})")
            await page.wait_for_timeout(DELAY_SCROLL_STEP)
            # Actualizar total_height por si se cargaron más artículos
            total_height = await page.evaluate("document.body.scrollHeight")

    async def _extract_cards(
        self, page, collected: dict, section_name: str
    ) -> int:
        """
        Extrae artículos desde div[class*='td-module-meta-info'].
        Busca h3[class*='entry-title'] > a dentro de cada div.
        Un solo round-trip JS.
        """
        found_new = 0

        items = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // Buscar todos los div con clase que contenga 'module-meta-info'
                document.querySelectorAll(
                    'div[class*="td-module-meta-info"], '
                    + 'div[class*="module-meta-info"]'
                ).forEach(metaDiv => {
                    // h3 con clase entry-title dentro del div
                    const h3 = metaDiv.querySelector(
                        'h3[class*="entry-title"], h2[class*="entry-title"]'
                    );
                    if (!h3) return;

                    const anchor = h3.querySelector('a[href]');
                    if (!anchor) return;

                    const href  = anchor.getAttribute('href') || '';
                    const title = (
                        anchor.getAttribute('title') ||
                        anchor.innerText ||
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
            # Fecha: time[class*='td-module-date'][datetime]
            # Buscar sin jerarquía en toda la página
            # -----------------------------------------------------------
            publication_date = ""

            time_el = await page.query_selector(
                "time[class*='td-module-date'][datetime], "
                "time[class*='entry-date'][datetime]"
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
            # Sección: intentar refinar desde categoría del artículo
            # -----------------------------------------------------------
            section = link_data.get("section", "")
            try:
                cat_el = await page.query_selector(
                    "a.td-post-category, [class*='td-post-cat'] a"
                )
                if cat_el:
                    cat_text = clean_text(await cat_el.inner_text())
                    if cat_text:
                        section = cat_text
            except Exception:
                pass

            # -----------------------------------------------------------
            # Texto: div[class*='tdb_single_content'] sin jerarquía
            # → p[class*='wp-block-paragraph'] y p genéricos
            # -----------------------------------------------------------
            content_div = await page.query_selector(
                "div[class*='tdb_single_content'], "
                "div[class*='td-post-content'], "
                "div[class*='tagdiv-type']"
            )

            # Fallback: div.entry-content
            if not content_div:
                content_div = await page.query_selector(
                    "div.entry-content, div[class*='entry-content']"
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
                        // Publicidad Tagdiv
                        '.td-a-ad', '[class*="td-a-ad"]',
                        '.id_top_ad', '.id_bottom_ad',
                        '[class*="id_ad_content"]',
                        '.tdc-a-ad', '[class*="tdc-a-ad"]',
                        'ins.adsbygoogle', '.adsbygoogle',
                        // Imágenes destacadas
                        '.tdb_single_featured_image',
                        '[class*="featured_image"]',
                        // Figuras (imágenes dentro del artículo)
                        'figure', 'figcaption',
                        // Redes sociales y sharing
                        '[class*="share"]', '[class*="social"]',
                        '[class*="sharedaddy"]',
                        // Artículos relacionados
                        '[class*="related"]', '[id*="related"]',
                        // Navegación
                        'nav', '[class*="post-navigation"]',
                        // Comentarios
                        '#comments', '[class*="comment"]',
                        // Scripts y estilos
                        'script', 'style', 'iframe',
                        // Bloque de autor
                        '[class*="author"]',
                        // Tags
                        '[class*="td-tags"]', '[class*="tags-links"]',
                    ];

                    remove_sels.forEach(sel => {
                        try {
                            clone.querySelectorAll(sel).forEach(e => e.remove());
                        } catch(err) {}
                    });

                    const parts = [];
                    const seen = new Set();

                    // Priorizar p[class*='wp-block-paragraph'] y p genéricos
                    // También incluir li para listas y blockquote
                    clone.querySelectorAll(
                        'p[class*="wp-block-paragraph"], p, li, blockquote p'
                    ).forEach(el => {
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
            await cerrar_pagina(page)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scraper The Global CR")
    parser.add_argument(
        "--test", action="store_true",
        help=(
            f"Modo prueba: {TEST_MAX_SECTIONS} secciones, "
            f"{TEST_MAX_SCROLL_ROUNDS} scrolls, "
            f"{TEST_MAX_ARTICLES} artículos/sección"
        )
    )
    parser.add_argument("--output", default="output")
    parser.add_argument("--logs", default="logs")
    args = parser.parse_args()

    scraper = TheGlobalCRScraper(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
