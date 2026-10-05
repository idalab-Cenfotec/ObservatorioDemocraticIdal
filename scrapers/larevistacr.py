"""
larevistacr.py
Scraper para La Revista CR (https://www.larevista.cr/)

Secciones scrapeadas:
  - Actualidad Nacional : https://www.larevista.cr/actualidad-nacional/
  - Actualidad Mundial  : https://www.larevista.cr/actualidad-mundial/
  - Entrevistas         : https://www.larevista.cr/entrevista/

Estructura del listado:
  - Contenedor principal : div.mg-posts-sec-inner
  - Cada artículo        : article (dentro del contenedor)
  - Título y URL         : h4.entry-title a  (buscado sin importar jerarquía)
  - Fecha                : span.mg-blog-date a (texto "8 de abril de 2026")
  - Sección              : primera a dentro de div.mg-blog-category
  - Paginación           : nav.pagination a.page-numbers (WordPress estándar /page/N/)

Estructura del artículo individual:
  - Contenedor de texto  : article[class*="page-content-single"] (match parcial)
  - Texto                : todos los <p> dentro del artículo
  - Fecha en artículo    : time[datetime] o meta[property="article:published_time"]
  - Sección              : tomada del listado (no siempre aparece en el artículo)
"""

import asyncio
import re
from datetime import datetime, timezone, timedelta

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scrapers.base_scraper import BaseScraper, cerrar_pagina

CR_TZ = timezone(timedelta(hours=-6))

BASE_URL = "https://www.larevista.cr/"

# Secciones a scrapear: (slug_url, nombre_seccion_normalizado)
SECTIONS = [
    ("actualidad-nacional", "Actualidad Nacional"),
    ("actualidad-mundial",  "Actualidad Mundial"),
    ("entrevista",          "Entrevistas"),
]

ARTICLE_TIMEOUT = 20_000
DELAY_BETWEEN_ARTICLES = 1.0
DELAY_BETWEEN_PAGES = 1.5

# Máximo de páginas por sección (cada página trae ~10 artículos)
# La sección actualidad-nacional tiene 319 páginas — ajustar según necesidad del corpus
MAX_PAGES_PER_SECTION = 50

# En test mode, limitar paginación a pocas páginas
TEST_MAX_PAGES = 2

# Modo prueba: limita número de artículos procesados
TEST_MAX_ARTICLES = 5

MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def parse_date_es(text: str) -> str:
    """Parsea '8 de abril de 2026' → '2026-04-08'."""
    text = text.strip().lower()
    match = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", text)
    if match:
        day = int(match.group(1))
        month = MESES_ES.get(match.group(2), 0)
        year = int(match.group(3))
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_article_url(url: str) -> bool:
    """Filtra solo URLs de artículos de larevista.cr."""
    if not url or not url.startswith("https://www.larevista.cr/"):
        return False
    exclude = [
        "/category/", "/author/", "/tag/", "/page/",
        "/#", "/?", "/wp-", "/feed", "/actualidad-nacional",
        "/actualidad-mundial", "/entrevista", "/squid",
        "/portada", "/boletin",
    ]
    for pat in exclude:
        if pat in url:
            return False
    # Artículo tiene slug directo: /slug-del-articulo/
    path = url.replace("https://www.larevista.cr/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    # Artículos tienen exactamente 1 segmento (el slug)
    # o pueden tener año/mes/slug (algunos WP)
    return len(parts) >= 1 and not parts[0].isdigit()


class LaRevistaCRScraper(BaseScraper):
    """
    Scraper para La Revista CR.
    Recorre 3 secciones con paginación estándar de WordPress.
    Visita cada artículo y extrae el texto de article[class*='page-content-single'].
    """

    SOURCE_NAME = "larevistacr"
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
            # PASO 1: Recolectar URLs de todas las secciones
            # -------------------------------------------------------
            article_links = {}  # url -> {url, title, section, publication_date}

            for slug, section_name in SECTIONS:
                section_url = f"{BASE_URL}{slug}/"
                self.logger.info(f"Recolectando sección: {section_name} ({section_url})")
                links = await self.seccion_segura(self._collect_section(context, section_url, section_name), "_collect_section")
                for link in links:
                    if link["url"] not in article_links:
                        article_links[link["url"]] = link
                self.logger.info(
                    f"  → {len(links)} artículos en '{section_name}' | "
                    f"Total acumulado: {len(article_links)}"
                )
                await asyncio.sleep(DELAY_BETWEEN_PAGES)

            self.logger.info(f"Total URLs únicas: {len(article_links)}")

            # -------------------------------------------------------
            # PASO 2: Visitar cada artículo
            # -------------------------------------------------------
            records = []
            links_list = list(article_links.values())
            if self.test_mode:
                links_list = links_list[:TEST_MAX_ARTICLES]

            for i, link_data in enumerate(links_list):
                self.logger.debug(f"[{i+1}/{len(links_list)}] {link_data['url']}")
                record = await self.articulo_seguro(context, link_data)
                if record:
                    records.append(record)
                    self.checkpoint(records)
                await asyncio.sleep(DELAY_BETWEEN_ARTICLES)

            await browser.close()
            return records

    # ------------------------------------------------------------------
    # Recolección paginada de una sección
    # ------------------------------------------------------------------

    async def _collect_section(
        self, context, section_url: str, section_name: str
    ) -> list[dict]:
        """
        Navega todas las páginas de una sección.
        Extrae URL, título, fecha y sección de cada artículo.
        En test mode, limita paginación a TEST_MAX_PAGES.
        """
        collected = {}
        page_num = 1
        
        # En test mode, limitar número de páginas a scrapear
        max_pages = TEST_MAX_PAGES if self.test_mode else MAX_PAGES_PER_SECTION

        while page_num <= max_pages:
            if page_num == 1:
                url = section_url
            else:
                url = section_url.rstrip("/") + f"/page/{page_num}/"

            page = await context.new_page()
            found_on_page = 0

            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=25_000)

                if resp and resp.status == 404:
                    self.logger.debug(f"Página {page_num} → 404, fin de sección")
                    break
                if resp and resp.status >= 400:
                    self.logger.warning(f"Página {page_num}: HTTP {resp.status} en {url} (posible bloqueo del sitio)")
                    break

                await page.wait_for_timeout(1500)

                # Buscar el contenedor principal mg-posts-sec-inner
                # (sin importar jerarquía — puede estar anidado)
                articles = await page.query_selector_all(
                    "div.mg-posts-sec-inner article, "
                    "div[class*='mg-posts-sec'] article"
                )

                # Fallback: cualquier article en la página
                if not articles:
                    articles = await page.query_selector_all("article")

                for art in articles:
                    try:
                        # --------------------------------------------------
                        # Título y URL: h4.entry-title a (buscar sin jerarquía)
                        # --------------------------------------------------
                        title_anchor = await art.query_selector(
                            "h4.entry-title a, h4.title a, h3.entry-title a"
                        )
                        if not title_anchor:
                            # Fallback más amplio
                            title_anchor = await art.query_selector(
                                "h4 a, h3 a"
                            )
                        if not title_anchor:
                            continue

                        href = await title_anchor.get_attribute("href")
                        title = await title_anchor.inner_text()

                        if not href or not is_article_url(href):
                            continue

                        # Deduplicar ya en la recolección
                        if href in collected:
                            continue

                        # --------------------------------------------------
                        # Fecha: span.mg-blog-date a
                        # --------------------------------------------------
                        pub_date = ""
                        date_el = await art.query_selector("span.mg-blog-date a")
                        if date_el:
                            date_text = await date_el.inner_text()
                            pub_date = parse_date_es(date_text)

                        # --------------------------------------------------
                        # Sección desde mg-blog-category (primera a)
                        # Si no, usar el nombre de sección del bucle
                        # --------------------------------------------------
                        section = section_name
                        cat_el = await art.query_selector("div.mg-blog-category a")
                        if cat_el:
                            cat_text = clean_text(await cat_el.inner_text())
                            if cat_text:
                                section = cat_text

                        collected[href] = {
                            "url": href.strip(),
                            "title": clean_text(title),
                            "section": section,
                            "publication_date": pub_date,
                        }
                        found_on_page += 1

                    except Exception as e:
                        self.logger.debug(f"Error en artículo del listado: {e}")
                        continue

                self.logger.debug(
                    f"  Sección '{section_name}' pág {page_num}: {found_on_page} artículos"
                )

                # Si no encontramos artículos, terminamos la sección
                if found_on_page == 0:
                    break

                # Verificar si hay página siguiente
                next_link = await page.query_selector("a.next.page-numbers")
                if not next_link:
                    break

                page_num += 1

            except PlaywrightTimeoutError:
                self.logger.warning(f"Timeout en listado: {url}")
                break
            except Exception as e:
                self.logger.error(f"Error en listado {url}: {e}", exc_info=True)
                break
            finally:
                await cerrar_pagina(page)

            await asyncio.sleep(DELAY_BETWEEN_PAGES)

        return list(collected.values())

    # ------------------------------------------------------------------
    # Scraping del artículo individual
    # ------------------------------------------------------------------

    async def _scrape_article(self, context, link_data: dict) -> dict | None:
        """
        Visita el artículo y extrae el texto completo.
        Busca article[class*='page-content-single'] sin importar jerarquía.
        Extrae todos los <p> dentro del artículo.
        """
        page = await context.new_page()

        try:
            await page.goto(
                link_data["url"], wait_until="domcontentloaded", timeout=ARTICLE_TIMEOUT
            )
            await page.wait_for_timeout(1500)

            # -----------------------------------------------------------
            # Buscar article con clase que contenga 'page-content-single'
            # sin importar jerarquía ni nombre exacto
            # -----------------------------------------------------------
            content_article = await page.query_selector(
                "article[class*='page-content-single']"
            )

            # Fallback: article.single o article.post
            if not content_article:
                content_article = await page.query_selector(
                    "article.single, article.post, article.page-content-single"
                )

            # Fallback final: primer article en la página
            if not content_article:
                content_article = await page.query_selector("article")

            if not content_article:
                self.logger.warning(f"Sin article encontrado: {link_data['url']}")
                return None

            # -----------------------------------------------------------
            # Extraer texto de todos los <p> dentro del artículo
            # Usando JavaScript para limpiar elementos no deseados primero
            # -----------------------------------------------------------
            full_text = await content_article.evaluate("""
                (el) => {
                    const clone = el.cloneNode(true);

                    // Remover elementos no deseados
                    const remove_sels = [
                        '.sharedaddy',
                        '.sd-sharing-enabled',
                        '.jp-relatedposts',
                        '.post-share',
                        '.navigation',
                        '.nav-links',
                        '.sd-block',
                        'script',
                        'style',
                        'iframe',
                        'figure',
                        'nav',
                        '.wp-block-embed',
                        '#jp-post-flair',
                        '.post-likes-widget-placeholder',
                    ];
                    remove_sels.forEach(sel => {
                        clone.querySelectorAll(sel).forEach(e => e.remove());
                    });

                    // Extraer todos los <p> del artículo
                    const paragraphs = [];
                    clone.querySelectorAll('p').forEach(p => {
                        const text = (p.innerText || p.textContent || '')
                            .replace(/\\s+/g, ' ').trim();
                        if (text.length > 15) {
                            paragraphs.push(text);
                        }
                    });

                    return paragraphs.join('\\n\\n');
                }
            """)

            full_text = clean_text(full_text) if full_text else ""

            if not full_text:
                self.logger.warning(f"Sin texto extraído: {link_data['url']}")
                return None

            # -----------------------------------------------------------
            # Fecha: intentar mejorarla desde el artículo
            # -----------------------------------------------------------
            publication_date = link_data.get("publication_date", "")

            # Buscar meta tag de fecha (más precisa, incluye hora)
            if not publication_date:
                meta_el = await page.query_selector(
                    'meta[property="article:published_time"]'
                )
                if meta_el:
                    raw_dt = await meta_el.get_attribute("content")
                    if raw_dt:
                        try:
                            dt = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
                            dt_cr = dt.astimezone(CR_TZ)
                            publication_date = dt_cr.strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            pass

            # Buscar time[datetime] como fallback
            if not publication_date:
                time_el = await page.query_selector("time[datetime]")
                if time_el:
                    raw_dt = await time_el.get_attribute("datetime")
                    if raw_dt:
                        pub_date = parse_date_es(raw_dt) or raw_dt[:10]
                        if pub_date:
                            publication_date = pub_date

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


# Ejecución directa para pruebas
if __name__ == "__main__":
    scraper = LaRevistaCRScraper()
    summary = scraper.run()
    print(summary)
