"""
vozdeguanacaste.py
Scraper para Voz de Guanacaste (https://vozdeguanacaste.com/)

Secciones (7):
  - Noticias        : https://vozdeguanacaste.com/category/noticias/
  - Cultura         : https://vozdeguanacaste.com/category/cultura/
  - Gentrificación  : https://vozdeguanacaste.com/gentrificacion/
  - Especiales      : https://vozdeguanacaste.com/category/especiales/
  - Ambiente        : https://vozdeguanacaste.com/category/naturaleza/
  - Derechos Humanos: https://vozdeguanacaste.com/category/derechos-humanos/
  - Publirreportaje : https://vozdeguanacaste.com/category/publirreportaje/

Paginación: /page/2/, /page/3/ (WordPress estándar)

Pop-up: puede aparecer ocasionalmente al cambiar de página.
Estrategia: al detectarlo, hacer clic fuera del overlay para cerrarlo.

Estructura listado:
  - Título y URL: h3.entry-title > a  (title + href)
  - Fecha       : span.published > abbr  (texto: "Jul 17, 2025")

Estructura artículo:
  - Texto : div.entry-content → todos los <p> y <h2> <h3> <h4>
            (excluye sharing, WPML, figuras, comentarios, nav)
  - Fecha : del listado; fallback meta tag

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

BASE_URL = "https://vozdeguanacaste.com"

SECTIONS = [
    ("https://vozdeguanacaste.com/category/noticias/",       "Noticias"),
    ("https://vozdeguanacaste.com/category/cultura/",        "Cultura"),
    ("https://vozdeguanacaste.com/gentrificacion/",          "Gentrificación"),
    ("https://vozdeguanacaste.com/category/especiales/",     "Especiales"),
    ("https://vozdeguanacaste.com/category/naturaleza/",     "Ambiente"),
    ("https://vozdeguanacaste.com/category/derechos-humanos/", "Derechos Humanos"),
    ("https://vozdeguanacaste.com/category/publirreportaje/", "Publirreportaje"),
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

MESES_ABR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    # Español abreviado
    "ene": 1, "abr": 4, "ago": 8,
}


def parse_vdg_date(text: str) -> str:
    """
    Parsea 'Jul 17, 2025' o 'Abr 17, 2026' → '2025-07-17'
    También intenta ISO parcial como fallback.
    """
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)

    # "Mes DD, YYYY" (inglés/español abreviado)
    match = re.search(r"(\w{3})\s+(\d{1,2}),\s*(\d{4})", text)
    if match:
        month = MESES_ABR.get(match.group(1), 0)
        day   = int(match.group(2))
        year  = int(match.group(3))
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # ISO parcial "YYYY-MM-DD"
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


def is_article_url(url: str) -> bool:
    if not url or not url.startswith("https://vozdeguanacaste.com/"):
        return False
    exclude = ["/category/", "/author/", "/tag/", "/page/",
               "/?", "/#", "/wp-", "/feed", "/en/"]
    for pat in exclude:
        if pat in url:
            return False
    # Excluir URLs base de sección
    for sec_url, _ in SECTIONS:
        if url.rstrip("/") + "/" == sec_url or url == sec_url.rstrip("/"):
            return False
    path = url.replace("https://vozdeguanacaste.com/", "").strip("/")
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 1 and not parts[0].startswith("page")


class VozDeGuanacaste(BaseScraper):
    """
    Scraper para Voz de Guanacaste.
    - 7 secciones con paginación /page/N/
    - Manejo de pop-up: clic fuera del overlay si aparece
    - Deduplicación global entre secciones
    - Modo prueba limita secciones, páginas y artículos
    """

    SOURCE_NAME = "vozdeguanacaste"
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
    # Manejo de pop-up
    # ------------------------------------------------------------------

    async def _dismiss_popup(self, page) -> None:
        """
        Detecta y cierra pop-ups comunes.
        Estrategia: clic en el overlay/backdrop fuera del modal,
        o en botones de cierre si existen.
        """
        try:
            # Intentar botones de cierre explícitos
            close_selectors = [
                "button[class*='close']", "[class*='modal-close']",
                "[class*='popup-close']", "[class*='overlay-close']",
                "[aria-label*='close']", "[aria-label*='cerrar']",
                "button[class*='dismiss']", "[class*='newsletter'] button",
                "[id*='popup'] button", "[class*='popup'] button[class*='close']",
            ]
            for sel in close_selectors:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click(timeout=2000)
                        await page.wait_for_timeout(500)
                        self.logger.debug("  Pop-up cerrado con botón")
                        return
                except Exception:
                    continue

            # Clic en el overlay/backdrop para cerrar
            overlay_selectors = [
                "[class*='overlay']", "[class*='backdrop']",
                "[class*='modal-overlay']", "[class*='popup-overlay']",
                "[id*='overlay']",
            ]
            for sel in overlay_selectors:
                try:
                    overlay = await page.query_selector(sel)
                    if overlay and await overlay.is_visible():
                        # Clic en una esquina del overlay (fuera del modal)
                        await page.mouse.click(10, 10)
                        await page.wait_for_timeout(500)
                        self.logger.debug("  Pop-up cerrado con clic fuera")
                        return
                except Exception:
                    continue

            # Presionar Escape como último recurso
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)

        except Exception as e:
            self.logger.debug(f"  No se pudo cerrar pop-up: {e}")

    async def _handle_page_popups(self, page) -> None:
        """
        Verifica si hay un pop-up activo y lo cierra.
        """
        try:
            popup_visible = await page.evaluate("""
                () => {
                    const selectors = [
                        '[class*="overlay"]', '[class*="backdrop"]',
                        '[class*="modal"]', '[class*="popup"]',
                        '[id*="popup"]', '[id*="modal"]'
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el) {
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            if (style.display !== 'none' &&
                                style.visibility !== 'hidden' &&
                                style.opacity !== '0' &&
                                rect.width > 100 && rect.height > 100) {
                                return true;
                            }
                        }
                    }
                    return false;
                }
            """)
            if popup_visible:
                await self._dismiss_popup(page)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Recolección paginada
    # ------------------------------------------------------------------

    async def _collect_section(
        self, context, base_url: str, section_name: str
    ) -> list[dict]:
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

                await page.wait_for_timeout(1800)

                # Cerrar pop-up si apareció
                await self._handle_page_popups(page)

                await self._scroll_to_bottom(page)

                n_antes = len(collected)
                found_on_page = await self._extract_cards(page, collected, section_name)
                # Varias páginas seguidas con solo URLs ya cargadas -> fin de sección
                if corte.pagina([l['url'] for l in list(collected.values())[n_antes:]]):
                    self.logger.info(f"  [{section_name}] Pág {page_num}: solo URLs ya conocidas, fin de sección")
                    break

                self.logger.debug(
                    f"  [{section_name}] Pág {page_num}: "
                    f"{found_on_page} nuevos | total: {len(collected)}"
                )

                if found_on_page == 0:
                    all_titles = await page.query_selector_all(
                        "h3.entry-title, h3[class*='entry-title']"
                    )
                    if len(all_titles) == 0:
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
        Extrae artículos desde h3.entry-title > a.
        Fecha: span.published > abbr (texto: "May 26, 2026").
        Un solo round-trip JS.
        """
        found_new = 0

        items = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();

                // h3 con clase entry-title contiene el enlace
                document.querySelectorAll(
                    'h3.entry-title, h3[class*="entry-title"]'
                ).forEach(h3 => {
                    const anchor = h3.querySelector('a[href]');
                    if (!anchor) return;

                    const href  = anchor.getAttribute('href') || '';
                    const title = (
                        anchor.getAttribute('title') ||
                        anchor.innerText ||
                        ''
                    ).replace(/\\s+/g, ' ').trim();

                    if (!href || !title || seen.has(href)) return;
                    seen.add(href);

                    // Buscar fecha en el artículo contenedor
                    let date = '';
                    const container = h3.closest(
                        'article, [class*="vdg-posts-item"], '
                        + '[class*="archive"], li'
                    ) || h3.parentElement?.parentElement;

                    if (container) {
                        // span.published > abbr[title] contiene la fecha textual
                        const abbr = container.querySelector(
                            'span.published abbr, '
                            + '[class*="published"] abbr, '
                            + '.vdg-entry-meta abbr'
                        );
                        if (abbr) {
                            // Usar innerText del abbr ("May 26, 2026")
                            date = (abbr.innerText || '').trim();
                        }
                        // Fallback: time[datetime]
                        if (!date) {
                            const timeEl = container.querySelector('time[datetime]');
                            if (timeEl) date = timeEl.getAttribute('datetime') || '';
                        }
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
            if not is_article_url(href):
                continue
            if href in collected:
                continue

            # Intentar parsear fecha textual; si falla intentar como ISO
            pub_date = ""
            if date_text:
                pub_date = parse_vdg_date(date_text)
                if not pub_date:
                    pub_date = parse_iso_date(date_text)

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
        page = await context.new_page()

        try:
            await page.goto(
                link_data["url"], wait_until="domcontentloaded", timeout=ARTICLE_TIMEOUT
            )
            await page.wait_for_timeout(1800)

            # Cerrar pop-up si apareció
            await self._handle_page_popups(page)

            # -----------------------------------------------------------
            # Fecha: refinar con meta tag
            # -----------------------------------------------------------
            publication_date = link_data.get("publication_date", "")

            meta_el = await page.query_selector(
                'meta[property="article:published_time"]'
            )
            if meta_el:
                raw_dt = await meta_el.get_attribute("content")
                if raw_dt:
                    publication_date = parse_iso_date(raw_dt)

            # Fallback: time[datetime] en el artículo
            if not publication_date:
                time_el = await page.query_selector(
                    "span.published abbr, time[datetime]"
                )
                if time_el:
                    tag = await time_el.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "time":
                        raw_dt = await time_el.get_attribute("datetime")
                        if raw_dt:
                            publication_date = parse_iso_date(raw_dt)
                    else:
                        text = await time_el.inner_text()
                        publication_date = parse_vdg_date(text)

            # -----------------------------------------------------------
            # Sección: refinar desde categoría del artículo
            # -----------------------------------------------------------
            section = link_data.get("section", "")
            try:
                cat_el = await page.query_selector(
                    "span.vdg-terms a, "
                    "[class*='vdg-terms'] a, "
                    "a[rel='category tag']"
                )
                if cat_el:
                    cat_text = clean_text(await cat_el.inner_text())
                    if cat_text:
                        section = cat_text
            except Exception:
                pass

            # -----------------------------------------------------------
            # Texto: div.entry-content → todos los <p>, <h2>, <h3>, <h4>
            # Excluir sharing, WPML, figuras, comentarios
            # -----------------------------------------------------------
            content_div = await page.query_selector(
                "div.entry-content, div[class*='entry-content']"
            )

            if not content_div:
                content_div = await page.query_selector(
                    "article .post-content, article main"
                )

            if not content_div:
                self.logger.warning(f"Sin contenedor: {link_data['url']}")
                return None

            full_text = await content_div.evaluate("""
                (el) => {
                    const clone = el.cloneNode(true);

                    const remove_sels = [
                        // Sharing buttons (sticky wrapper)
                        '.sticky-wrapper', '[id*="postShares"]',
                        '[class*="postShares"]', '[class*="sharer"]',
                        // WPML (traducción)
                        '[class*="wpml-ls"]', '[class*="wpml"]',
                        '[noindex]',
                        // Figuras e imágenes
                        'figure', 'figcaption',
                        '[class*="wp-caption"]', '[class*="wp-block-image"]',
                        // Artículos relacionados
                        '[class*="related"]', '[id*="related"]',
                        // Comentarios
                        '#comments', '[class*="comment"]',
                        // Navegación
                        'nav', '[class*="post-navigation"]',
                        '[id*="scroll-limit"]',
                        // Redes sociales
                        '[class*="social"]', '[class*="addtoany"]',
                        // Scripts y estilos
                        'script', 'style', 'iframe',
                    ];

                    remove_sels.forEach(sel => {
                        try {
                            clone.querySelectorAll(sel).forEach(e => e.remove());
                        } catch(err) {}
                    });

                    const parts = [];
                    const seen = new Set();

                    // Párrafos, listas y subtítulos
                    clone.querySelectorAll('p, h2, h3, h4, li').forEach(el => {
                        const text = (el.innerText || el.textContent || '')
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

    parser = argparse.ArgumentParser(description="Scraper Voz de Guanacaste")
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

    scraper = VozDeGuanacaste(
        output_dir=args.output,
        log_dir=args.logs,
        test_mode=args.test,
    )
    summary = scraper.run()
    print(summary)
