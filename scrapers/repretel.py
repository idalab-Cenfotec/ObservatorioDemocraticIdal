"""
repretel.py
Scraper para Repretel (https://www.repretel.com/)

Sección: Actualidad
URL base: https://www.repretel.com/category/actualidad/
Paginación: /page/N/  (209k+ artículos, ~10k páginas)

Límite temporal: se detiene cuando encuentra artículos anteriores a 2024-01-01.
Estrategia: artículos están ordenados de más reciente a más antiguo por página,
por lo que al encontrar la primera fecha < 2023 en una página se para.

Estructura listado:
  - Enlace y título : a[class*='feed__item'][href][title]
    - href  → URL relativa (/noticia/slug/) → se convierte a absoluta
    - title → título del artículo

Estructura artículo:
  - Fecha : div[class*='single-layout__meta-date']
            ("22 de Mayo de 2026 - 15:18") → "2026-05-22 15:18:00"
  - Texto : div[class*='single-layout__article'] → todos los <p>

Deduplicación: global por URL.
Modo prueba: --test limita páginas y artículos.
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

BASE_URL = "https://www.repretel.com"
SECTION_URL = "https://www.repretel.com/category/actualidad/"

# Detener al encontrar artículos anteriores a esta fecha
DATE_CUTOFF = datetime(2024, 1, 1, tzinfo=CR_TZ)

ARTICLE_TIMEOUT = 22_000
DELAY_BETWEEN_ARTICLES = 1.0
DELAY_BETWEEN_PAGES = 1.5
DELAY_SCROLL = 1800

# Modo prueba
TEST_MAX_PAGES = 3
TEST_MAX_ARTICLES = 10

MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def parse_repretel_date(text: str) -> tuple[str, datetime | None]:
    """
    Parsea '22 de Mayo de 2026 - 15:18' → ('2026-05-22 15:18:00', datetime_obj)
    Retorna ('', None) si no puede parsear.
    """
    text = text.strip().lower()
    match = re.search(
        r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})\s*-\s*(\d{1,2}):(\d{2})",
        text
    )
    if match:
        day   = int(match.group(1))
        month = MESES_ES.get(match.group(2), 0)
        year  = int(match.group(3))
        hour  = int(match.group(4))
        minute= int(match.group(5))
        if month:
            dt = datetime(year, month, day, hour, minute, tzinfo=CR_TZ)
            formatted = dt.strftime("%Y-%m-%d %H:%M:%S")
            return formatted, dt

    # Fallback: solo fecha sin hora
    match2 = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", text)
    if match2:
        day   = int(match2.group(1))
        month = MESES_ES.get(match2.group(2), 0)
        year  = int(match2.group(3))
        if month:
            dt = datetime(year, month, day, tzinfo=CR_TZ)
            return dt.strftime("%Y-%m-%d"), dt

    return "", None


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
    if href.startswith("http"):
        return href
    return BASE_URL + href


def is_article_url(url: str) -> bool:
    if not url or not url.startswith("https://www.repretel.com/"):
        return False
    exclude = ["/category/", "/author/", "/tag/", "/page/",
               "/?", "/#", "/wp-", "/feed"]
    for pat in exclude:
        if pat in url:
            return False
    path = url.replace("https://www.repretel.com/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 1


class RepretelScraper(BaseScraper):
    """
    Scraper para Repretel — sección Actualidad.
    - Paginación /page/N/ hasta agotar o superar límite de fecha 2024
    - En cada página: scroll → extrae links → pasa a siguiente
    - Cada artículo se visita para extraer fecha exacta y texto
    - Se detiene cuando publication_date < 2024-01-01
    - Modo prueba: --test limita páginas y artículos
    """

    SOURCE_NAME = "repretel"
    BASE_URL = BASE_URL + "/"

    def __init__(self, output_dir="output", log_dir="logs", test_mode=False):
        super().__init__(output_dir=output_dir, log_dir=log_dir)
        self.test_mode = test_mode
        if test_mode:
            self.logger.info(
                f"*** MODO PRUEBA: {TEST_MAX_PAGES} páginas | "
                f"{TEST_MAX_ARTICLES} artículos ***"
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
            # PASO 1: Recolectar URLs paginando hasta límite de fecha
            # -------------------------------------------------------
            self.logger.info(f"Recolectando sección Actualidad ({SECTION_URL})")
            article_links = await self._collect_all_pages(context)
            self.logger.info(f"Total URLs únicas recolectadas: {len(article_links)}")

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
                self.logger.info(f"Modo prueba: procesando {len(links_list)} artículos")

            for i, link_data in enumerate(links_list):
                if self.tiempo_agotado():
                    break
                self.logger.debug(f"[{i+1}/{len(links_list)}] {link_data['url']}")
                record = await self._scrape_article(context, link_data)
                if record:
                    records.append(record)
                await asyncio.sleep(DELAY_BETWEEN_ARTICLES + random.uniform(0.1, 0.4))

                # Pausa cada 30 artículos
                if (i + 1) % 30 == 0:
                    self.logger.info(f"  Pausa tras {i+1} artículos...")
                    await asyncio.sleep(4)

            await browser.close()
            return records

    # ------------------------------------------------------------------
    # Recolección paginada con límite temporal
    # ------------------------------------------------------------------

    async def _collect_all_pages(self, context) -> dict:
        """
        Recorre páginas de Actualidad de más reciente a más antigua.
        Se detiene cuando:
          - Recibe 404
          - Todos los artículos de una página son anteriores a 2023-01-01
          - Se alcanza TEST_MAX_PAGES en modo prueba
        """
        collected = {}  # url → {url, title, section}
        page_num = 1
        cutoff_reached = False
        max_pages = TEST_MAX_PAGES if self.test_mode else 999_999
        corte = CorteIncremental(self)

        while page_num <= max_pages and not cutoff_reached:
            if self.tiempo_agotado(0.5):
                self.logger.warning("  Tiempo del listado agotado, se pasa a los artículos")
                break
            url = (
                SECTION_URL
                if page_num == 1
                else SECTION_URL.rstrip("/") + f"/page/{page_num}/"
            )
            page = await context.new_page()
            found_on_page = 0
            urls_pagina = []

            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=25_000)

                if resp and resp.status == 404:
                    self.logger.info(f"  Página {page_num} → 404, fin de sección")
                    break

                await page.wait_for_timeout(1500)
                await self._scroll_to_bottom(page)

                # Extraer todos los a.feed__item de la página
                items = await self._extract_feed_items(page)

                page_all_old = True  # flag: ¿todos los artículos son viejos?

                for item in items:
                    href = item.get("href", "")
                    title = clean_text(item.get("title", ""))

                    if not href or not title:
                        continue

                    url_abs = make_absolute(href)
                    if not is_article_url(url_abs):
                        continue
                    if url_abs in collected:
                        continue

                    # Verificar fecha desde atributo data si existe
                    # (algunos feeds incluyen data-date en el elemento)
                    item_date_str = item.get("date", "")
                    item_dt = None
                    if item_date_str:
                        _, item_dt = parse_repretel_date(item_date_str)

                    # Si tenemos fecha y es anterior al corte, marcar
                    if item_dt:
                        if item_dt >= DATE_CUTOFF:
                            page_all_old = False
                        # No agregar artículos anteriores al corte
                        # pero tampoco detener: puede haber más recientes en la misma página
                        if item_dt < DATE_CUTOFF:
                            self.logger.debug(
                                f"  Artículo fuera de rango: {url_abs} ({item_date_str})"
                            )
                            continue
                    else:
                        # Sin fecha en listado → agregar igualmente, se validará en el artículo
                        page_all_old = False

                    collected[url_abs] = {
                        "url": url_abs,
                        "title": title,
                        "section": "Actualidad",
                        "publication_date": "",
                    }
                    found_on_page += 1
                    urls_pagina.append(url_abs)

                self.logger.info(
                    f"  Pág {page_num}: {found_on_page} nuevos | "
                    f"total: {len(collected)} | "
                    f"todos viejos: {page_all_old}"
                )

                # Varias páginas seguidas con solo URLs ya cargadas -> fin del listado
                if corte.pagina(urls_pagina):
                    self.logger.info(f"  Pág {page_num}: solo URLs ya conocidas, fin del listado")
                    break

                # Si todos en la página son anteriores al corte → parar
                if page_all_old and page_num > 1:
                    self.logger.info(
                        f"  Página {page_num} completamente anterior a 2023, "
                        "deteniendo recolección"
                    )
                    cutoff_reached = True
                    break

                if found_on_page == 0 and len(items) == 0:
                    self.logger.info(f"  Página {page_num} vacía, fin")
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

        return collected

    async def _scroll_to_bottom(self, page) -> None:
        prev_height = -1
        for _ in range(5):
            h = await page.evaluate("document.body.scrollHeight")
            if h == prev_height:
                break
            prev_height = h
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(DELAY_SCROLL)

    async def _extract_feed_items(self, page) -> list[dict]:
        """
        Extrae todos los a[class*='feed__item'] de la página.
        Retorna lista de {href, title, date}.
        La clase puede ser 'feed__item' o variantes como 'feed__item--featured'.
        """
        return await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // Buscar todos los elementos a con clase que contenga 'feed__item'
                document.querySelectorAll('a[class*="feed__item"]').forEach(a => {
                    const href  = a.getAttribute('href') || '';
                    const title = (a.getAttribute('title') || '').trim();

                    if (!href || !title) return;
                    if (seen.has(href)) return;
                    seen.add(href);

                    // Intentar extraer fecha del elemento (si tiene data-date o time)
                    let date = '';
                    const timeEl = a.querySelector('time[datetime]');
                    if (timeEl) date = timeEl.getAttribute('datetime') || '';

                    // También buscar div.feed__time con texto de fecha
                    const timeDiv = a.querySelector('[class*="feed__time"]');
                    if (!date && timeDiv) {
                        date = (timeDiv.innerText || '').trim();
                    }

                    results.push({ href, title, date });
                });

                return results;
            }
        """)

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
            # Fecha: div[class*='single-layout__meta-date']
            # Buscar sin jerarquía
            # -----------------------------------------------------------
            publication_date = ""
            pub_dt = None

            date_div = await page.query_selector(
                "div.single-layout__meta-date, "
                "div[class*='single-layout__meta-date'], "
                "div[class*='meta-date']"
            )
            if date_div:
                date_text = await date_div.inner_text()
                publication_date, pub_dt = parse_repretel_date(date_text)

            # Fallback: meta tag
            if not publication_date:
                meta_el = await page.query_selector(
                    'meta[property="article:published_time"]'
                )
                if meta_el:
                    raw_dt = await meta_el.get_attribute("content")
                    if raw_dt:
                        publication_date = parse_iso_date(raw_dt)
                        try:
                            pub_dt = datetime.fromisoformat(
                                raw_dt.replace("Z", "+00:00")
                            ).astimezone(CR_TZ)
                        except Exception:
                            pass

            # Validar fecha contra corte (2023) — descartar artículos viejos
            # que llegaron sin fecha en el listado
            if pub_dt and pub_dt < DATE_CUTOFF:
                self.logger.debug(
                    f"  Artículo anterior a 2023, descartando: {link_data['url']}"
                )
                return None

            # Sin fecha → marcar como NULL (no descartar, el validador de base lo manejará)
            # publication_date quedará "" y base_scraper lo descartará por ser nulo

            # -----------------------------------------------------------
            # Texto: div[class*='single-layout__article'] sin jerarquía
            # -----------------------------------------------------------
            content_div = await page.query_selector(
                "div.single-layout__article, "
                "div[class*='single-layout__article']"
            )

            # Fallback: div.single-layout__content
            if not content_div:
                content_div = await page.query_selector(
                    "div[class*='single-layout__content'], "
                    "div[class*='single-layout']"
                )

            if not content_div:
                content_div = await page.query_selector(
                    "div.entry-content, article"
                )

            if not content_div:
                self.logger.warning(f"Sin contenedor: {link_data['url']}")
                return None

            full_text = await content_div.evaluate("""
                (el) => {
                    const clone = el.cloneNode(true);

                    const remove_sels = [
                        // Imágenes y figuras
                        'figure', 'figcaption', '[class*="wp-block-image"]',
                        // Publicidad
                        '[class*="ad-"]', '[id*="ad"]',
                        '[class*="adsbygoogle"]', 'ins',
                        // Redes sociales y sharing
                        '[class*="share"]', '[class*="social"]',
                        '[class*="sharedaddy"]',
                        // Embeds externos (Facebook, Twitter, etc.)
                        'iframe', '[class*="wp-block-embed"]',
                        '.fb-post', '[class*="twitter-tweet"]',
                        // Navegación y relacionados
                        'nav', '[class*="related"]',
                        '[class*="navigation"]',
                        // Comentarios
                        '#comments', '[class*="comments"]',
                        // Scripts y estilos
                        'script', 'style',
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
                "section": "Actualidad",
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

    parser = argparse.ArgumentParser(description="Scraper Repretel")
    parser.add_argument(
        "--test", action="store_true",
        help=(
            f"Modo prueba: {TEST_MAX_PAGES} páginas, "
            f"{TEST_MAX_ARTICLES} artículos"
        )
    )
    parser.add_argument("--output", default="output")
    parser.add_argument("--logs", default="logs")
    args = parser.parse_args()

    scraper = RepretelScraper(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
