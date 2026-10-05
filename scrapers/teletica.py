"""
teletica.py
Scraper para Teletica (https://www.teletica.com/)

Secciones (8):
  - Sucesos       : https://www.teletica.com/noticias/nacional/sucesos
  - Política      : https://www.teletica.com/noticias/nacional/politica
  - Entrevistas   : https://www.teletica.com/noticias/nacional/entrevistas
  - En Profundidad: https://www.teletica.com/noticias/en-profundidad
  - Reportajes    : https://www.teletica.com/noticias/nacional/reportajes
  - Ambiente      : https://www.teletica.com/noticias/nacional/ambiente
  - Ciencia       : https://www.teletica.com/noticias/internacional/ciencia
  - Tecnología    : https://www.teletica.com/noticias/internacional/tecnologia

Sin paginación numérica — botón "Cargar más noticias":
  <a class="btn-view-more get-more-search-keywords">Cargar más noticias</a>

Estructura listado:
  - Contenedor : div.text que contiene a.nota-link en el mismo bloque padre
  - Título      : div.text h2
  - URL         : a.nota-link[href]  (absoluta o relativa)

Estructura artículo:
  - Fecha : div.authorDate > span  ("25 de mayo de 2026, 18:50 PM")
  - Texto : div.text-editor → p > span[style*='pre-wrap'] o p directo
            (excluye "Lea también", ads, imágenes, WhatsApp banners)

Deduplicación global entre secciones.
Modo prueba: --test limita secciones, clics y artículos.
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

# Descartar artículos anteriores a esta fecha
DATE_CUTOFF = datetime(2024, 1, 1, tzinfo=CR_TZ)

BASE_URL = "https://www.teletica.com"

SECTIONS = [
    ("https://www.teletica.com/noticias/nacional/sucesos",            "Sucesos"),
    ("https://www.teletica.com/noticias/nacional/politica",           "Política"),
    ("https://www.teletica.com/noticias/nacional/entrevistas",        "Entrevistas"),
    ("https://www.teletica.com/noticias/en-profundidad",              "En Profundidad"),
    ("https://www.teletica.com/noticias/nacional/reportajes",         "Reportajes"),
    ("https://www.teletica.com/noticias/nacional/ambiente",           "Ambiente"),
    ("https://www.teletica.com/noticias/internacional/ciencia",       "Ciencia"),
    ("https://www.teletica.com/noticias/internacional/tecnologia",    "Tecnología"),
]

ARTICLE_TIMEOUT = 25_000
DELAY_BETWEEN_ARTICLES = 1.2
DELAY_BETWEEN_SECTIONS = 3.0
DELAY_LOAD_MORE = 3000   # ms tras clic "Cargar más"
DELAY_SCROLL = 2000

# Modo prueba
TEST_MAX_SECTIONS = 2
TEST_MAX_LOAD_MORE = 3
TEST_MAX_ARTICLES = 8

MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def parse_teletica_date(text: str) -> str:
    """
    Parsea '25 de mayo de 2026, 18:50 PM' → '2026-05-25 18:50:00'
    También maneja '25 de mayo de 2026' sin hora.
    """
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)

    # Con hora: "DD de mes de YYYY, HH:MM"
    match = re.search(
        r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4}),?\s+(\d{1,2}):(\d{2})",
        text
    )
    if match:
        day   = int(match.group(1))
        month = MESES_ES.get(match.group(2), 0)
        year  = int(match.group(3))
        hour  = int(match.group(4))
        minute= int(match.group(5))
        if month:
            return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:00"

    # Sin hora
    match2 = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", text)
    if match2:
        day   = int(match2.group(1))
        month = MESES_ES.get(match2.group(2), 0)
        year  = int(match2.group(3))
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
    href = href.strip()
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE_URL + href
    return BASE_URL + "/" + href


def is_article_url(url: str) -> bool:
    if not url or not url.startswith("https://www.teletica.com/"):
        return False
    exclude = ["/noticias/", "/autor/", "/tag/", "/buscar",
               "/?", "/#", "/wp-", "/feed", "/videos/",
               "/programas/", "/series/", "/deportes/"]
    for pat in exclude:
        if pat in url:
            return False
    path = url.replace("https://www.teletica.com/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    # Artículos tienen formato /seccion/slug_ID  (al menos 2 segmentos)
    return len(parts) >= 2


class TeleticaScraper(BaseScraper):
    """
    Scraper para Teletica CR.
    - 8 secciones con botón 'Cargar más noticias'
    - Scroll + clic hasta agotar contenido
    - Deduplicación global entre secciones
    - Modo prueba: limita secciones, clics y artículos
    """

    SOURCE_NAME = "teletica"
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
                await asyncio.sleep(DELAY_BETWEEN_ARTICLES + random.uniform(0.2, 0.8))

                # Pausa cada 25 artículos
                if (i + 1) % 25 == 0:
                    self.logger.info(f"  Pausa tras {i+1} artículos...")
                    await asyncio.sleep(5)

            await browser.close()
            return records

    # ------------------------------------------------------------------
    # Recolección con botón "Cargar más noticias"
    # ------------------------------------------------------------------

    async def _collect_section(
        self, context, section_url: str, section_name: str
    ) -> list[dict]:
        """
        Carga la sección completa haciendo scroll + clic en
        'Cargar más noticias' hasta que el botón desaparezca.
        """
        page = await context.new_page()
        collected = {}
        max_clicks = TEST_MAX_LOAD_MORE if self.test_mode else 200

        try:
            await page.goto(section_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2500)

            click_count = 0

            corte = CorteIncremental(self)

            while click_count <= max_clicks:
                if self.tiempo_agotado(0.5):
                    self.logger.warning(f"  [{section_name}] Tiempo del listado agotado, se pasa a los artículos")
                    break
                # Scroll al final
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

                # Intentar clic en "Cargar más noticias"
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
            await cerrar_pagina(page)

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

    async def _click_load_more(self, page) -> bool:
        """
        Busca y hace clic en 'Cargar más noticias'.
        Selector: a.btn-view-more.get-more-search-keywords
        Fallback: texto del elemento.
        """
        try:
            btn = await page.query_selector(
                "a.btn-view-more.get-more-search-keywords, "
                "a[class*='btn-view-more'][class*='get-more']"
            )

            # Fallback por texto
            if not btn:
                anchors = await page.query_selector_all("a.btn-view-more, a[class*='view-more']")
                for a in anchors:
                    try:
                        text = (await a.inner_text()).strip().lower()
                        if "cargar" in text and "notic" in text:
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
            await page.wait_for_timeout(600)
            await btn.click()
            return True

        except Exception as e:
            self.logger.debug(f"Error en 'Cargar más': {e}")
            return False

    async def _extract_cards(
        self, page, collected: dict, section_name: str
    ) -> int:
        """
        Extrae artículos buscando div.text que tengan un a.nota-link
        en el mismo bloque contenedor.

        Estrategia JS:
          1. Buscar todos los a.nota-link (tienen el href del artículo)
          2. Desde cada uno, subir al contenedor padre y buscar div.text > h2
          3. Extraer también la fecha desde div.text h6 span.date si existe
        """
        found_new = 0

        items = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // Buscar todos los enlaces de artículo
                document.querySelectorAll('a.nota-link[href]').forEach(notaLink => {
                    const href = notaLink.getAttribute('href') || '';
                    if (!href || seen.has(href)) return;

                    // Subir al contenedor del bloque de la noticia
                    const container = notaLink.closest(
                        '.nota, .component, .block, [class*="nota"], '
                        + 'article, [class*="component"]'
                    ) || notaLink.parentElement;

                    if (!container) return;

                    // Título: buscar h2 en el contenedor (con o sin div.text intermedio)
                    let title = '';
                    const h2El = container.querySelector('h2');
                    if (h2El) title = (h2El.innerText || '').replace(/\\s+/g, ' ').trim();

                    // Fallback: innerText de la nota-link (contiene el título)
                    if (!title) {
                        title = (notaLink.innerText || notaLink.getAttribute('title') || '').replace(/\\s+/g, ' ').trim();
                    }

                    if (!title || title.length < 5) return;
                    seen.add(href);

                    results.push({ href, title });
                });

                return results;
            }
        """)

        for item in items:
            href = item.get("href", "").strip()
            title = clean_text(item.get("title", ""))

            if not href or not title:
                continue

            url = make_absolute(href)

            if not is_article_url(url):
                continue
            if url in collected:
                continue

            collected[url] = {
                "url": url,
                "title": title,
                "section": section_name,
                "publication_date": "",  # se extrae al visitar el artículo
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
            await page.wait_for_timeout(1800)

            # -----------------------------------------------------------
            # Fecha: div.authorDate > span
            # "25 de mayo de 2026, 18:50 PM"
            # -----------------------------------------------------------
            publication_date = ""

            author_div = await page.query_selector(
                "div.authorDate, div[class*='authorDate']"
            )
            if author_div:
                # El span con la fecha es el último span (después del autor)
                spans = await author_div.query_selector_all("span")
                for span in spans:
                    text = (await span.inner_text()).strip()
                    # Filtrar spans vacíos y de autores
                    if text and re.search(r"\d{1,2}\s+de\s+\w+\s+de\s+\d{4}", text.lower()):
                        parsed = parse_teletica_date(text)
                        if parsed:
                            publication_date = parsed
                            break

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
            # Sección: URL del artículo ya tiene la sección incorporada
            # Intentar refinar desde h4 del artículo si está disponible
            # -----------------------------------------------------------
            section = link_data.get("section", "")
            try:
                # El artículo puede tener h4 con la sección en el header
                h4_el = await page.query_selector(
                    "div.text-editor h4, div[class*='authorDate'] ~ * h4"
                )
                if h4_el:
                    h4_text = clean_text(await h4_el.inner_text())
                    if h4_text and len(h4_text) < 50:
                        section = h4_text
            except Exception:
                pass

            # -----------------------------------------------------------
            # Texto: div.text-editor → p > span o p directo
            # Excluir: "Lea también", ads, imágenes, WhatsApp banners
            # -----------------------------------------------------------
            content_div = await page.query_selector(
                "div.text-editor, div[class*='text-editor']"
            )

            if not content_div:
                # Fallback: buscar el contenedor principal del artículo
                content_div = await page.query_selector(
                    "[class*='article-body'], [class*='article-content'], "
                    "article main, main article"
                )

            if not content_div:
                self.logger.warning(f"Sin contenedor: {link_data['url']}")
                return None

            full_text = await content_div.evaluate("""
                (el) => {
                    const clone = el.cloneNode(true);

                    const remove_sels = [
                        // Publicidad / banners
                        '.banner-holder', '[class*="banner-holder"]',
                        '[class*="ndg_inject"]',
                        '[id*="gpt_unit"]', '[id*="google_ads"]',
                        // "Lea también" / notas relacionadas
                        '.related', '#relatedNews',
                        '[class*="related"]',
                        // Imágenes y figuras
                        'figure', 'figcaption',
                        '.image', '[class*="nota-image"]',
                        // Bloques de autor/fecha (ya los tenemos)
                        '.top', '[class*="authorDate"]',
                        // WhatsApp y redes sociales
                        'a[href*="whatsapp"]',
                        'img[alt*="WhatsApp"]',
                        '[class*="social"]',
                        // Scripts, estilos, iframes
                        'script', 'style', 'iframe',
                        // Notas embebidas
                        '.component', '[class*="component--"]',
                    ];

                    remove_sels.forEach(sel => {
                        try {
                            clone.querySelectorAll(sel).forEach(e => e.remove());
                        } catch(err) {}
                    });

                    const parts = [];
                    const seen = new Set();

                    // Extraer texto de <p> — Teletica usa <p><span style="...">texto</span></p>
                    // o <p> directos. Ambos casos cubiertos con innerText del <p>
                    clone.querySelectorAll('p').forEach(p => {
                        // Ignorar párrafos vacíos o con solo imágenes
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

            # Filtro temporal: descartar artículos anteriores a 2024-01-01
            if publication_date:
                try:
                    pub_dt = datetime.fromisoformat(publication_date.replace(" ", "T"))
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=CR_TZ)
                    if pub_dt < DATE_CUTOFF:
                        self.logger.debug(
                            f"Artículo anterior a 2024, descartando: {link_data['url']} ({publication_date})"
                        )
                        return None
                except Exception:
                    pass

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

    parser = argparse.ArgumentParser(description="Scraper Teletica CR")
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

    scraper = TeleticaScraper(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
