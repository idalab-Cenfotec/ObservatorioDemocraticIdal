"""
puroperiodismo.py
Scraper para Puro Periodismo (https://www.puroperiodismo.com/)

Secciones (8):
  - Nacionales    : https://www.puroperiodismo.com/nacionales/
  - Actualidad    : https://www.puroperiodismo.com/category/actualidad/
  - Opinión       : https://www.puroperiodismo.com/opinion/
  - Blogs         : https://www.puroperiodismo.com/category/opinion/blogs/
  - Internacional : https://www.puroperiodismo.com/category/internacional/
  - Empresas      : https://www.puroperiodismo.com/category/tech/empresas/
  - Titulares     : https://www.puroperiodismo.com/category/titulares/
  - Inseguridad   : https://www.puroperiodismo.com/category/inseguridad/

Paginación: /page/N/ (WordPress estándar)
Algunas secciones son de página única (Nacionales, Opinión) — se trata igual,
simplemente retornan 404 al intentar /page/2/.

Estructura listado:
  - Título y URL : a[href][title]  (buscar sin jerarquía todos los a con title)
                   Filtrar solo los que sean URLs de artículo válidas
  - Fecha        : div[class*='td-module-meta-info'] time[datetime]

Estructura artículo:
  - Texto : div[class*='td-post-content'] → p[class*='wp-block'] y p sin clase
  - Fecha : del listado (ISO); fallback meta tag

Deduplicación global entre secciones.

Modo prueba: --test en CLI o test_mode=True en constructor.
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

BASE_URL = "https://www.puroperiodismo.com"

SECTIONS = [
    ("https://www.puroperiodismo.com/nacionales/",              "Nacionales"),
    ("https://www.puroperiodismo.com/category/actualidad/",     "Actualidad"),
    ("https://www.puroperiodismo.com/opinion/",                 "Opinión"),
    ("https://www.puroperiodismo.com/category/opinion/blogs/",  "Blogs"),
    ("https://www.puroperiodismo.com/category/internacional/",  "Internacional"),
    ("https://www.puroperiodismo.com/category/tech/empresas/",  "Empresas"),
    ("https://www.puroperiodismo.com/category/titulares/",      "Titulares"),
    ("https://www.puroperiodismo.com/category/inseguridad/",    "Inseguridad"),
]

ARTICLE_TIMEOUT = 22_000
DELAY_BETWEEN_ARTICLES = 1.2
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
    """
    Artículos de Puro Periodismo tienen formato:
    /YYYY/MM/slug/  o  /seccion/slug/
    Excluye páginas de categoría, autor, tag, paginación, etc.
    """
    if not url or not url.startswith("https://www.puroperiodismo.com/"):
        return False
    exclude = [
        "/category/", "/author/", "/tag/", "/page/",
        "/?", "/#", "/wp-", "/feed",
        # Excluir URLs base de sección que coinciden parcialmente
        "puroperiodismo.com/nacionales/",
        "puroperiodismo.com/opinion/",
        "puroperiodismo.com/internacional/",
        "puroperiodismo.com/titulares/",
        "puroperiodismo.com/inseguridad/",
    ]
    for pat in exclude:
        if pat in url:
            return False
    path = url.replace("https://www.puroperiodismo.com/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    # Artículos tienen al menos 2 segmentos (año/slug o seccion/slug)
    return len(parts) >= 2


class PuroPeriodismoScraper(BaseScraper):
    """
    Scraper para Puro Periodismo.
    - 8 secciones con paginación /page/N/
    - Scroll al final antes de cambiar página
    - Deduplicación global entre secciones
    - Extracción de título desde a[title] (más limpio que inner_text)
    - Modo prueba: limita secciones, páginas y artículos
    """

    SOURCE_NAME = "puroperiodismo"
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
            article_links = {}  # url → {url, title, section, publication_date}

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
                    f"  → {len(links)} encontrados | {new_count} nuevos únicos | "
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
                await asyncio.sleep(DELAY_BETWEEN_ARTICLES + random.uniform(0.1, 0.5))

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
        En cada página: scroll completo → extrae artículos → siguiente.
        Para secciones de página única, /page/2/ retorna 404 y termina.
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
                else base_url.rstrip("/") + f"/page/{page_num}/"
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
                    f"{found_on_page} nuevos | total sección: {len(collected)}"
                )

                # Si no hay artículos nuevos ni elementos en la página → fin
                if found_on_page == 0:
                    # Doble verificación: buscar cualquier enlace de artículo
                    sample = await page.query_selector_all("a[title][href*='puroperiodismo']")
                    if len(sample) == 0:
                        self.logger.debug(f"  [{section_name}] Página vacía, terminando")
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
        """Scroll progresivo hasta el final para lazy-loading."""
        prev_height = -1
        for _ in range(6):
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
        Extrae artículos buscando a[href][title] que sean URLs de artículo válidas.
        Busca la fecha en el div[class*='td-module-meta-info'] más cercano.
        Retorna número de artículos NUEVOS (no en el dict global de collected).
        """
        found_new = 0

        # Extraer todos los pares (href, title, datetime) de una sola evaluación JS
        # para eficiencia — evita múltiples round-trips Playwright→browser
        items = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // Buscar todos los <a href title> que parezcan artículos
                document.querySelectorAll('a[href][title]').forEach(a => {
                    const href = a.getAttribute('href') || '';
                    const title = (a.getAttribute('title') || a.innerText || '').trim();

                    if (!href.startsWith('https://www.puroperiodismo.com/')) return;
                    if (seen.has(href)) return;
                    if (title.length < 5) return;

                    // Excluir patrones de no-artículo
                    const excl = ['/category/', '/author/', '/tag/', '/page/',
                                  '/?', '/#', '/wp-', '/feed'];
                    if (excl.some(p => href.includes(p))) return;

                    // Necesita al menos 2 segmentos de path
                    const path = href.replace('https://www.puroperiodismo.com/', '').replace(/\\/$/, '');
                    if (path.split('/').filter(Boolean).length < 2) return;

                    seen.add(href);

                    // Buscar fecha en el módulo contenedor del enlace
                    let pubDate = '';
                    const container = a.closest(
                        '[class*="td-module"], article, [class*="post"], '
                        + '[class*="loop"], [class*="card"]'
                    );
                    if (container) {
                        const timeEl = container.querySelector('time[datetime]');
                        if (timeEl) pubDate = timeEl.getAttribute('datetime') || '';
                    }

                    results.push({ href, title, pubDate });
                });

                return results;
            }
        """)

        for item in items:
            href = item.get("href", "")
            title = clean_text(item.get("title", ""))
            pub_date_raw = item.get("pubDate", "")

            if not href or not is_article_url(href):
                continue
            if href in collected:
                continue
            if not title:
                continue

            pub_date = parse_iso_date(pub_date_raw) if pub_date_raw else ""

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
        """
        Visita el artículo.
        Fecha : del listado (ISO); fallback meta tag
        Texto : div[class*='td-post-content'] →
                p[class*='wp-block'] y p sin clase específica
        """
        page = await context.new_page()

        try:
            await page.goto(
                link_data["url"], wait_until="domcontentloaded", timeout=ARTICLE_TIMEOUT
            )
            await page.wait_for_timeout(1500)

            # -----------------------------------------------------------
            # Fecha: mejorar con meta tag si ya viene del listado
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
            # Sección: refinar desde categoría del artículo si hay
            # -----------------------------------------------------------
            section = link_data.get("section", "")
            try:
                cat_el = await page.query_selector(
                    "a.td-post-category, "
                    "[class*='td-post-cat'] a, "
                    "a[rel='category tag']"
                )
                if cat_el:
                    cat_text = clean_text(await cat_el.inner_text())
                    if cat_text:
                        section = cat_text
            except Exception:
                pass

            # -----------------------------------------------------------
            # Texto: div[class*='td-post-content'] sin importar jerarquía
            # Extrae p[class*='wp-block'] + p sin clase + blockquote
            # -----------------------------------------------------------
            content_div = await page.query_selector(
                "div.td-post-content, div[class*='td-post-content']"
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
                        // Publicidad (Puro Periodismo tiene muchos bloques adsense)
                        '.code-block', '[class*="code-block"]',
                        '.td-a-rec', '[class*="td-a-rec"]',
                        '[class*="td-a-ad"]',
                        'ins.adsbygoogle', '.adsbygoogle',
                        '[id*="google_ads"]',
                        // Imagen destacada
                        '.td-post-featured-image', '[class*="featured-image"]',
                        // Redes sociales y sharing
                        '.sharedaddy', '[class*="sharedaddy"]',
                        '[class*="sd-sharing"]', '[class*="sd-block"]',
                        '[class*="sd-like"]', '[class*="jetpack-likes"]',
                        // Artículos relacionados
                        '[class*="related"]', '[id*="jp-relatedposts"]',
                        // Navegación
                        'nav', '.navigation', '[class*="post-navigation"]',
                        // Scripts, estilos, iframes
                        'script', 'style', 'iframe',
                        'figure', 'figcaption',
                        // Comentarios
                        '#comments', '.comments-area',
                        // Autor y tags
                        '[class*="author"]', '[class*="tags"]',
                    ];

                    remove_sels.forEach(sel => {
                        try {
                            clone.querySelectorAll(sel).forEach(e => e.remove());
                        } catch(err) {}
                    });

                    const parts = [];

                    // Priorizar p[class*='wp-block'] y p genéricos
                    // También capturar blockquote como texto
                    clone.querySelectorAll('p, blockquote p, li').forEach(el => {
                        const text = (el.innerText || el.textContent || '')
                            .replace(/\\s+/g, ' ').trim();
                        if (text.length >= 15) parts.push(text);
                    });

                    // Deduplicar manteniendo orden
                    const seen = new Set();
                    return parts
                        .filter(t => {
                            if (seen.has(t)) return false;
                            seen.add(t);
                            return true;
                        })
                        .join('\\n\\n');
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

    parser = argparse.ArgumentParser(description="Scraper Puro Periodismo")
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

    scraper = PuroPeriodismoScraper(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
