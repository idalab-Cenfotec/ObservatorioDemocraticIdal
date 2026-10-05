"""
ticotimes.py
Scraper para The Tico Times (https://ticotimes.net/)

Secciones (2):
  - Local News   : https://ticotimes.net/
  - Expat Living : https://ticotimes.net/categories/topics/expat-living

Carga dinámica con botón específico:
  <a class="td_ajax_load_more td_ajax_load_more_js"
     id="next-page-tdi_50">Load more</a>
  IMPORTANTE: NO hacer scroll hasta el fondo — solo hasta encontrar
  el botón visible, hacer clic y esperar la carga.

Estructura listado:
  - Contenedor : div[class*='td-module-meta-info']
  - Título y URL: h3[class*='entry-title'] > a  (title + href)

Estructura artículo:
  - Fecha : div.tdb-block-inner > time[class*='td-module-date'][datetime]
            (ISO 8601 con timezone: "2026-05-24T07:03:26-06:00")
  - Texto : div.tdb-block-inner.td-fix-index → todos los <p>
            (excluye AdThrive ads, related, sharing, nav)

Deduplicación global entre secciones.
Modo prueba: --test limita secciones, clics y artículos.

Nota: sitio principalmente en inglés → language = 'en'.
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

BASE_URL = "https://ticotimes.net"

SECTIONS = [
    ("https://ticotimes.net/",                                  "Local News"),
    ("https://ticotimes.net/categories/topics/expat-living",    "Expat Living"),
]

ARTICLE_TIMEOUT = 25_000
DELAY_BETWEEN_ARTICLES = 1.2
DELAY_BETWEEN_SECTIONS = 3.0
DELAY_AFTER_LOAD_MORE = 3500   # ms tras clic en "Load more"
DELAY_SCROLL_STEP = 600        # ms entre pasos de scroll

MAX_LOAD_MORE_CLICKS = 100

# Modo prueba
TEST_MAX_SECTIONS = 2
TEST_MAX_LOAD_MORE = 3
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
    if not url or not url.startswith("https://ticotimes.net/"):
        return False
    exclude = ["/categories/", "/author/", "/tag/", "/page/",
               "/?", "/#", "/wp-", "/feed", "/topics/"]
    for pat in exclude:
        if pat in url:
            return False
    # Artículos tienen formato /YYYY/MM/DD/slug
    path = url.replace("https://ticotimes.net/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 2


class TicoTimesScraper(BaseScraper):
    """
    Scraper para The Tico Times.
    - 2 secciones con botón 'Load more' específico por ID
    - Scroll suave hasta el botón (no hasta el fondo)
    - Deduplicación global entre secciones
    - Modo prueba limita secciones, clics y artículos
    """

    SOURCE_NAME = "ticotimes"
    BASE_URL = BASE_URL + "/"

    def __init__(self, output_dir="output", log_dir="logs", test_mode=False):
        super().__init__(output_dir=output_dir, log_dir=log_dir)
        self.test_mode = test_mode
        if test_mode:
            self.logger.info(
                f"*** MODO PRUEBA: {TEST_MAX_SECTIONS} secciones | "
                f"{TEST_MAX_LOAD_MORE} 'Load more' | "
                f"{TEST_MAX_ARTICLES} artículos/sección ***"
            )

    def scrape(self) -> list[dict]:
        return asyncio.run(self._scrape_async())

    async def _scrape_async(self) -> list[dict]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                viewport={"width": 1280, "height": 900},
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
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
                await asyncio.sleep(DELAY_BETWEEN_ARTICLES + random.uniform(0.2, 0.8))

                if (i + 1) % 25 == 0:
                    self.logger.info(f"  Pausa tras {i+1} artículos...")
                    await asyncio.sleep(5)

            await browser.close()
            return records

    # ------------------------------------------------------------------
    # Recolección con botón "Load more"
    # ------------------------------------------------------------------

    async def _collect_section(
        self, context, section_url: str, section_name: str
    ) -> list[dict]:
        """
        Carga la sección con scroll suave hasta el botón + clic.
        El botón es específico: a.td_ajax_load_more.td_ajax_load_more_js
        con id "next-page-tdi_50" (u otro id similar).
        """
        page = await context.new_page()
        collected = {}
        max_clicks = TEST_MAX_LOAD_MORE if self.test_mode else MAX_LOAD_MORE_CLICKS

        try:
            await page.goto(section_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2500)

            # Extracción inicial
            await self._extract_cards(page, collected, section_name)

            click_count = 0
            corte = CorteIncremental(self)
            while click_count <= max_clicks:
                if self.tiempo_agotado(0.5):
                    self.logger.warning(f"  [{section_name}] Tiempo del listado agotado, se pasa a los artículos")
                    break
                prev_count = len(collected)

                # Scroll suave hasta encontrar el botón "Load more"
                clicked = await self._scroll_to_and_click_load_more(page)

                if not clicked:
                    self.logger.debug(f"  [{section_name}] Sin botón 'Load more', fin")
                    break

                click_count += 1

                # Esperar carga de nuevos artículos
                await page.wait_for_timeout(DELAY_AFTER_LOAD_MORE)

                # Extraer artículos nuevos
                await self._extract_cards(page, collected, section_name)
                new_this_round = len(collected) - prev_count

                # Varias rondas seguidas con solo URLs ya cargadas -> fin de sección
                if corte.pagina([l["url"] for l in list(collected.values())[prev_count:]]):
                    self.logger.info(f"  [{section_name}] Clic {click_count}: solo URLs ya conocidas, fin de sección")
                    break

                self.logger.debug(
                    f"  [{section_name}] Clic {click_count}: "
                    f"+{new_this_round} | total: {len(collected)}"
                )

        except PlaywrightTimeoutError:
            self.logger.warning(f"Timeout en sección: {section_url}")
        except Exception as e:
            self.logger.error(f"Error en {section_url}: {e}", exc_info=True)
        finally:
            await cerrar_pagina(page)

        return list(collected.values())

    async def _scroll_to_and_click_load_more(self, page) -> bool:
        """
        Busca el botón 'Load more' específico de Tagdiv:
          a.td_ajax_load_more.td_ajax_load_more_js

        Estrategia:
          1. Buscar el botón en el DOM actual
          2. Si existe, hacer scroll suave hasta él y hacer clic
          3. Retorna True si encontró y clicó, False si no existe
        """
        try:
            # Selector específico: clase doble td_ajax_load_more td_ajax_load_more_js
            btn = await page.query_selector(
                "a.td_ajax_load_more.td_ajax_load_more_js, "
                "a[class*='td_ajax_load_more']"
            )

            if not btn:
                return False

            # Verificar visibilidad
            is_visible = await btn.is_visible()
            if not is_visible:
                # Intentar scroll suave hasta el botón para hacerlo visible
                await btn.scroll_into_view_if_needed()
                await page.wait_for_timeout(800)
                is_visible = await btn.is_visible()
                if not is_visible:
                    return False

            # Scroll suave hasta el botón (pasos de 300px para no saltarlo)
            btn_y = await btn.evaluate("el => el.getBoundingClientRect().top + window.scrollY")
            current_y = await page.evaluate("window.scrollY")
            target_y = max(0, btn_y - 300)  # un poco antes del botón

            while current_y < target_y:
                current_y = min(current_y + 300, target_y)
                await page.evaluate(f"window.scrollTo(0, {current_y})")
                await page.wait_for_timeout(DELAY_SCROLL_STEP)

            await page.wait_for_timeout(500)

            # Clic en el botón
            try:
                await btn.click(timeout=5000)
            except Exception:
                await page.evaluate("(el) => el.click()", btn)

            return True

        except Exception as e:
            self.logger.debug(f"Error buscando 'Load more': {e}")
            return False

    async def _extract_cards(
        self, page, collected: dict, section_name: str
    ) -> int:
        """
        Extrae artículos desde div[class*='td-module-meta-info'].
        h3[class*='entry-title'] > a → title + href.
        Un solo round-trip JS.
        """
        found_new = 0

        items = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                document.querySelectorAll(
                    'div[class*="td-module-meta-info"]'
                ).forEach(metaDiv => {
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
            await page.wait_for_timeout(1800)

            # -----------------------------------------------------------
            # Fecha: div.tdb-block-inner > time[class*='td-module-date'][datetime]
            # -----------------------------------------------------------
            publication_date = ""

            time_el = await page.query_selector(
                "div.tdb-block-inner time[class*='td-module-date'][datetime], "
                "time[class*='entry-date'][datetime], "
                "time[class*='td-module-date'][datetime]"
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
            # Texto: div.tdb-block-inner.td-fix-index → todos los <p>
            # Excluir AdThrive ads, relacionados, sharing, nav
            # -----------------------------------------------------------
            # Seleccionar el tdb-block-inner que contiene el artículo
            # (puede haber varios en la página — tomar el que tiene más párrafos)
            full_text = await page.evaluate("""
                () => {
                    // Buscar todos los div con ambas clases
                    const candidates = document.querySelectorAll(
                        'div.tdb-block-inner.td-fix-index, '
                        + 'div[class*="tdb-block-inner"][class*="td-fix-index"]'
                    );

                    // Tomar el que tiene más párrafos
                    let best = null;
                    let bestCount = 0;
                    candidates.forEach(c => {
                        const count = c.querySelectorAll('p').length;
                        if (count > bestCount) {
                            bestCount = count;
                            best = c;
                        }
                    });

                    if (!best || bestCount < 2) return '';

                    const clone = best.cloneNode(true);

                    const remove_sels = [
                        // AdThrive ads
                        '[class*="adthrive"]', '[id*="AdThrive"]',
                        '[class*="adthrive-ad"]',
                        '[id*="google_ads"]',
                        // Publicidad genérica
                        '.td-a-ad', '[class*="td-a-ad"]',
                        'ins.adsbygoogle', '.adsbygoogle',
                        // Artículos relacionados / recomendados
                        '[class*="related"]', '[id*="related"]',
                        '[class*="td-related"]',
                        // Imagen destacada
                        '[class*="featured_image"]',
                        '[class*="tdb_single_featured"]',
                        // Sharing
                        '[class*="share"]', '[class*="sharedaddy"]',
                        '[class*="jetpack"]',
                        // Navegación
                        'nav', '[class*="post-navigation"]',
                        // Comentarios
                        '#comments', '[class*="comment"]',
                        // Autor y fecha (ya las tenemos)
                        '[class*="tdb_single_date"]',
                        '[class*="tdb_single_author"]',
                        // Figuras, scripts, estilos
                        'figure', 'figcaption',
                        'script', 'style', 'iframe',
                        // Tags
                        '[class*="td-tags"]',
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

    parser = argparse.ArgumentParser(description="Scraper The Tico Times")
    parser.add_argument(
        "--test", action="store_true",
        help=(
            f"Modo prueba: {TEST_MAX_SECTIONS} secciones, "
            f"{TEST_MAX_LOAD_MORE} 'Load more', "
            f"{TEST_MAX_ARTICLES} artículos/sección"
        )
    )
    parser.add_argument("--output", default="output")
    parser.add_argument("--logs", default="logs")
    args = parser.parse_args()

    scraper = TicoTimesScraper(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
