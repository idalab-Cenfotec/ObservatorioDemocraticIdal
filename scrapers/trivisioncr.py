"""
trivisioncr.py
Scraper para Trivisión CR (https://trivisioncr.com/)

Secciones (8):
  - Nacionales   : https://trivisioncr.com/noticias-nacionales/
  - Política     : https://trivisioncr.com/noticias-de-politica/
  - Economía     : https://trivisioncr.com/noticias-de-economia/
  - Guanacastecas: https://trivisioncr.com/noticias-guanacastecas/
  - Tecnología   : https://trivisioncr.com/noticias-de-tecnologia/
  - Clima        : https://trivisioncr.com/noticias-del-clima/
  - Salud        : https://trivisioncr.com/noticias-de-salud/
  - Educación    : https://trivisioncr.com/noticias-de-educacion/

Paginación: /page/2/, /page/3/ (WordPress estándar)

Estructura listado:
  - Contenedor : div[id^='dv25-pc-'] > div.dv25-pc-wrap
  - Título      : h2.dv25-pc-title
  - URL         : a.dv25-pc-btn[href]
  - Fecha       : span.dv25-pc-date  ("18/05/2026" → DD/MM/YYYY)

Estructura artículo:
  - Texto : div.elementor-widget-container → todos los <p>
            Seleccionar el que tiene más párrafos (Elementor puede
            tener varios contenedores por página)
  - Fecha : del listado (DD/MM/YYYY); fallback meta tag

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
from scrapers.base_scraper import BaseScraper, CorteIncremental, cerrar_pagina

CR_TZ = timezone(timedelta(hours=-6))

BASE_URL = "https://trivisioncr.com"

SECTIONS = [
    ("https://trivisioncr.com/noticias-nacionales/",       "Nacionales"),
    ("https://trivisioncr.com/noticias-de-politica/",      "Política"),
    ("https://trivisioncr.com/noticias-de-economia/",      "Economía"),
    ("https://trivisioncr.com/noticias-guanacastecas/",    "Guanacastecas"),
    ("https://trivisioncr.com/noticias-de-tecnologia/",    "Tecnología"),
    ("https://trivisioncr.com/noticias-del-clima/",        "Clima"),
    ("https://trivisioncr.com/noticias-de-salud/",         "Salud"),
    ("https://trivisioncr.com/noticias-de-educacion/",     "Educación"),
]

ARTICLE_TIMEOUT = 22_000
DELAY_BETWEEN_ARTICLES = 1.0
DELAY_BETWEEN_PAGES = 1.5
DELAY_BETWEEN_SECTIONS = 2.5
DELAY_SCROLL = 4000

# Modo prueba
TEST_MAX_SECTIONS = 2
TEST_MAX_PAGES = 2
TEST_MAX_ARTICLES = 8


def parse_trivsion_date(text: str) -> str:
    """
    Parsea '18/05/2026' (DD/MM/YYYY) → '2026-05-18'
    También intenta DD-MM-YYYY y YYYY-MM-DD como fallbacks.
    """
    text = text.strip()

    # DD/MM/YYYY
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        day   = int(match.group(1))
        month = int(match.group(2))
        year  = int(match.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # DD-MM-YYYY
    match2 = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", text)
    if match2:
        day   = int(match2.group(1))
        month = int(match2.group(2))
        year  = int(match2.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31 and year > 2000:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # YYYY-MM-DD
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
    if not url or not url.startswith("https://trivisioncr.com/"):
        return False
    exclude = ["/page/", "/author/", "/tag/", "/category/",
               "/?", "/#", "/wp-", "/feed"]
    for pat in exclude:
        if pat in url:
            return False
    # Excluir URLs base de sección
    for sec_url, _ in SECTIONS:
        if url.rstrip("/") + "/" == sec_url or url == sec_url.rstrip("/"):
            return False
    path = url.replace("https://trivisioncr.com/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 2


class TrivisionCRScraper(BaseScraper):
    """
    Scraper para Trivisión CR.
    - 8 secciones con paginación /page/N/
    - Scroll al final antes de cambiar página
    - Deduplicación global entre secciones
    - Modo prueba limita secciones, páginas y artículos
    """

    SOURCE_NAME = "trivisioncr"
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
    # Recolección paginada
    # ------------------------------------------------------------------

    async def _collect_section(
        self, context, base_url: str, section_name: str
    ) -> list[dict]:
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
                else base_url.rstrip("/") + f"/page/{page_num}/"
            )
            page = await context.new_page()
            found_on_page = 0

            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=25_000)

                if resp and resp.status == 404:
                    self.logger.debug(f"  [{section_name}] Pág {page_num} → 404, fin")
                    break

                await page.wait_for_timeout(4000)
                await self._scroll_to_bottom(page)

                try:
                    await page.wait_for_selector(
                        "div[id^='dv25-pc-'], div.dv25-pc-wrap, a.dv25-ng-card",
                        timeout=10_000
                    )
                except Exception:
                    pass

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
                    # Verificar si hay tarjetas en la página
                    all_cards = await page.query_selector_all(
                        "div[id^='dv25-pc-'], .dv25-pc-wrap, .dv25-pc-title, a.dv25-ng-card"
                    )
                    if len(all_cards) == 0:
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
        Extrae artículos de dos tipos de tarjeta:
        - a.dv25-ng-card (grilla actual): h3.dv25-ng-title, div.dv25-ng-date, href en la tarjeta
        - div.dv25-pc-wrap (tarjeta anterior): h2.dv25-pc-title, a.dv25-pc-btn[href], span.dv25-pc-date
        Un solo round-trip JS.
        """
        found_new = 0

        items = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // Buscar contenedores principales: div cuyo id empiece con 'dv25-pc-'
                // También buscar directamente .dv25-pc-wrap como fallback
                const wraps = document.querySelectorAll(
                    'div[id^="dv25-pc-"] .dv25-pc-wrap, '
                    + 'div.dv25-pc-wrap'
                );

                wraps.forEach(wrap => {
                    // Título
                    const h2 = wrap.querySelector('h2.dv25-pc-title, h2[class*="dv25"]');
                    const title = h2
                        ? (h2.innerText || '').replace(/\\s+/g, ' ').trim()
                        : '';

                    // URL: a.dv25-pc-btn
                    const btn = wrap.querySelector('a.dv25-pc-btn[href], a[class*="dv25"][href]');
                    const href = btn ? (btn.getAttribute('href') || '') : '';

                    if (!href || !title || seen.has(href)) return;
                    seen.add(href);

                    // Fecha: span.dv25-pc-date
                    const dateSpan = wrap.querySelector(
                        'span.dv25-pc-date, span[class*="dv25"][class*="date"]'
                    );
                    const date = dateSpan
                        ? (dateSpan.innerText || '').trim()
                        : '';

                    results.push({ href, title, date });
                });

                // Grilla actual del sitio: <a class="dv25-ng-card" href> con
                // h3.dv25-ng-title y div.dv25-ng-date (DD/MM/AAAA)
                document.querySelectorAll('a.dv25-ng-card[href]').forEach(card => {
                    const h3 = card.querySelector('[class*="dv25-ng-title"]');
                    const title = h3
                        ? (h3.innerText || '').replace(/\\s+/g, ' ').trim()
                        : '';
                    const href = card.getAttribute('href') || '';

                    if (!href || !title || seen.has(href)) return;
                    seen.add(href);

                    const dateEl = card.querySelector('[class*="dv25-ng-date"]');
                    const date = dateEl ? (dateEl.innerText || '').trim() : '';

                    results.push({ href, title, date });
                });

                return results;
            }
        """)

        for item in items:
            href = item.get("href", "").strip()
            title = clean_text(item.get("title", ""))
            date_text = item.get("date", "")

            if not href or not title:
                continue
            if not is_article_url(href):
                continue
            if href in collected:
                continue

            pub_date = parse_trivsion_date(date_text) if date_text else ""

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
            try:
                await page.wait_for_selector(
                    "div.elementor-widget-container",
                    timeout=10_000
                )
            except Exception:
                pass

            # -----------------------------------------------------------
            # Fecha: refinar con meta tag si es posible (trae hora exacta)
            # Usar la del listado como base (DD/MM/YYYY ya parseada)
            # -----------------------------------------------------------
            publication_date = link_data.get("publication_date", "")

            meta_el = await page.query_selector(
                'meta[property="article:published_time"]'
            )
            if meta_el:
                raw_dt = await meta_el.get_attribute("content")
                if raw_dt:
                    publication_date = parse_iso_date(raw_dt)

            # Fallback: buscar fecha en el propio artículo
            if not publication_date:
                date_el = await page.query_selector(
                    "[class*='dv25'][class*='date'], "
                    "[class*='entry-date'], "
                    "time[datetime]"
                )
                if date_el:
                    tag = await date_el.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "time":
                        raw_dt = await date_el.get_attribute("datetime")
                        if raw_dt:
                            publication_date = parse_iso_date(raw_dt)
                    else:
                        date_text = await date_el.inner_text()
                        publication_date = parse_trivsion_date(date_text)

            # -----------------------------------------------------------
            # Texto: div.elementor-widget-container → todos los <p>
            # Seleccionar el que tiene más párrafos (artículo principal)
            # -----------------------------------------------------------
            full_text = await page.evaluate("""
                () => {
                    // Todos los elementor-widget-container
                    const containers = document.querySelectorAll(
                        'div.elementor-widget-container'
                    );

                    // Primero el widget de contenido de la nota. Elegir "el que
                    // tenga más párrafos" falla en notas cortas: el pie de página
                    // (3 párrafos institucionales) le gana al cuerpo (1-2 párrafos).
                    let best = document.querySelector(
                        '.elementor-widget-theme-post-content .elementor-widget-container'
                    );
                    let bestCount = best ? best.querySelectorAll('p').length : 0;

                    // Respaldo: el contenedor con más párrafos
                    if (bestCount < 1) {
                        best = null;
                        containers.forEach(c => {
                            const count = c.querySelectorAll('p').length;
                            if (count > bestCount) {
                                bestCount = count;
                                best = c;
                            }
                        });
                    }

                    if (!best || bestCount < 1) return '';

                    const clone = best.cloneNode(true);

                    const remove_sels = [
                        // Publicidad
                        '[class*="ad-"]', '[id*="ad"]',
                        'ins.adsbygoogle', '.adsbygoogle',
                        '[id*="google_ads"]',
                        // Imágenes y figuras
                        'figure', 'figcaption',
                        '[class*="wp-block-image"]',
                        // Redes sociales y sharing
                        '[class*="share"]', '[class*="social"]',
                        '[class*="sharedaddy"]',
                        // Artículos relacionados
                        '[class*="related"]', '[id*="related"]',
                        // Navegación y comentarios
                        'nav', '#comments', '[class*="comment"]',
                        // Scripts, estilos, iframes
                        'script', 'style', 'iframe',
                        // Botones y elementos de interfaz
                        'button', '[class*="dv25-pc-btn"]',
                        // Tags y categorías
                        '[class*="tags"]', '[class*="category"]',
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
                titulo_pagina = await page.title()
                self.registrar_pagina_sin_contenido(titulo_pagina)
                self.logger.warning(f"Sin texto: {link_data['url']} (título de la página: {titulo_pagina!r})")
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

    parser = argparse.ArgumentParser(description="Scraper Trivisión CR")
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

    scraper = TrivisionCRScraper(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
