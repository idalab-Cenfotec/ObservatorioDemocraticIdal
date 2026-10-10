"""
ncrnoticias.py
Scraper para NCR Noticias (https://ncrnoticias.com/)

Secciones (11):
  Política, Negocios, Salud, Sucesos, Internacionales,
  Tecnología, Denuncia NCR, Vitrina Comercial, Educación, Opinión, Virales

Dinámica de listado:
  - NO hay paginación numérica
  - Al final del scroll aparece un botón "Cargar más"
    <a class="td_ajax_load_more ...">Cargar más</a>
  - Se hace clic, se espera la carga y se repite hasta que no haya botón

Estructura listado:
  - Título y URL : h3[class*='entry-title'] a  (href + title)
  - Fecha        : div[class*='td-editor-date'] time[datetime]

Estructura artículo:
  - Texto : div[class*='tdb-block-inner'] → todos los <p>
            (excluye ads, comentarios, fb-comments, sharing)

Modo prueba:
  - Pasar test_mode=True al constructor o usar --test en CLI
  - Limita a 2 secciones, 3 clics de "Cargar más" y 5 artículos por sección
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

BASE_URL = "https://ncrnoticias.com/"

SECTIONS = [
    ("https://ncrnoticias.com/category/politica/",          "Política"),
    ("https://ncrnoticias.com/category/economia-negocios/", "Negocios"),
    ("https://ncrnoticias.com/category/salud/",             "Salud"),
    ("https://ncrnoticias.com/category/sucesos/",           "Sucesos"),
    ("https://ncrnoticias.com/category/internacionales/",   "Internacionales"),
    ("https://ncrnoticias.com/category/tecnologia/",        "Tecnología"),
    ("https://ncrnoticias.com/category/denuncia/",          "Denuncia NCR"),
    ("https://ncrnoticias.com/category/de-su-interes/",     "Vitrina Comercial"),
    ("https://ncrnoticias.com/category/educacion/",         "Educación"),
    ("https://ncrnoticias.com/category/opinion/",           "Opinión"),
    ("https://ncrnoticias.com/category/virales/",           "Virales"),
]

# Configuración normal
MAX_LOAD_MORE_CLICKS = 50       # Máximo de clics en "Cargar más" por sección
ARTICLE_TIMEOUT = 25_000
DELAY_BETWEEN_ARTICLES = 1.5   # segundos (más conservador para no ser bloqueado)
DELAY_BETWEEN_SECTIONS = 3.0
DELAY_LOAD_MORE = 5000          # ms tras clic en "Cargar más"
DELAY_SCROLL = 2000             # ms entre scrolls

# Configuración modo prueba
TEST_MAX_SECTIONS = 2
TEST_MAX_LOAD_MORE = 3
TEST_MAX_ARTICLES = 5


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
    if not url or not url.startswith("https://ncrnoticias.com/"):
        return False
    exclude = ["/category/", "/author/", "/tag/", "/page/",
               "/?", "/#", "/wp-", "/feed", "ncrnoticias.com/#"]
    for pat in exclude:
        if pat in url:
            return False
    path = url.replace("https://ncrnoticias.com/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 1


class NCRNoticiasScraper(BaseScraper):
    """
    Scraper para NCR Noticias.
    - 11 secciones con carga dinámica vía botón "Cargar más"
    - Deduplicación global por URL entre secciones
    - Delays variables para evitar bloqueos
    - Modo prueba: --test en CLI o test_mode=True en constructor
    """

    SOURCE_NAME = "ncrnoticias"
    BASE_URL = BASE_URL

    def __init__(self, output_dir: str = "output", log_dir: str = "logs",
                 test_mode: bool = False):
        super().__init__(output_dir=output_dir, log_dir=log_dir)
        self.test_mode = test_mode
        if test_mode:
            self.logger.info("*** MODO PRUEBA ACTIVO ***")
            self.logger.info(
                f"  Secciones: {TEST_MAX_SECTIONS} | "
                f"Cargar más: {TEST_MAX_LOAD_MORE} | "
                f"Artículos/sección: {TEST_MAX_ARTICLES}"
            )

    def scrape(self) -> list[dict]:
        return asyncio.run(self._scrape_async())

    async def _scrape_async(self) -> list[dict]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="es-CR",
                viewport={"width": 1280, "height": 800},
                # Ocultar que es automatizado
                extra_http_headers={
                    "Accept-Language": "es-CR,es;q=0.9,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
            )

            # Ocultar webdriver
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)

            # -------------------------------------------------------
            # PASO 1: Recolectar URLs de todas las secciones
            # -------------------------------------------------------
            article_links = {}  # url → {url, title, section, publication_date}

            sections_to_run = SECTIONS
            if self.test_mode:
                sections_to_run = SECTIONS[:TEST_MAX_SECTIONS]

            for section_url, section_name in sections_to_run:
                self.logger.info(f"Recolectando: {section_name} ({section_url})")
                links = await self.seccion_segura(self._collect_section(context, section_url, section_name), "_collect_section")

                new_count = 0
                for link in links:
                    if link["url"] not in article_links:
                        article_links[link["url"]] = link
                        new_count += 1

                self.logger.info(
                    f"  → {len(links)} encontrados | {new_count} nuevos únicos | "
                    f"Total global: {len(article_links)}"
                )

                # Pausa entre secciones para no saturar
                await asyncio.sleep(DELAY_BETWEEN_SECTIONS)

                if self.test_mode and len(article_links) >= TEST_MAX_ARTICLES * TEST_MAX_SECTIONS:
                    self.logger.debug(
                        f"  Modo prueba: {len(article_links)} links recolectados, "
                        f"deteniendo recolección"
                    )
                    break

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

            # En modo prueba: limitar artículos totales
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

                # Delay variable para no ser detectado como bot
                delay = DELAY_BETWEEN_ARTICLES + random.uniform(0.3, 1.2)
                await asyncio.sleep(delay)

                # Pausa extra cada 20 artículos
                if (i + 1) % 20 == 0:
                    self.logger.info(f"  Pausa de seguridad tras {i+1} artículos...")
                    await asyncio.sleep(5)

            await browser.close()
            return records

    # ------------------------------------------------------------------
    # Recolección con botón "Cargar más"
    # ------------------------------------------------------------------

    async def _collect_section(
        self, context, section_url: str, section_name: str
    ) -> list[dict]:
        """
        Carga una sección completa haciendo clic en "Cargar más" hasta que
        el botón desaparezca o se alcance el límite de clics.
        """
        page = await context.new_page()
        collected = {}
        max_clicks = TEST_MAX_LOAD_MORE if self.test_mode else MAX_LOAD_MORE_CLICKS

        try:
            await page.goto(section_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(5000)

            click_count = 0

            corte = CorteIncremental(self)

            while click_count <= max_clicks:
                if self.tiempo_agotado(0.5):
                    self.logger.warning(f"  [{section_name}] Tiempo del listado agotado, se pasa a los artículos")
                    break
                # Scroll hasta el final para que aparezca el botón
                await self._scroll_to_bottom(page)

                # Extraer artículos visibles en este momento
                n_antes = len(collected)
                new_on_round = await self._extract_cards(page, collected, section_name)
                # Varias páginas/rondas seguidas con solo URLs ya cargadas -> fin de sección
                if corte.pagina([l['url'] for l in list(collected.values())[n_antes:]]):
                    self.logger.info(f"  [{section_name}] Ronda {click_count}: solo URLs ya conocidas, fin de sección")
                    break

                self.logger.debug(
                    f"  [{section_name}] Ronda {click_count}: "
                    f"{new_on_round} nuevos | total: {len(collected)}"
                )

                # En modo prueba, parar cuando hay suficientes artículos
                if self.test_mode and len(collected) >= TEST_MAX_ARTICLES:
                    self.logger.debug(
                        f"  Modo prueba: {len(collected)} artículos en {section_name}, terminando"
                    )
                    break

                # Buscar botón "Cargar más"
                clicked = await self._click_load_more(page)

                if not clicked:
                    self.logger.debug(
                        f"  [{section_name}] Sin botón 'Cargar más', sección completa"
                    )
                    break

                click_count += 1
                # Esperar a que carguen los nuevos artículos
                await page.wait_for_timeout(DELAY_LOAD_MORE)

        except PlaywrightTimeoutError:
            self.logger.warning(f"Timeout en sección: {section_url}")
        except Exception as e:
            self.logger.error(f"Error en {section_url}: {e}", exc_info=True)
        finally:
            await cerrar_pagina(page)

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
        Busca por texto y por clase td_ajax_load_more.
        Retorna True si encontró y clickeó.
        """
        try:
            # Selector principal: clase td_ajax_load_more con texto "Cargar más"
            btn = await page.query_selector(
                "a.td_ajax_load_more, a[class*='td_ajax_load_more']"
            )

            # Fallback por texto
            if not btn:
                anchors = await page.query_selector_all("a")
                for a in anchors:
                    try:
                        text = await a.inner_text()
                        if "cargar más" in text.lower() or "cargar mas" in text.lower():
                            btn = a
                            break
                    except Exception:
                        continue

            if not btn:
                return False

            is_visible = await btn.is_visible()
            if not is_visible:
                return False

            await btn.scroll_into_view_if_needed()
            await page.wait_for_timeout(600)
            await btn.click()
            return True

        except Exception as e:
            self.logger.debug(f"Error buscando 'Cargar más': {e}")
            return False

    async def _extract_cards(
        self, page, collected: dict, section_name: str
    ) -> int:
        """
        Extrae tarjetas de artículos de la página actual.
        Busca h3[class*='entry-title'] sin importar jerarquía.
        """
        found_new = 0

        # Buscar h3 con clase que contenga 'entry-title' o 'td-module-title'
        cards = await page.query_selector_all(
            "h3[class*='entry-title'], h3[class*='td-module-title'], "
            "h2[class*='entry-title']"
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

                # Título: atributo title o texto del anchor
                title = await anchor.get_attribute("title") or ""
                if not title:
                    title = await anchor.inner_text()
                title = clean_text(title)
                if not title:
                    continue

                # Fecha: buscar el time[datetime] más cercano al h3
                # Subir al contenedor padre y buscar td-editor-date
                pub_date = ""
                try:
                    # Buscar en el módulo padre del h3
                    date_time = await page.query_selector(
                        f"h3 a[href='{href}'] ~ * time[datetime], "
                        f"h3 a[href='{href}']"
                    )
                    # Alternativa: buscar por evaluación JS del elemento padre
                    pub_date = await anchor.evaluate("""
                        (el) => {
                            // Subir hasta el contenedor del artículo
                            let container = el.closest(
                                '[class*="td-module"], [class*="td-block"], '
                                + 'article, [class*="post"]'
                            );
                            if (!container) container = el.parentElement?.parentElement?.parentElement;
                            if (!container) return '';
                            const timeEl = container.querySelector('time[datetime]');
                            return timeEl ? timeEl.getAttribute('datetime') : '';
                        }
                    """)
                    if pub_date:
                        pub_date = parse_iso_date(pub_date)
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
        """
        Visita el artículo.
        Texto: div[class*='tdb-block-inner'] → todos los <p>
        Fecha: del listado (ISO); fallback meta tag.
        """
        page = await context.new_page()

        try:
            await page.goto(
                link_data["url"], wait_until="domcontentloaded", timeout=ARTICLE_TIMEOUT
            )
            try:
                await page.wait_for_selector(
                    "div.td-post-content, div.td-fix-index, div.tdb-block-inner, div.entry-content",
                    timeout=10_000
                )
            except Exception:
                pass

            # -----------------------------------------------------------
            # Fecha: mejorar con meta tag si la del listado no tiene hora
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
            # Sección: intentar refinar desde las categorías del artículo
            # -----------------------------------------------------------
            section = link_data.get("section", "")

            # -----------------------------------------------------------
            # Texto: contenedor del cuerpo del artículo (Newspaper/Tagdiv theme)
            # -----------------------------------------------------------
            content_div = await page.query_selector(
                "div.td-post-content, div[class*='td-post-content']"
            )

            # Fallback: div.td-fix-index (wrapper del post en Tagdiv)
            if not content_div:
                content_div = await page.query_selector(
                    "div.td-fix-index, div[class*='td-fix-index']"
                )

            # Fallback: tdb-block-inner (bloque genérico del builder)
            if not content_div:
                content_div = await page.query_selector(
                    "div.tdb-block-inner, div[class*='tdb-block-inner']"
                )

            # Fallback final: entry-content o article
            if not content_div:
                content_div = await page.query_selector(
                    "div.entry-content, article"
                )

            if not content_div:
                titulo_pagina = await page.title()
                self.registrar_pagina_sin_contenido(titulo_pagina)
                self.logger.warning(f"Sin contenedor: {link_data['url']} (título de la página: {titulo_pagina!r})")
                return None

            full_text = await content_div.evaluate("""
                (el) => {
                    const clone = el.cloneNode(true);

                    const remove_sels = [
                        // Publicidad
                        '.td-a-ad', '[class*="td-a-ad"]',
                        '[id*="_ad"]', '[class*="ad-slot"]',
                        '[class*="dfp"]', '[class*="googletag"]',
                        // Facebook comments
                        '.fb-comments', '#fb-root', '.fb_iframe_widget',
                        '[class*="heateor"]', '[class*="heateorFfc"]',
                        // Redes sociales
                        '[class*="share"]', '[class*="social"]',
                        // Artículos relacionados
                        '[class*="related"]', '[class*="td-related"]',
                        // Navegación y widgets
                        'nav', '.navigation', '[class*="td-next"]',
                        '[class*="td-prev"]',
                        // Scripts y estilos
                        'script', 'style', 'iframe',
                        'figure', 'figcaption',
                        // Comentarios
                        '#comments', '.comments-area',
                        // Tags y meta
                        '[class*="td-tags"]', '[class*="td-post-source"]',
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
                        if (text.length >= 15) parts.push(text);
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
# CLI: permite --test para modo prueba
# ------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scraper NCR Noticias")
    parser.add_argument(
        "--test", action="store_true",
        help=(
            f"Modo prueba: solo {TEST_MAX_SECTIONS} secciones, "
            f"{TEST_MAX_LOAD_MORE} clics de 'Cargar más', "
            f"{TEST_MAX_ARTICLES} artículos por sección"
        )
    )
    parser.add_argument("--output", default="output")
    parser.add_argument("--logs", default="logs")
    args = parser.parse_args()

    scraper = NCRNoticiasScraper(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
