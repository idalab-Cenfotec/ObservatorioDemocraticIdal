"""
telediario.py
Scraper para Telediario CR (https://www.telediario.cr/)

Secciones (5):
  - Nacional         : https://www.telediario.cr/nacional
  - Elecciones 2026  : https://www.telediario.cr/politica/elecciones-costa-rica
  - En Alerta        : https://www.telediario.cr/en-alerta
  - Internacional    : https://www.telediario.cr/internacional
  - Tendencia        : https://www.telediario.cr/tendencia

Paginación: /seccion/page/2, /seccion/page/3 ...
(sin barra final, número va tras /page/)

Estructura listado:
  - Contenedor : article[class*='lr-list-row-row-news__article']
  - Título y URL: a[class*='board-module__a'][href]  (texto + href relativo)
  - Fecha       : span[class*='news__date']  ("21-05-2026" → DD-MM-YYYY)

Estructura artículo:
  - Texto : span[class*='base__body'][class*='news'] → todos los <p>
            (excluye bloques "Te Recomendamos", tweets, figuras, ads)
  - Fecha : del listado; fallback meta tag / time[datetime]

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

BASE_URL = "https://www.telediario.cr"

SECTIONS = [
    ("https://www.telediario.cr/nacional",                         "Nacional"),
    ("https://www.telediario.cr/politica/elecciones-costa-rica",   "Elecciones 2026"),
    ("https://www.telediario.cr/en-alerta",                        "En Alerta"),
    ("https://www.telediario.cr/internacional",                    "Internacional"),
    ("https://www.telediario.cr/tendencia",                        "Tendencia"),
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


def parse_telediario_date(text: str) -> str:
    """
    Parsea '21-05-2026' (DD-MM-YYYY) → '2026-05-21'
    También intenta 'YYYY-MM-DD' si viene en otro orden.
    """
    text = text.strip()

    # Formato DD-MM-YYYY
    match = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", text)
    if match:
        day   = int(match.group(1))
        month = int(match.group(2))
        year  = int(match.group(3))
        # Validar que sea DD-MM-YYYY y no YYYY-MM-DD invertido
        if year > 2000 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # Formato YYYY-MM-DD
    match2 = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match2:
        return f"{match2.group(1)}-{match2.group(2)}-{match2.group(3)}"

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
    href = href.strip()
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE_URL + href
    return BASE_URL + "/" + href


def is_article_url(url: str) -> bool:
    if not url or not url.startswith("https://www.telediario.cr/"):
        return False
    exclude = ["/page/", "/?", "/#", "/wp-", "/feed",
               "/television/", "/radio/", "/tag/", "/author/"]
    for pat in exclude:
        if pat in url:
            return False
    path = url.replace("https://www.telediario.cr/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 2


class TelediarioCRScraper(BaseScraper):
    """
    Scraper para Telediario CR.
    - 5 secciones con paginación /page/N
    - Scroll al final antes de cambiar página
    - Deduplicación global entre secciones
    - Modo prueba limita secciones, páginas y artículos
    """

    SOURCE_NAME = "telediario"
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
        Paginación: página 1 = base_url, página 2+ = base_url/page/N
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
                else base_url.rstrip("/") + f"/page/{page_num}"
            )
            page = await context.new_page()
            found_on_page = 0

            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=25_000)

                if resp and resp.status in (404, 301, 302):
                    # 301/302 pueden indicar que la paginación se agotó
                    final_url = page.url
                    if final_url == base_url or resp.status == 404:
                        self.logger.debug(
                            f"  [{section_name}] Pág {page_num} → {resp.status}, fin"
                        )
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

                if found_on_page == 0:
                    all_articles = await page.query_selector_all(
                        "article[class*='lr-list-row-row-news__article']"
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
        Extrae artículos desde article[class*='lr-list-row-row-news__article'].
        Título y URL: a[class*='board-module__a'] con href y texto.
        Fecha: span[class*='news__date'] dentro del mismo article.
        Un solo round-trip JS.
        """
        found_new = 0

        items = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // Buscar todos los article con clase lr-list-row-row-news__article
                const articles = document.querySelectorAll(
                    'article[class*="lr-list-row-row-news__article"]'
                );

                articles.forEach(art => {
                    // Enlace principal del artículo
                    // Priorizar el que está dentro del h2 (título principal)
                    const titleAnchor = art.querySelector(
                        'h2 a[class*="board-module__a"], '
                        + 'h2 a[href], '
                        + 'a[class*="board-module__a"]'
                    );
                    if (!titleAnchor) return;

                    const href  = titleAnchor.getAttribute('href') || '';
                    const title = (titleAnchor.innerText || '').replace(/\\s+/g, ' ').trim();

                    if (!href || !title || seen.has(href)) return;
                    seen.add(href);

                    // Fecha: span con clase que termine en news__date
                    let date = '';
                    const dateSpan = art.querySelector(
                        'span[class*="news__date"], '
                        + '[class*="__date"]'
                    );
                    if (dateSpan) date = (dateSpan.innerText || '').trim();

                    // Fallback: atributo datetime del time
                    if (!date) {
                        const timeEl = art.querySelector('time[datetime]');
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
            date_text = item.get("date", "")

            if not href or not title:
                continue

            url = make_absolute(href)

            if not is_article_url(url):
                continue
            if url in collected:
                continue

            pub_date = parse_telediario_date(date_text) if date_text else ""

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
            # Fecha: refinar con meta tag o time[datetime] del artículo
            # -----------------------------------------------------------
            publication_date = link_data.get("publication_date", "")

            # Buscar time[datetime] en el artículo (más preciso con hora)
            if not publication_date or len(publication_date) == 10:
                time_el = await page.query_selector(
                    "time[datetime][class*='datetime'], "
                    "time[datetime][class*='date']"
                )
                if time_el:
                    raw_dt = await time_el.get_attribute("datetime")
                    if raw_dt:
                        # datetime="21-05-2026 09:49" → parsear
                        date_part = parse_telediario_date(raw_dt.split(" ")[0])
                        time_part = ""
                        if " " in raw_dt:
                            time_part = raw_dt.split(" ")[1]
                        if date_part and time_part:
                            publication_date = f"{date_part} {time_part}:00"
                        elif date_part:
                            publication_date = date_part

            # Fallback meta
            if not publication_date:
                meta_el = await page.query_selector(
                    'meta[property="article:published_time"]'
                )
                if meta_el:
                    raw_dt = await meta_el.get_attribute("content")
                    if raw_dt:
                        publication_date = parse_iso_date(raw_dt)

            # -----------------------------------------------------------
            # Sección: refinar desde el artículo si es posible
            # -----------------------------------------------------------
            section = link_data.get("section", "")
            try:
                sect_el = await page.query_selector(
                    "[data-camus-section], "
                    "[class*='__section'][data-camus-section]"
                )
                if sect_el:
                    sect_text = clean_text(await sect_el.inner_text())
                    if sect_text:
                        section = sect_text
            except Exception:
                pass

            # -----------------------------------------------------------
            # Texto: span[class*='base__body'][class*='news']
            # Buscar sin jerarquía, extraer todos los <p>
            # Excluir bloques "Te Recomendamos", tweets, figuras, relacionados
            # -----------------------------------------------------------
            full_text = await page.evaluate("""
                () => {
                    // Buscar span con 'base__body' en la clase y que contenga 'news'
                    const candidates = document.querySelectorAll(
                        'span[class*="base__body"]'
                    );

                    // Seleccionar el que tenga más párrafos (el del artículo)
                    let bodySpan = null;
                    let maxP = 0;
                    candidates.forEach(c => {
                        const pCount = c.querySelectorAll('p').length;
                        if (pCount > maxP) {
                            maxP = pCount;
                            bodySpan = c;
                        }
                    });

                    if (!bodySpan || maxP < 1) return '';

                    const clone = bodySpan.cloneNode(true);

                    // Eliminar bloques no deseados
                    const remove_sels = [
                        // Bloques "Te Recomendamos" / artículos relacionados
                        '[class*="nd-text-highlights-detail-bold"]',
                        '[class*="nd-related-news"]',
                        '[data-mrf-recirculation]',
                        '[class*="related"]',
                        // Tweets y embeds
                        '[camus-noembed-id]',
                        'iframe', '.twitter-tweet',
                        // Imágenes y figuras
                        'figure', 'figcaption',
                        '[class*="img-container"]',
                        // Publicidad
                        '[class*="ad-"]', '[id*="ad"]',
                        // Scripts y estilos
                        'script', 'style',
                        // Navegación
                        'nav',
                    ];

                    remove_sels.forEach(sel => {
                        try {
                            clone.querySelectorAll(sel).forEach(e => e.remove());
                        } catch(err) {}
                    });

                    const parts = [];
                    const seen = new Set();

                    // Extraer p y h3 (Telediario usa h3 como subtítulos dentro del cuerpo)
                    clone.querySelectorAll('p, h3').forEach(el => {
                        const text = (el.innerText || el.textContent || '')
                            .replace(/\\s+/g, ' ').trim();
                        if (text.length < 10) return;
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

    parser = argparse.ArgumentParser(description="Scraper Telediario CR")
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

    scraper = TelediarioCRScraper(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
