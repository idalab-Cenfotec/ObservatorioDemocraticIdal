"""
latejacr.py
Scraper para La Teja CR (https://www.lateja.cr/)

Secciones scrapeadas:
  - Lo más leído    : https://www.lateja.cr/lo-mas-leido/
  - Últimas noticias: https://www.lateja.cr/ultimas-noticias/
  - Deportes        : https://www.lateja.cr/deportes/
  - Farándula       : https://www.lateja.cr/farandula/
  - Sucesos         : https://www.lateja.cr/sucesos/
  - Nacional        : https://www.lateja.cr/nacional/        ← prioritaria
  - Internacional   : https://www.lateja.cr/internacionales/ ← prioritaria

Estructura del listado (todas las secciones usan scroll infinito):
  - Contenedor "lo más leído" : div.most-read-container > div.list
    - Cada ítem               : div.row-border
    - Enlace y título         : a.title (href) > h4 > span.title
  - Contenedor otras secciones: scroll infinito genérico
    - Cada ítem puede variar, buscar a[href*='/story/'] con texto

Estructura del artículo:
  - Fecha  : time[class*='c-date'][datetime]  → atributo datetime ISO 8601
  - Texto  : article[class*='ArticleBody'] o article[class*='article-body']
             → extraer todo el texto de p, h1, h2, h3, li, blockquote
             → ignorar divs de publicidad, widgets, redes sociales
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

BASE_URL = "https://www.lateja.cr/"

# Secciones: (url, nombre_seccion, tipo)
# tipo: "mostread" para lo-mas-leido, "scroll" para el resto
SECTIONS = [
    ("https://www.lateja.cr/lo-mas-leido/",      "Lo más leído",     "mostread"),
    ("https://www.lateja.cr/ultimas-noticias/",  "Últimas noticias", "scroll"),
    ("https://www.lateja.cr/nacional/",          "Nacional",         "scroll"),
    ("https://www.lateja.cr/internacionales/",   "Internacional",    "scroll"),
    ("https://www.lateja.cr/sucesos/",           "Sucesos",          "scroll"),
    ("https://www.lateja.cr/deportes/",          "Deportes",         "scroll"),
    ("https://www.lateja.cr/farandula/",         "Farándula",        "scroll"),
]

# Número de scrolls por sección (cada scroll carga ~10-20 artículos)
MAX_SCROLLS = 20

# En test mode, limitar scrolls a pocas iteraciones
TEST_MAX_SCROLLS = 2

ARTICLE_TIMEOUT = 25_000
DELAY_BETWEEN_ARTICLES = 1.2
DELAY_SCROLL = 2500  # ms entre scrolls

# Modo prueba: limita número de artículos procesados
TEST_MAX_ARTICLES = 5


def parse_iso_date(raw: str) -> str:
    """
    Parsea fecha ISO 8601 como '2026-05-15T17:38:26.012Z'
    Retorna 'YYYY-MM-DD HH:MM:SS' en UTC-6.
    """
    try:
        raw = raw.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        dt_cr = dt.astimezone(CR_TZ)
        return dt_cr.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return raw[:10] if raw else ""


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_article_url(url: str) -> bool:
    """Filtra solo URLs de artículos de La Teja (terminan en /story/)."""
    if not url:
        return False
    if not url.startswith("https://www.lateja.cr/"):
        return False
    # Artículos de La Teja tienen formato /seccion/slug/ID/story/
    return "/story/" in url


class LaTejaCRScraper(BaseScraper):
    """
    Scraper para La Teja CR.
    Recorre 7 secciones con scroll infinito en el listado.
    Visita cada artículo y extrae el texto del article body.
    """

    SOURCE_NAME = "latejacr"
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
            article_links = {}  # url -> {url, title, section}

            for section_url, section_name, section_type in SECTIONS:
                self.logger.info(f"Recolectando: {section_name} ({section_url})")

                if section_type == "mostread":
                    links = await self._collect_mostread(context, section_url, section_name)
                else:
                    links = await self._collect_scroll(context, section_url, section_name)

                new_count = 0
                for link in links:
                    if link["url"] not in article_links:
                        article_links[link["url"]] = link
                        new_count += 1

                self.logger.info(
                    f"  → {len(links)} encontrados | {new_count} nuevos | "
                    f"Total acumulado: {len(article_links)}"
                )

                if self.test_mode and len(article_links) >= TEST_MAX_ARTICLES:
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
    # Recolección: sección "Lo más leído"
    # ------------------------------------------------------------------

    async def _collect_mostread(
        self, context, section_url: str, section_name: str
    ) -> list[dict]:
        """
        Recolecta desde div.most-read-container > div.list > div.row-border
        Con scroll para cargar más items.
        En test mode, limita el número de scrolls a TEST_MAX_SCROLLS.
        """
        page = await context.new_page()
        collected = {}
        
        # Usar TEST_MAX_SCROLLS en test mode
        max_scrolls = TEST_MAX_SCROLLS if self.test_mode else MAX_SCROLLS

        try:
            await page.goto(section_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2000)

            for scroll_num in range(max_scrolls):
                # Extraer items del contenedor most-read
                rows = await page.query_selector_all(
                    "div.most-read-container div.list div.row-border, "
                    "div[class*='most-read'] div[data-testid='recommend-row']"
                )

                for row in rows:
                    try:
                        # Enlace: a.title con href
                        link_el = await row.query_selector("a.title, a[class*='title']")
                        if not link_el:
                            continue

                        href = await link_el.get_attribute("href")
                        if not href or not is_article_url(href):
                            continue
                        if href in collected:
                            continue

                        # Título: span.title dentro del a
                        title = ""
                        span_el = await link_el.query_selector("span.title, span[class*='title']")
                        if span_el:
                            title = await span_el.inner_text()
                        else:
                            # Fallback: texto del h4
                            h4 = await link_el.query_selector("h4")
                            if h4:
                                title = await h4.inner_text()
                                # Limpiar el número (ej: "1. ")
                                title = re.sub(r"^\d+\.\s*", "", title).strip()

                        if not title:
                            title = await link_el.inner_text()
                            title = re.sub(r"^\d+\.\s*", "", title).strip()

                        collected[href] = {
                            "url": href.strip(),
                            "title": clean_text(title),
                            "section": section_name,
                            "publication_date": "",  # se extrae al visitar el artículo
                        }

                    except Exception as e:
                        self.logger.debug(f"Error en row-border: {e}")
                        continue

                if self.test_mode and len(collected) >= TEST_MAX_ARTICLES:
                    self.logger.debug(
                        f"  Modo prueba: {len(collected)} artículos en mostread, terminando"
                    )
                    break

                prev_count = len(collected)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(DELAY_SCROLL)

                # Si no cargaron nuevos items, terminar
                rows_after = await page.query_selector_all(
                    "div.most-read-container div.list div.row-border, "
                    "div[class*='most-read'] div[data-testid='recommend-row']"
                )
                if len(rows_after) <= len(rows) and scroll_num > 2:
                    self.logger.debug("Sin nuevos items en scroll de mostread, terminando")
                    break

                self.logger.debug(
                    f"  Scroll {scroll_num+1}: {len(collected)} artículos acumulados"
                )

        except Exception as e:
            self.logger.error(f"Error en mostread {section_url}: {e}", exc_info=True)
        finally:
            await page.close()

        return list(collected.values())

    # ------------------------------------------------------------------
    # Recolección: secciones con scroll infinito genérico
    # ------------------------------------------------------------------

    async def _collect_scroll(
        self, context, section_url: str, section_name: str
    ) -> list[dict]:
        """
        Recolecta desde secciones con scroll infinito + botón "Ver más".
        En test mode, limita el número de scrolls a TEST_MAX_SCROLLS.
        """
        page = await context.new_page()
        collected = {}
        
        # Usar TEST_MAX_SCROLLS en test mode para ir más rápido
        max_scrolls = TEST_MAX_SCROLLS if self.test_mode else MAX_SCROLLS

        try:
            await page.goto(section_url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2500)

            corte = CorteIncremental(self)
            for scroll_num in range(max_scrolls):
                if self.tiempo_agotado(0.5):
                    self.logger.warning(f"  [{section_name}] Tiempo del listado agotado, se pasa a los artículos")
                    break
                # --- Extraer artículos actualmente visibles ---
                n_antes = len(collected)
                links_data = await self._extract_story_links(page)
                for item in links_data:
                    url = item.get("url", "")
                    if url and is_article_url(url) and url not in collected:
                        collected[url] = {
                            "url": url,
                            "title": clean_text(item.get("title", "")),
                            "section": section_name,
                            "publication_date": "",
                        }

                # Varios scrolls seguidos con solo URLs ya cargadas -> fin de sección
                if corte.pagina([l["url"] for l in list(collected.values())[n_antes:]]):
                    self.logger.info(f"  [{section_name}] Scroll {scroll_num + 1}: solo URLs ya conocidas, fin de sección")
                    break

                if self.test_mode and len(collected) >= TEST_MAX_ARTICLES:
                    self.logger.debug(
                        f"  Modo prueba: {len(collected)} artículos en {section_name}, terminando"
                    )
                    break

                prev_count = len(collected)

                # --- Scroll al final ---
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(DELAY_SCROLL)

                # --- Buscar botón "Ver más" (por texto, sin depender de clase exacta) ---
                ver_mas_clicked = await self._click_ver_mas(page)

                if ver_mas_clicked:
                    # Esperar a que carguen los nuevos artículos
                    await page.wait_for_timeout(DELAY_SCROLL + 500)
                    self.logger.debug(
                        f"  [{section_name}] Scroll {scroll_num+1}: "
                        f"'Ver más' pulsado, esperando carga..."
                    )
                else:
                    # Sin botón: verificar si hay artículos nuevos tras el scroll
                    links_after = await self._extract_story_links(page)
                    new_after_scroll = sum(
                        1 for item in links_after
                        if item.get("url") and item["url"] not in collected
                    )

                    self.logger.debug(
                        f"  [{section_name}] Scroll {scroll_num+1}: "
                        f"{len(collected)} acumulados | nuevos tras scroll: {new_after_scroll}"
                    )

                    # Terminar si no hay botón y no llegaron nuevos artículos
                    if new_after_scroll == 0 and scroll_num > 2:
                        self.logger.debug(
                            f"  Sin botón 'Ver más' ni nuevos artículos en "
                            f"{section_name}, terminando"
                        )
                        break

        except Exception as e:
            self.logger.error(f"Error en scroll {section_url}: {e}", exc_info=True)
        finally:
            await page.close()

        return list(collected.values())

    async def _extract_story_links(self, page) -> list[dict]:
        """Extrae todos los enlaces /story/ visibles en la página actual."""
        return await page.evaluate("""
            () => {
                const results = [];
                const anchors = document.querySelectorAll('a[href*="/story/"]');

                anchors.forEach(a => {
                    const href = a.getAttribute('href') || '';
                    if (!href.includes('/story/')) return;

                    let title = '';
                    const titleEl = a.querySelector(
                        'h2, h3, h4, [class*="title"], [class*="headline"]'
                    );
                    if (titleEl) {
                        title = (titleEl.innerText || titleEl.textContent || '').trim();
                    }
                    if (!title) {
                        title = (a.innerText || a.textContent || '').trim();
                    }
                    // Limpiar número de lista (ej: "1. Título")
                    title = title.replace(/^\\d+\\.\\s*/, '').trim();
                    if (title.length < 10) return;

                    let url = href;
                    if (url.startsWith('/')) {
                        url = 'https://www.lateja.cr' + url;
                    }
                    results.push({ url, title });
                });

                // Deduplicar por URL
                const seen = new Set();
                return results.filter(r => {
                    if (seen.has(r.url)) return false;
                    seen.add(r.url);
                    return true;
                });
            }
        """)

    async def _click_ver_mas(self, page) -> bool:
        """
        Busca y hace clic en el botón 'Ver más'.
        Busca por texto visible (no por clase exacta) para mayor robustez.
        Retorna True si encontró y clickeó el botón, False si no existe.
        """
        try:
            # Buscar button cuyo texto contenga 'Ver más' o 'Ver mas'
            # sin importar jerarquía ni clase exacta
            btn = await page.query_selector(
                "button[aria-label*='Ver más'], "
                "button[aria-label*='Ver mas']"
            )

            # Fallback: buscar por texto interno del botón
            if not btn:
                buttons = await page.query_selector_all("button")
                for b in buttons:
                    try:
                        text = await b.inner_text()
                        if "ver más" in text.lower() or "ver mas" in text.lower():
                            btn = b
                            break
                    except Exception:
                        continue

            if not btn:
                return False

            # Verificar que el botón esté visible
            is_visible = await btn.is_visible()
            if not is_visible:
                return False

            # Scroll al botón y hacer clic
            await btn.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
            await btn.click()
            return True

        except Exception as e:
            self.logger.debug(f"Error buscando botón 'Ver más': {e}")
            return False

    # ------------------------------------------------------------------
    # Scraping del artículo individual
    # ------------------------------------------------------------------

    async def _scrape_article(self, context, link_data: dict) -> dict | None:
        """
        Visita el artículo y extrae:
        - Fecha: time[class*='c-date'][datetime] → atributo datetime ISO 8601
        - Texto: article[class*='ArticleBody'] o article[class*='article-body']
                 → todo el texto de p, h1-h4, li, blockquote
        """
        page = await context.new_page()

        try:
            await page.goto(
                link_data["url"], wait_until="domcontentloaded", timeout=ARTICLE_TIMEOUT
            )
            await page.wait_for_timeout(1500)

            # -----------------------------------------------------------
            # Fecha: buscar time con clase c-date o b-date (sin jerarquía)
            # -----------------------------------------------------------
            publication_date = link_data.get("publication_date", "")

            time_el = await page.query_selector(
                "time[class*='c-date'], time[class*='b-date'], time[datetime]"
            )
            if time_el:
                raw_dt = await time_el.get_attribute("datetime")
                if raw_dt:
                    publication_date = parse_iso_date(raw_dt)

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
            # Sección: intentar inferir desde la URL si no está definida
            # Formato URL: /seccion/slug/ID/story/
            # -----------------------------------------------------------
            section = link_data.get("section", "")
            if not section:
                parts = link_data["url"].replace(BASE_URL, "").split("/")
                if parts:
                    section = parts[0].capitalize()

            # -----------------------------------------------------------
            # Título: intentar refinarlo desde el artículo (h1 del artículo)
            # -----------------------------------------------------------
            title = link_data.get("title", "")
            if not title or len(title) < 5:
                h1 = await page.query_selector("h1")
                if h1:
                    title = clean_text(await h1.inner_text())

            # -----------------------------------------------------------
            # Texto: buscar article con clase que contenga 'ArticleBody'
            # o 'article-body' (sin importar jerarquía ni nombre exacto)
            # -----------------------------------------------------------
            content_article = await page.query_selector(
                "article[class*='ArticleBody'], "
                "article[class*='article-body'], "
                "article[class*='article-body-wrapper']"
            )

            # Fallback: div con clase ArticleBody
            if not content_article:
                content_article = await page.query_selector(
                    "div[class*='ArticleBody'], div[class*='article-body']"
                )

            # Fallback final: primer article de la página
            if not content_article:
                content_article = await page.query_selector("article")

            if not content_article:
                self.logger.warning(f"Sin article body: {link_data['url']}")
                return None

            # -----------------------------------------------------------
            # Extraer texto con JavaScript: p, h1-h4, li, blockquote
            # Excluir ads, widgets, redes sociales, related posts
            # -----------------------------------------------------------
            full_text = await content_article.evaluate("""
                (el) => {
                    const clone = el.cloneNode(true);

                    // Selectores de elementos a eliminar
                    const remove_sels = [
                        // Publicidad
                        '[id*="gnad"]', '[class*="AdUnit"]', '[class*="dfp-container"]',
                        '[class*="ad-"]', '[data-test-id*="adslot"]',
                        // Widgets externos
                        '[class*="mgid"]', '[data-type="_mgwidget"]',
                        '[id*="mgid-slot"]', '[id*="piano-inline"]',
                        '[id*="fusion-static"]',
                        // Redes sociales y sharing
                        '[class*="sharedaddy"]', '[class*="post-share"]',
                        // Artículos relacionados
                        '[id*="jp-relatedposts"]', '[class*="related"]',
                        // Navegación
                        'nav', '[class*="navigation"]',
                        // Scripts y estilos
                        'script', 'style', 'iframe',
                        // Figuras (imágenes — solo queremos texto)
                        'figure', 'figcaption',
                        // Interstitial links (LEA MÁS)
                        '[class*="interstitial"]',
                    ];

                    remove_sels.forEach(sel => {
                        try {
                            clone.querySelectorAll(sel).forEach(e => e.remove());
                        } catch(err) {}
                    });

                    // Extraer texto de elementos relevantes en orden
                    const parts = [];
                    const elements = clone.querySelectorAll(
                        'h1, h2, h3, h4, p, li, blockquote'
                    );

                    elements.forEach(el => {
                        const text = (el.innerText || el.textContent || '')
                            .replace(/\\s+/g, ' ').trim();
                        if (text.length > 15) {
                            parts.push(text);
                        }
                    });

                    return parts.join('\\n\\n');
                }
            """)

            full_text = clean_text(full_text) if full_text else ""

            if not full_text:
                self.logger.warning(f"Sin texto extraído: {link_data['url']}")
                return None

            return {
                "url": link_data["url"],
                "title": title,
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


# Ejecución directa para pruebas
if __name__ == "__main__":
    scraper = LaTejaCRScraper()
    summary = scraper.run()
    print(summary)
