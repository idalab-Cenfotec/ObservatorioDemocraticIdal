"""
sinartdigital.py
Scraper para Sinar Digital — Trece Noticias (https://sinartdigital.com/)

Secciones (6):
  - Nacionales  : https://sinartdigital.com/trecenoticias/nacionales
  - Salud       : https://sinartdigital.com/trecenoticias/salud
  - Tecnología  : https://sinartdigital.com/trecenoticias/tecnologia
  - Economía    : https://sinartdigital.com/trecenoticias/economia
  - Mundo       : https://sinartdigital.com/trecenoticias/mundo
  - Entrevistas : https://sinartdigital.com/trecenoticias/entrevistas

Paginación: /trecenoticias/seccion/2, /trecenoticias/seccion/3, ...
(sin /page/, el número va directo al final)

Estructura listado:
  - Título y URL : h2.pos-subtitle > a  (title + href relativo)
  - Fecha        : div.element.element-itemcreated  ("Lunes, 11 Noviembre 2024")

Estructura artículo:
  - Texto : div[class*='element-textarea'] → todos los <p>
  - Fecha : del listado; fallback meta tag

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

BASE_URL = "https://sinartdigital.com"

SECTIONS = [
    ("https://sinartdigital.com/trecenoticias/nacionales",  "Nacionales"),
    ("https://sinartdigital.com/trecenoticias/salud",       "Salud"),
    ("https://sinartdigital.com/trecenoticias/tecnologia",  "Tecnología"),
    ("https://sinartdigital.com/trecenoticias/economia",    "Economía"),
    ("https://sinartdigital.com/trecenoticias/mundo",       "Mundo"),
    ("https://sinartdigital.com/trecenoticias/entrevistas", "Entrevistas"),
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

DIAS_ES = {
    "lunes": "Monday", "martes": "Tuesday", "miércoles": "Wednesday",
    "miercoles": "Wednesday", "jueves": "Thursday", "viernes": "Friday",
    "sábado": "Saturday", "sabado": "Saturday", "domingo": "Sunday",
}

MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def parse_sinar_date(text: str) -> str:
    """
    Parsea 'Lunes, 11 Noviembre 2024' → '2024-11-11'
    También maneja 'Martes, 5 de Marzo 2024' y variantes.
    """
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)

    # Remover nombre del día y coma
    text = re.sub(r"^\w+,?\s*", "", text).strip()

    # Formato "DD Mes YYYY" o "DD de Mes YYYY"
    match = re.search(r"(\d{1,2})\s+(?:de\s+)?(\w+)\s+(\d{4})", text)
    if match:
        day   = int(match.group(1))
        month = MESES_ES.get(match.group(2), 0)
        year  = int(match.group(3))
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}"

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


def make_absolute(href: str) -> str:
    """Convierte href relativo a URL absoluta."""
    href = href.strip()
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE_URL + href
    return BASE_URL + "/" + href


def is_article_url(url: str) -> bool:
    if not url or not url.startswith("https://sinartdigital.com/"):
        return False
    exclude = ["/category/", "/author/", "/tag/",
               "/?", "/#", "/wp-", "/feed"]
    for pat in exclude:
        if pat in url:
            return False
    path = url.replace("https://sinartdigital.com/trecenoticias/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    # Artículos tienen formato /trecenoticias/seccion/item/slug
    # → al menos 2 segmentos después de trecenoticias
    return len(parts) >= 2 and parts[0] != str(parts[0]).isdigit()


class SinartDigitalScraper(BaseScraper):
    """
    Scraper para Sinar Digital — Trece Noticias.
    - 6 secciones con paginación /seccion/N
    - Scroll al final antes de cambiar página
    - Deduplicación global entre secciones
    - Modo prueba limita secciones, páginas y artículos
    """

    SOURCE_NAME = "sinartdigital"
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
        """
        Paginación especial de Sinar: /trecenoticias/seccion/2, /3, ...
        Página 1 = URL base sin número.
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
                else base_url.rstrip("/") + f"/{page_num}"
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

                self.logger.debug(
                    f"  [{section_name}] Pág {page_num}: "
                    f"{found_on_page} nuevos | total: {len(collected)}"
                )

                # Varias páginas seguidas con solo URLs ya cargadas → fin de sección
                if corte.pagina([l["url"] for l in list(collected.values())[n_antes:]]):
                    self.logger.info(f"  [{section_name}] Pág {page_num}: solo URLs ya conocidas, fin de sección")
                    break

                # Sin artículos en la página → fin de sección
                if found_on_page == 0:
                    all_titles = await page.query_selector_all(
                        "h2.pos-subtitle, h2[class*='pos-subtitle']"
                    )
                    if len(all_titles) == 0:
                        self.logger.debug(f"  [{section_name}] Sin h2.pos-subtitle, terminando")
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
        Extrae tarjetas buscando h2.pos-subtitle > a.
        Fecha: div.element.element-itemcreated en el mismo bloque.
        Un solo round-trip JS para eficiencia.
        """
        found_new = 0

        items = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // Buscar todos los h2.pos-subtitle
                document.querySelectorAll('h2.pos-subtitle, h2[class*="pos-subtitle"]').forEach(h2 => {
                    const anchor = h2.querySelector('a[href]');
                    if (!anchor) return;

                    const href  = anchor.getAttribute('href') || '';
                    const title = (
                        anchor.getAttribute('title') ||
                        anchor.innerText ||
                        ''
                    ).replace(/\\s+/g, ' ').trim();

                    if (!href || !title || seen.has(href)) return;
                    seen.add(href);

                    // Buscar fecha en el bloque contenedor del h2
                    let date = '';
                    const container = h2.closest(
                        '[class*="pos-item"], [class*="item"], article, .row, li'
                    ) || h2.parentElement?.parentElement;

                    if (container) {
                        // div con clase que contenga 'element-itemcreated'
                        const dateEl = container.querySelector(
                            '[class*="element-itemcreated"], '
                            + '[class*="itemcreated"]'
                        );
                        if (dateEl) date = (dateEl.innerText || '').trim();
                    }

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

            url = make_absolute(href)

            if not is_article_url(url):
                continue
            if url in collected:
                continue

            pub_date = parse_sinar_date(date_text) if date_text else ""

            collected[url] = {
                "url": url,
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
            # Fecha: refinar con meta tag (tiene hora exacta)
            # Si no, usar la del listado
            # -----------------------------------------------------------
            publication_date = link_data.get("publication_date", "")

            meta_el = await page.query_selector(
                'meta[property="article:published_time"]'
            )
            if meta_el:
                raw_dt = await meta_el.get_attribute("content")
                if raw_dt:
                    publication_date = parse_iso_date(raw_dt)

            # Si no hay meta, intentar leer del artículo directamente
            if not publication_date:
                date_el = await page.query_selector(
                    "[class*='element-itemcreated'], [class*='itemcreated']"
                )
                if date_el:
                    date_text = await date_el.inner_text()
                    publication_date = parse_sinar_date(date_text)

            # -----------------------------------------------------------
            # Texto: div[class*='element-textarea'] → todos los <p>
            # Hay varios bloques element-textarea por artículo,
            # recolectar todos en orden
            # -----------------------------------------------------------
            full_text = await page.evaluate("""
                () => {
                    // Buscar todos los divs con clase que contenga 'element-textarea'
                    const blocks = document.querySelectorAll(
                        'div[class*="element-textarea"]'
                    );

                    if (blocks.length === 0) return '';

                    const parts = [];
                    const seen = new Set();

                    blocks.forEach(block => {
                        const clone = block.cloneNode(true);

                        // Limpiar elementos no deseados dentro del bloque
                        ['script', 'style', 'iframe', 'figure', 'figcaption',
                         '[class*="share"]', '[class*="social"]',
                         '[class*="ad-"]'].forEach(sel => {
                            try {
                                clone.querySelectorAll(sel).forEach(e => e.remove());
                            } catch(err) {}
                        });

                        // Extraer párrafos y listas
                        const pEls = clone.querySelectorAll('p, li');
                        if (pEls.length > 0) {
                            pEls.forEach(el => {
                                const text = (el.innerText || el.textContent || '')
                                    .replace(/\\s+/g, ' ').trim();
                                if (text.length < 15) return;
                                if (seen.has(text)) return;
                                seen.add(text);
                                parts.push(text);
                            });
                        } else {
                            // Fallback: texto directo del bloque (ej. divs planos sin <p>)
                            const text = (clone.innerText || clone.textContent || '')
                                .replace(/\\s+/g, ' ').trim();
                            if (text.length >= 15 && !seen.has(text)) {
                                seen.add(text);
                                parts.push(text);
                            }
                        }
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

    parser = argparse.ArgumentParser(description="Scraper Sinar Digital")
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

    scraper = SinartDigitalScraper(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
