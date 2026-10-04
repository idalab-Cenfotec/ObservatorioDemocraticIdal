"""
monumental.py
Scraper para Monumental CR (https://www.monumental.co.cr/)

Secciones:
  - Nacionales     : https://www.monumental.co.cr/category/nacionales/
  - Internacionales: https://www.monumental.co.cr/category/internacionales/

Paginación estándar WordPress: /category/slug/page/N/

Estructura listado:
  - Contenedor por artículo : div.data
  - Título y URL            : div.data > h2 > a  (texto e href)
  - Fecha                   : div.data > span.date  ("07 mayo 2026")

Estructura artículo:
  - Texto : div[class*='contentPost'] → div.content-body → todos los <p>
  - Fecha : se toma del listado (span.date); fallback meta tag
"""

import asyncio
import re
from datetime import datetime, timezone, timedelta

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.base_scraper import BaseScraper, CorteIncremental

CR_TZ = timezone(timedelta(hours=-6))

BASE_URL = "https://www.monumental.co.cr/"

SECTIONS = [
    ("https://www.monumental.co.cr/category/nacionales/",      "Nacionales"),
    ("https://www.monumental.co.cr/category/internacionales/", "Internacionales"),
]

ARTICLE_TIMEOUT = 20_000
DELAY_BETWEEN_ARTICLES = 1.0
DELAY_BETWEEN_PAGES = 1.5
DELAY_SCROLL = 2000  # ms entre scrolls

# Modo prueba: limita número de artículos procesados y páginas de sección
TEST_MAX_ARTICLES = 5
TEST_MAX_PAGES = 2

MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def parse_date_es(text: str) -> str:
    """
    Parsea '07 mayo 2026' → '2026-05-07'.
    También maneja formatos con coma: '7 de mayo de 2026'.
    """
    text = text.strip().lower()
    # Formato "07 mayo 2026"
    match = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", text)
    if match:
        day = int(match.group(1))
        month = MESES_ES.get(match.group(2), 0)
        year = int(match.group(3))
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}"
    # Formato "7 de mayo de 2026"
    match2 = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", text)
    if match2:
        day = int(match2.group(1))
        month = MESES_ES.get(match2.group(2), 0)
        year = int(match2.group(3))
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


def is_article_url(url: str) -> bool:
    if not url or not url.startswith("https://www.monumental.co.cr/"):
        return False
    exclude = ["/category/", "/author/", "/tag/", "/page/",
               "/?", "/#", "/wp-", "/feed"]
    for pat in exclude:
        if pat in url:
            return False
    # Artículos tienen formato /YYYY/MM/DD/slug/
    path = url.replace("https://www.monumental.co.cr/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 2


class MonumentalScraper(BaseScraper):
    """
    Scraper para Monumental CR.
    Recorre 2 secciones con paginación WordPress estándar.
    En cada página hace scroll completo antes de pasar a la siguiente.
    """

    SOURCE_NAME = "monumental"
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
            # PASO 1: Recolectar URLs de ambas secciones
            # -------------------------------------------------------
            article_links = {}

            for section_url, section_name in SECTIONS:
                self.logger.info(f"Recolectando: {section_name} ({section_url})")
                links = await self._collect_section(context, section_url, section_name)
                new_count = 0
                for link in links:
                    if link["url"] not in article_links:
                        article_links[link["url"]] = link
                        new_count += 1
                self.logger.info(
                    f"  → {len(links)} encontrados | {new_count} nuevos | "
                    f"Total: {len(article_links)}"
                )

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
                links_list = links_list[:TEST_MAX_ARTICLES]

            for i, link_data in enumerate(links_list):
                if self.tiempo_agotado():
                    break
                self.logger.debug(f"[{i+1}/{len(links_list)}] {link_data['url']}")
                record = await self._scrape_article(context, link_data)
                if record:
                    records.append(record)
                await asyncio.sleep(DELAY_BETWEEN_ARTICLES)

            await browser.close()
            return records

    # ------------------------------------------------------------------
    # Recolección paginada
    # ------------------------------------------------------------------

    async def _collect_section(
        self, context, base_url: str, section_name: str
    ) -> list[dict]:
        """
        Recorre todas las páginas de la sección.
        Por cada página: scroll completo → extrae div.data → siguiente página.
        """
        collected = {}
        page_num = 1
        max_pages = TEST_MAX_PAGES if self.test_mode else 9999
        corte = CorteIncremental(self)

        while page_num <= max_pages:
            if self.tiempo_agotado(0.5):
                self.logger.warning(f"  [{section_name}] Tiempo del listado agotado, se pasa a los artículos")
                break
            if page_num == 1:
                url = base_url
            else:
                url = base_url.rstrip("/") + f"/page/{page_num}/"

            page = await context.new_page()
            found_on_page = 0

            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=25_000)

                if resp and resp.status == 404:
                    self.logger.debug(f"  Página {page_num} → 404, fin")
                    break

                await page.wait_for_timeout(1500)

                # Scroll progresivo hasta el final
                await self._scroll_to_bottom(page)

                # Extraer tarjetas div.data
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

                # En modo prueba, detenerse cuando ya hay suficientes links
                if self.test_mode and len(collected) >= TEST_MAX_ARTICLES:
                    self.logger.debug(
                        f"  Modo prueba: recolectados {len(collected)} links, terminando"
                    )
                    break

                # Sin artículos → fin de la sección
                if found_on_page == 0:
                    all_cards = await page.query_selector_all("div.data")
                    if len(all_cards) == 0:
                        self.logger.debug(f"  Sin div.data en pág {page_num}, terminando")
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
        """Scroll progresivo hasta el final para activar lazy-loading."""
        prev_height = -1
        for _ in range(8):
            current_height = await page.evaluate("document.body.scrollHeight")
            if current_height == prev_height:
                break
            prev_height = current_height
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(DELAY_SCROLL)

    async def _extract_cards(
        self, page, collected: dict, section_name: str
    ) -> int:
        """
        Extrae artículos desde div.data en la página actual.
        Retorna el número de artículos nuevos encontrados.
        """
        found_new = 0
        cards = await page.query_selector_all("div.data")

        for card in cards:
            try:
                # URL y título: h2 > a
                anchor = await card.query_selector("h2 a")
                if not anchor:
                    continue

                href = await anchor.get_attribute("href")
                if not href or not is_article_url(href):
                    continue
                if href in collected:
                    continue

                title = clean_text(await anchor.inner_text())
                if not title:
                    continue

                # Fecha: span.date
                pub_date = ""
                date_el = await card.query_selector("span.date")
                if date_el:
                    date_text = await date_el.inner_text()
                    pub_date = parse_date_es(date_text)

                collected[href] = {
                    "url": href.strip(),
                    "title": title,
                    "section": section_name,
                    "publication_date": pub_date,
                }
                found_new += 1

            except Exception as e:
                self.logger.debug(f"Error en div.data: {e}")
                continue

        return found_new

    # ------------------------------------------------------------------
    # Scraping del artículo individual
    # ------------------------------------------------------------------

    async def _scrape_article(self, context, link_data: dict) -> dict | None:
        """
        Visita el artículo.
        Texto: div[class*='contentPost'] (sin jerarquía) → div.content-body → <p>
        Fecha: del listado; fallback meta tag.
        """
        page = await context.new_page()

        try:
            await page.goto(
                link_data["url"], wait_until="domcontentloaded", timeout=ARTICLE_TIMEOUT
            )
            await page.wait_for_timeout(1500)

            # -----------------------------------------------------------
            # Fecha: mejorar con meta si la del listado no tiene hora
            # -----------------------------------------------------------
            publication_date = link_data.get("publication_date", "")

            if not publication_date:
                meta_el = await page.query_selector(
                    'meta[property="article:published_time"]'
                )
                if meta_el:
                    raw_dt = await meta_el.get_attribute("content")
                    if raw_dt:
                        publication_date = parse_iso_date(raw_dt)

            # -----------------------------------------------------------
            # Texto: buscar div con clase que contenga 'contentPost'
            # sin importar jerarquía ni nombre exacto de clase
            # -----------------------------------------------------------
            content_div = await page.query_selector(
                "div[class*='contentPost'], div[class*='content-post']"
            )

            # Fallback: div.content-body directamente
            if not content_div:
                content_div = await page.query_selector(
                    "div.content-body, div[class*='content-body']"
                )

            # Fallback final: article
            if not content_div:
                content_div = await page.query_selector("article")

            if not content_div:
                self.logger.warning(f"Sin contenedor: {link_data['url']}")
                return None

            # Extraer <p> con JS, removiendo publicidad y widgets
            full_text = await content_div.evaluate("""
                (el) => {
                    const clone = el.cloneNode(true);

                    const remove_sels = [
                        'script', 'style', 'iframe',
                        'figure', 'figcaption',
                        '[class*="ad"]', '[id*="ad"]',
                        '[class*="share"]', '[class*="social"]',
                        '[class*="related"]', '[class*="widget"]',
                        '[class*="newsletter"]', '[class*="suscri"]',
                        'nav', '.navigation',
                    ];
                    remove_sels.forEach(sel => {
                        try { clone.querySelectorAll(sel).forEach(e => e.remove()); }
                        catch(err) {}
                    });

                    const parts = [];
                    clone.querySelectorAll('p').forEach(p => {
                        const text = (p.innerText || p.textContent || '')
                            .replace(/\\s+/g, ' ').trim();
                        if (text.length >= 15) parts.push(text);
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
                "section": link_data["section"],
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


if __name__ == "__main__":
    scraper = MonumentalScraper()
    summary = scraper.run()
    print(summary)
