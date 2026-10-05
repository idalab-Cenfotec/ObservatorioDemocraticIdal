"""
noticiasenlineacr.py
Scraper para Noticias en Línea CR (https://noticiasenlineacr.com/)

Secciones (4):
  - Inicio       : https://noticiasenlineacr.com/
  - Nacionales   : https://noticiasenlineacr.com/category/nacionales/
  - Última Hora  : https://noticiasenlineacr.com/category/ultima-hora/
  - Suceso       : https://noticiasenlineacr.com/category/suceso/

Dinámica de listado:
  - Botón "Cargar más" al final del scroll
    <a id="load-more-archives" class="... load-more-button ...">Cargar más</a>
  - Sitio tiene protección ligera → delays conservadores, user-agent real

Estructura listado:
  - Título y URL : h2[class*='entry-title'] > a
  - Fecha        : span.date.meta-item  ("04/26/2025" → MM/DD/YYYY)

Estructura artículo:
  - Contenedor : article[id*='the-post'] > div[class*='entry-content']
  - Texto      : todos los <p> dentro de entry-content
  - Fecha      : del listado; fallback JSON-LD datePublished

Deduplicación global: Inicio suele repetir artículos de otras secciones.
"""

import asyncio
import re
import random
import json
from datetime import datetime, timezone, timedelta

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.base_scraper import BaseScraper, CorteIncremental

CR_TZ = timezone(timedelta(hours=-6))

BASE_URL = "https://noticiasenlineacr.com"

# Inicio primero para poblar el dict; las categorías aportan lo que falte
SECTIONS = [
    ("https://noticiasenlineacr.com/",                          "Inicio"),
    ("https://noticiasenlineacr.com/category/nacionales/",      "Nacionales"),
    ("https://noticiasenlineacr.com/category/ultima-hora/",     "Última Hora"),
    ("https://noticiasenlineacr.com/category/suceso/",          "Suceso"),
]

MAX_LOAD_MORE = 60      # clics máximos por sección (producción)
ARTICLE_TIMEOUT = 25_000
DELAY_BETWEEN_ARTICLES = 2.0   # conservador
DELAY_BETWEEN_SECTIONS = 4.0
DELAY_LOAD_MORE = 3500          # ms tras clic — sitio tiene carga lenta
DELAY_SCROLL = 2200

# Modo prueba
TEST_MAX_SECTIONS = 2
TEST_MAX_LOAD_MORE = 3
TEST_MAX_ARTICLES = 6


def parse_mdy(text: str) -> str:
    """Parsea '04/26/2025' (MM/DD/YYYY) → '2025-04-26'."""
    text = text.strip()
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if match:
        month = int(match.group(1))
        day   = int(match.group(2))
        year  = int(match.group(3))
        if year < 100:
            year += 2000
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
    if not url:
        return False
    if not url.startswith("https://noticiasenlineacr.com/"):
        return False
    exclude = ["/category/", "/author/", "/tag/", "/page/",
               "/?", "/#", "/wp-", "/feed", "noticiasenlineacr.com/#",
               "noticiasenlineacr.com/$"]
    for pat in exclude:
        if pat in url:
            return False
    path = url.replace("https://noticiasenlineacr.com/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 1


class NoticiasEnLineaCRScraper(BaseScraper):
    """
    Scraper para Noticias en Línea CR.
    - 4 secciones con botón "Cargar más" (no paginación)
    - Deduplicación global entre secciones (Inicio repite artículos)
    - Delays conservadores: sitio con protección ligera
    - Modo prueba vía --test en CLI
    """

    SOURCE_NAME = "noticiasenlineacr"
    BASE_URL = BASE_URL + "/"

    def __init__(self, output_dir="output", log_dir="logs", test_mode=False):
        super().__init__(output_dir=output_dir, log_dir=log_dir)
        self.test_mode = test_mode
        if test_mode:
            self.logger.info(
                f"*** MODO PRUEBA: {TEST_MAX_SECTIONS} secciones | "
                f"{TEST_MAX_LOAD_MORE} 'Cargar más' | "
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
                locale="es-CR",
                viewport={"width": 1280, "height": 900},
                extra_http_headers={
                    "Accept-Language": "es-CR,es;q=0.9,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": "https://www.google.com/",
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

            for i, link_data in enumerate(links_list):
                if self.tiempo_agotado():
                    break
                self.logger.debug(f"[{i+1}/{len(links_list)}] {link_data['url']}")
                record = await self._scrape_article(context, link_data)
                if record:
                    records.append(record)

                # Delay variable para no ser detectado como bot
                await asyncio.sleep(DELAY_BETWEEN_ARTICLES + random.uniform(0.5, 1.5))

                # Pausa extra cada 15 artículos
                if (i + 1) % 15 == 0:
                    self.logger.info(f"  Pausa de seguridad tras {i+1} artículos...")
                    await asyncio.sleep(6 + random.uniform(1, 3))

            await browser.close()
            return records

    # ------------------------------------------------------------------
    # Recolección con botón "Cargar más"
    # ------------------------------------------------------------------

    async def _collect_section(
        self, context, section_url: str, section_name: str
    ) -> list[dict]:
        page = await context.new_page()
        collected = {}
        max_clicks = TEST_MAX_LOAD_MORE if self.test_mode else MAX_LOAD_MORE

        try:
            await page.goto(section_url, wait_until="domcontentloaded", timeout=30_000)
            # Espera inicial más larga para sitio protegido
            await page.wait_for_timeout(3000)

            click_count = 0

            corte = CorteIncremental(self)

            while click_count <= max_clicks:
                if self.tiempo_agotado(0.5):
                    self.logger.warning(f"  [{section_name}] Tiempo del listado agotado, se pasa a los artículos")
                    break
                # Scroll hasta el final
                await self._scroll_to_bottom(page)

                # Extraer artículos visibles
                n_antes = len(collected)
                new_round = await self._extract_cards(page, collected, section_name)
                # Varias páginas/rondas seguidas con solo URLs ya cargadas -> fin de sección
                if corte.pagina([l['url'] for l in list(collected.values())[n_antes:]]):
                    self.logger.info(f"  [{section_name}] Ronda {click_count}: solo URLs ya conocidas, fin de sección")
                    break
                self.logger.debug(
                    f"  [{section_name}] Ronda {click_count}: "
                    f"{new_round} nuevos | total: {len(collected)}"
                )

                # Intentar clic en "Cargar más"
                clicked = await self._click_load_more(page)
                if not clicked:
                    self.logger.debug(f"  [{section_name}] Sin 'Cargar más', sección completa")
                    break

                click_count += 1
                await page.wait_for_timeout(DELAY_LOAD_MORE)

        except PlaywrightTimeoutError:
            self.logger.warning(f"Timeout en sección: {section_url}")
        except Exception as e:
            self.logger.error(f"Error en {section_url}: {e}", exc_info=True)
        finally:
            await page.close()

        return list(collected.values())

    async def _scroll_to_bottom(self, page) -> None:
        """Scroll progresivo hasta el final."""
        prev_height = -1
        for _ in range(6):
            current_height = await page.evaluate("document.body.scrollHeight")
            if current_height == prev_height:
                break
            prev_height = current_height
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(DELAY_SCROLL)

    async def _click_load_more(self, page) -> bool:
        """
        Busca y hace clic en el botón 'Cargar más'.
        Selector primario: a#load-more-archives
        Fallback: a[class*='load-more-button'] o texto 'Cargar más'
        """
        try:
            # Selector más específico del sitio
            btn = await page.query_selector(
                "a#load-more-archives, "
                "a[id*='load-more'], "
                "a[class*='load-more-button']"
            )

            # Fallback por texto
            if not btn:
                anchors = await page.query_selector_all("a")
                for a in anchors:
                    try:
                        text = (await a.inner_text()).strip().lower()
                        if "cargar más" in text or "cargar mas" in text:
                            btn = a
                            break
                    except Exception:
                        continue

            if not btn:
                return False

            visible = await btn.is_visible()
            if not visible:
                return False

            await btn.scroll_into_view_if_needed()
            await page.wait_for_timeout(800)

            # Clic normal primero
            try:
                await btn.click(timeout=5000)
            except Exception:
                # Fallback: clic por JavaScript si el normal falla
                await page.evaluate("(el) => el.click()", btn)

            return True

        except Exception as e:
            self.logger.debug(f"Error en 'Cargar más': {e}")
            return False

    async def _extract_cards(
        self, page, collected: dict, section_name: str
    ) -> int:
        """
        Extrae tarjetas buscando h2[class*='entry-title'] sin jerarquía.
        """
        found_new = 0

        cards = await page.query_selector_all(
            "h2[class*='entry-title'], h2.entry-title"
        )

        for card in cards:
            try:
                anchor = await card.query_selector("a[href]")
                if not anchor:
                    continue

                href = await anchor.get_attribute("href")
                if not href or not is_article_url(href):
                    continue
                if href in collected:
                    continue

                title = clean_text(await anchor.inner_text())
                if not title:
                    title = await anchor.get_attribute("title") or ""
                    title = clean_text(title)
                if not title:
                    continue

                # Fecha: span.date.meta-item más cercano al h2
                pub_date = ""
                try:
                    pub_date = await anchor.evaluate("""
                        (el) => {
                            // Subir hasta el contenedor del artículo
                            let container = el.closest(
                                'article, [class*="post"], [class*="loop-item"], '
                                + '[class*="container-wrapper"]'
                            );
                            if (!container) {
                                container = el.parentElement?.parentElement?.parentElement;
                            }
                            if (!container) return '';

                            // Buscar span.date
                            const dateEl = container.querySelector(
                                'span.date, span[class*="date"]'
                            );
                            return dateEl ? (dateEl.innerText || '').trim() : '';
                        }
                    """)
                    if pub_date:
                        pub_date = parse_mdy(pub_date)
                except Exception:
                    pass

                collected[href] = {
                    "url": href.strip(),
                    "title": title,
                    "section": section_name,
                    "publication_date": pub_date,
                }
                found_new += 1

            except Exception as e:
                self.logger.debug(f"Error en tarjeta: {e}")
                continue

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
            await page.wait_for_timeout(2000)

            # -----------------------------------------------------------
            # Fecha: mejorar con JSON-LD datePublished (más precisa)
            # -----------------------------------------------------------
            publication_date = link_data.get("publication_date", "")

            try:
                jsonld_el = await page.query_selector(
                    'script[type="application/ld+json"]#tie-schema-json, '
                    'script[type="application/ld+json"]'
                )
                if jsonld_el:
                    jsonld_text = await jsonld_el.inner_text()
                    jsonld = json.loads(jsonld_text)
                    raw_dt = jsonld.get("datePublished", "")
                    if raw_dt:
                        publication_date = parse_iso_date(raw_dt)
            except Exception:
                pass

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
            # Sección: intentar refinar desde breadcrumb del artículo
            # -----------------------------------------------------------
            section = link_data.get("section", "")
            try:
                cat_el = await page.query_selector(
                    "span.post-cat-wrap a, "
                    ".post-cat, "
                    "nav#breadcrumb a:nth-child(2)"
                )
                if cat_el:
                    cat_text = clean_text(await cat_el.inner_text())
                    if cat_text and cat_text.lower() not in ("inicio", "home"):
                        section = cat_text
            except Exception:
                pass

            # -----------------------------------------------------------
            # Contenedor: article[id*='the-post']
            # Texto: div[class*='entry-content'] → todos los <p>
            # -----------------------------------------------------------
            article_el = await page.query_selector(
                "article[id*='the-post'], article#the-post"
            )
            if not article_el:
                article_el = await page.query_selector("article")

            if not article_el:
                self.logger.warning(f"Sin article: {link_data['url']}")
                return None

            content_div = await article_el.query_selector(
                "div[class*='entry-content'], div.entry-content, div.entry"
            )
            if not content_div:
                content_div = article_el

            full_text = await content_div.evaluate("""
                (el) => {
                    const clone = el.cloneNode(true);

                    const remove_sels = [
                        // Sharing y redes sociales
                        '[id*="share-buttons"]', '[class*="share-buttons"]',
                        '[class*="share-links"]',
                        // Publicidad
                        '[class*="ad-"]', '[id*="_ad"]',
                        // Comentarios (Facebook y nativos)
                        '#fb-root', '.fb-comments', '[class*="heateor"]',
                        '#comments', '.comments-area', '.comment-respond',
                        // Navegación
                        'nav', '#breadcrumb',
                        // Meta y autor
                        '[class*="post-meta"]', '[class*="single-post-meta"]',
                        '[id*="post-extra-info"]',
                        // Figuras, videos e imágenes
                        'figure', 'figcaption',
                        '.wp-video', '.mejs-container',
                        // Scripts y estilos
                        'script', 'style', 'iframe',
                        // Bloque relacionado
                        '[class*="related"]', '[class*="tie-related"]',
                    ];

                    remove_sels.forEach(sel => {
                        try {
                            clone.querySelectorAll(sel).forEach(e => e.remove());
                        } catch(err) {}
                    });

                    const parts = [];
                    clone.querySelectorAll('p').forEach(p => {
                        const text = (p.innerText || p.textContent || '')
                            .replace(/\\s+/g, ' ').trim();
                        // Filtrar párrafos vacíos o solo espacios (&nbsp;)
                        if (text.length >= 15 && text !== '\u00a0') {
                            parts.push(text);
                        }
                    });

                    // Las notas nuevas del sitio no usan <p>: el texto va en <div> sueltos.
                    // Respaldo: bloques hoja (sin div/p/li adentro) con texto propio.
                    if (parts.length === 0) {
                        clone.querySelectorAll('div, li, blockquote').forEach(b => {
                            if (b.querySelector('div, p, li')) return;
                            const text = (b.textContent || '').replace(/\\s+/g, ' ').trim();
                            if (text.length >= 15 && text !== ' ') {
                                parts.push(text);
                            }
                        });
                    }

                    return parts.join('\\n\\n');
                }
            """)

            full_text = clean_text(full_text) if full_text else ""

            if not full_text:
                self.logger.warning(f"Sin texto: {link_data['url']} (título de la página: {await page.title()!r})")
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

    parser = argparse.ArgumentParser(description="Scraper Noticias en Línea CR")
    parser.add_argument(
        "--test", action="store_true",
        help=(
            f"Modo prueba: {TEST_MAX_SECTIONS} secciones, "
            f"{TEST_MAX_LOAD_MORE} 'Cargar más', "
            f"{TEST_MAX_ARTICLES} artículos/sección"
        )
    )
    parser.add_argument("--output", default="output")
    parser.add_argument("--logs", default="logs")
    args = parser.parse_args()

    scraper = NoticiasEnLineaCRScraper(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
