"""
lavozdegoicoechea.py
Scraper optimizado para La Voz de Goicoechea
"""

import asyncio
import re
from datetime import datetime, timezone, timedelta

from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError
)

import sys
import os

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )
)

from scrapers.base_scraper import BaseScraper, cerrar_pagina

CR_TZ = timezone(timedelta(hours=-6))

BASE_URL = "https://lavozdegoicoechea.com/"

SECTIONS = [
    ("https://lavozdegoicoechea.com/blog/", "Noticia"),
    ("https://lavozdegoicoechea.com/category/cantonales/", "Cantonal"),
    ("https://lavozdegoicoechea.com/category/noticia/nacionales/", "Nacionales"),
    ("https://lavozdegoicoechea.com/category/noticia/personajes/", "Personajes"),
    ("https://lavozdegoicoechea.com/category/noticia/la-hormiga/", "La Hormiga"),
    ("https://lavozdegoicoechea.com/category/noticia/editorial/", "Editorial"),
]

ARTICLE_TIMEOUT = 20000

MAX_SCROLLS = 6

# En test mode, limitar scrolls para rapidez
TEST_MAX_SCROLLS = 2

MAX_PAGES_PER_SECTION = 10

# En test mode, limitar paginación a 1 página
TEST_MAX_PAGES = 1

MAX_CONCURRENT_ARTICLES = 5

DELAY_SCROLL = 1200
DELAY_BETWEEN_PAGES = 1.5

# Modo prueba: limita número de artículos procesados
TEST_MAX_ARTICLES = 5

def clean_text(text: str) -> str:

    text = re.sub(r"[ \t]+", " ", text)

    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def parse_iso_date(raw: str) -> str:

    try:

        raw = raw.strip().replace("Z", "+00:00")

        dt = datetime.fromisoformat(raw)

        dt_cr = dt.astimezone(CR_TZ)

        return dt_cr.strftime("%Y-%m-%d %H:%M:%S")

    except Exception:

        return raw[:10] if raw else ""


def is_article_url(url: str) -> bool:

    if not url:
        return False

    if not url.startswith(BASE_URL):
        return False

    url = url.lower().strip()

    excluded = [

        "/category/",
        "/tag/",
        "/author/",
        "/page/",
        "/feed/",
        "/wp-",

        "/contact",
        "/contacto",
        "/about",
        "/acerca",
        "/privacy",
        "/politica",
        "/terminos",
        "/terms",

        "/anunciese",
        "/anuncie",
        "/publicidad",

        "/facebook",
        "/instagram",
        "/twitter",
        "/youtube",

        "#",
        "?",
    ]

    for e in excluded:

        if e in url:
            return False

    path = (
        url
        .replace(BASE_URL, "")
        .strip("/")
    )

    if not path:
        return False

    # evita URLs basura cortas
    if len(path.split("-")) < 2:
        return False

    return True


class LaVozDeGoicoecheaScraper(BaseScraper):

    SOURCE_NAME = "lavozdegoicoechea"

    BASE_URL = BASE_URL

    def __init__(self, output_dir="output", log_dir="logs", test_mode: bool = False):
        super().__init__(output_dir=output_dir, log_dir=log_dir)
        self.test_mode = test_mode
        if self.test_mode:
            self.logger.info(f"*** MODO PRUEBA ACTIVO: limitando a {TEST_MAX_ARTICLES} artículos ***")

    def scrape(self):

        return asyncio.run(
            self._scrape_async()
        )

    async def _scrape_async(self):

        async with async_playwright() as p:

            browser = await p.chromium.launch(
                headless=True
            )

            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="es-CR",
            )

            # =====================================================
            # BLOQUEAR RECURSOS PESADOS
            # =====================================================

            await context.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if route.request.resource_type in [
                        "image",
                        "media",
                        "font"
                    ]
                    else route.continue_()
                )
            )

            # =====================================================
            # RECOLECTAR LINKS
            # =====================================================

            article_links = {}

            for section_url, section_name in SECTIONS:

                self.logger.info(
                    f"Recolectando sección: "
                    f"{section_name}"
                )

                links = await self.seccion_segura(
                    self._collect_section(
                        context,
                        section_url,
                        section_name
                    ),
                    section_name
                )

                new_count = 0

                for link in links:

                    if link["url"] not in article_links:

                        article_links[
                            link["url"]
                        ] = link

                        new_count += 1

                self.logger.info(
                    f"{section_name}: "
                    f"{len(links)} encontrados | "
                    f"{new_count} nuevos | "
                    f"Total global: "
                    f"{len(article_links)}"
                )

            self.logger.info(
                f"TOTAL URLs ÚNICAS: "
                f"{len(article_links)}"
            )

            # =====================================================
            # SCRAPEAR ARTÍCULOS
            # =====================================================

            semaphore = asyncio.Semaphore(
                MAX_CONCURRENT_ARTICLES
            )

            registros = []   # para el guardado parcial (gather solo devuelve al final)

            async def bounded_scrape(link):

                async with semaphore:

                    if self.tiempo_agotado():
                        return None

                    rec = await self.articulo_seguro(
                        context,
                        link
                    )
                    if rec:
                        registros.append(rec)
                        self.checkpoint(registros)
                    return rec

            links_list = [l for l in article_links.values() if not self.debe_omitir(l["url"])]
            if self.incremental:
                self.logger.info(
                    f"Artículos nuevos por visitar: {len(links_list)} "
                    f"({self.omitidos_conocidos} ya estaban en la base)"
                )
            if self.test_mode:
                links_list = links_list[:TEST_MAX_ARTICLES]

            tasks = [
                bounded_scrape(link)
                for link in links_list
            ]

            results = await asyncio.gather(*tasks)

            records = [
                r for r in results if r
            ]

            await browser.close()

            return records

    # ============================================================
    # RECOLECTAR SECCIÓN
    # ============================================================

    async def _collect_section(
        self,
        context,
        section_url,
        section_name
    ):

        collected = {}

        page_num = 1

        no_growth_rounds = 0
        
        # En test mode, limitar páginas a procesar
        max_pages = TEST_MAX_PAGES if self.test_mode else MAX_PAGES_PER_SECTION

        while page_num <= max_pages:

            if page_num == 1:

                url = section_url

            else:

                url = (
                    section_url.rstrip("/")
                    + f"/page/{page_num}/"
                )

            page = await context.new_page()

            try:

                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=25000
                )

                if response and response.status == 404:
                    break

                await page.wait_for_timeout(1500)

                # =================================================
                # SCROLL
                # =================================================

                # En test mode, usar pocos scrolls para rapidez
                max_scrolls = TEST_MAX_SCROLLS if self.test_mode else MAX_SCROLLS

                previous_height = 0

                for _ in range(max_scrolls):

                    current_height = await page.evaluate(
                        "document.body.scrollHeight"
                    )

                    if current_height == previous_height:
                        break

                    previous_height = current_height

                    await page.evaluate("""
                        window.scrollTo(
                            0,
                            document.body.scrollHeight
                        )
                    """)

                    await page.wait_for_timeout(
                        DELAY_SCROLL
                    )

                # =================================================
                # EXTRAER LINKS
                # =================================================

                links_data = await page.evaluate("""
                    () => {

                        const results = [];

                        const anchors =
                            document.querySelectorAll(
                                'h1 a[href], h2 a[href], h3 a[href], h4 a[href]'
                            );

                        anchors.forEach(a => {

                            const href =
                                a.href || '';

                            if (
                                !href.includes(
                                    'lavozdegoicoechea.com'
                                )
                            ) return;

                            let title = (
                                a.innerText ||
                                a.textContent ||
                                ''
                            )
                            .replace(/\\s+/g, ' ')
                            .trim();

                            if (title.length < 20)
                                return;

                            results.push({
                                url: href,
                                title: title
                            });

                        });

                        const seen = new Set();

                        return results.filter(r => {

                            if (seen.has(r.url))
                                return false;

                            seen.add(r.url);

                            return true;
                        });
                    }
                """)

                new_this_page = 0

                for item in links_data:

                    url = item.get("url", "")

                    if not is_article_url(url):
                        continue

                    if url in collected:
                        continue

                    collected[url] = {
                        "url": url,
                        "title": clean_text(
                            item.get("title", "")
                        ),
                        "section": section_name,
                        "publication_date": "",
                    }

                    new_this_page += 1

                self.logger.debug(
                    f"{section_name} | "
                    f"Pág {page_num} | "
                    f"{new_this_page} nuevos"
                )

                # =================================================
                # DETENER
                # =================================================

                if new_this_page == 0:

                    no_growth_rounds += 1

                else:

                    no_growth_rounds = 0

                if no_growth_rounds >= 2:
                    break

                page_num += 1

            except Exception as e:

                self.logger.error(
                    f"Error en {url}: {e}",
                    exc_info=True
                )

                break

            finally:

                await cerrar_pagina(page)

            await asyncio.sleep(
                DELAY_BETWEEN_PAGES
            )

        return list(collected.values())

    # ============================================================
    # SCRAPEAR ARTÍCULO
    # ============================================================

    async def _scrape_article(
        self,
        context,
        link_data
    ):

        page = await context.new_page()

        try:

            await page.goto(
                link_data["url"],
                wait_until="domcontentloaded",
                timeout=ARTICLE_TIMEOUT
            )

            await page.wait_for_timeout(1200)

            # =================================================
            # FECHA
            # =================================================

            publication_date = ""

            time_el = await page.query_selector(
                "time[datetime]"
            )

            if time_el:

                raw_dt = await time_el.get_attribute(
                    "datetime"
                )

                if raw_dt:

                    publication_date = parse_iso_date(
                        raw_dt
                    )

            # =================================================
            # TÍTULO
            # =================================================

            title = link_data.get("title", "")

            if not title or len(title) < 5:

                h1 = await page.query_selector(
                    "h1"
                )

                if h1:

                    title = clean_text(
                        await h1.inner_text()
                    )

            # =================================================
            # BODY
            # =================================================

            article = await page.query_selector(
                "article, "
                "main, "
                "div[class*='content'], "
                "div[class*='post']"
            )

            if not article:
                return None

            full_text = await article.evaluate("""
                (el) => {

                    const clone =
                        el.cloneNode(true);

                    const remove = [

                        'script',
                        'style',
                        'iframe',
                        'nav',

                        'figure',
                        'figcaption',

                        '.sharedaddy',
                        '.comments',
                        '.related',

                        '.ads',
                        '.ad',
                    ];

                    remove.forEach(sel => {

                        try {

                            clone
                                .querySelectorAll(sel)
                                .forEach(
                                    e => e.remove()
                                );

                        } catch(err) {}

                    });

                    const parts = [];

                    clone
                        .querySelectorAll(
                            'p, h2, h3, li'
                        )
                        .forEach(el => {

                            const text = (
                                el.innerText ||
                                el.textContent ||
                                ''
                            )
                            .replace(/\\s+/g, ' ')
                            .trim();

                            if (text.length > 20) {

                                parts.push(text);
                            }

                        });

                    return parts.join('\\n\\n');
                }
            """)

            full_text = clean_text(
                full_text
            )

            if not full_text:
                return None

            return {
                "url": link_data["url"],
                "title": title,
                "section": link_data.get(
                    "section",
                    ""
                ),
                "publication_date": publication_date,
                "full_text": full_text,
            }

        except PlaywrightTimeoutError:

            self.logger.warning(
                f"Timeout: "
                f"{link_data['url']}"
            )

            return None

        except Exception as e:

            self.logger.error(
                f"Error artículo "
                f"{link_data['url']}: {e}",
                exc_info=True
            )

            return None

        finally:

            await cerrar_pagina(page)


if __name__ == "__main__":

    scraper = LaVozDeGoicoecheaScraper()

    summary = scraper.run()

    print(summary)