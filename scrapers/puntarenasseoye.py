import asyncio
import re
from datetime import datetime
from playwright.async_api import async_playwright
from scrapers.base_scraper import BaseScraper, CorteIncremental, cerrar_pagina

MONTHS_ES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
}

# Sections to scrape
SECTIONS = [
    ("nacionales",  "https://www.puntarenasseoye.com/category/nacionales/"),
    ("regionales",  "https://www.puntarenasseoye.com/category/puntarenas/regionales/"),
    ("salud",       "https://www.puntarenasseoye.com/category/salud/"),
    ("sucesos",     "https://www.puntarenasseoye.com/category/sucesos/"),
    ("turismo",     "https://www.puntarenasseoye.com/category/turismo/"),
]

TEST_MAX_SECTIONS = 2
TEST_MAX_PAGES    = 2
TEST_MAX_ARTICLES = 8


def parse_pso_date(raw: str) -> str:
    """'13 de abril de 2026' → '2026-04-13'"""
    raw = raw.strip().lower()
    # Pattern: "D de MES de YYYY"
    m = re.match(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", raw)
    if m:
        day, month_str, year = m.groups()
        month = MONTHS_ES.get(month_str)
        if month:
            return f"{year}-{month}-{day.zfill(2)}"
    return raw


async def _dismiss_popups(page):
    """Try to close any popup/overlay that might appear."""
    try:
        # OneSignal slidedown
        btn = page.locator("#onesignal-slidedown-cancel-button")
        if await btn.count() > 0 and await btn.is_visible():
            await btn.click()
            await page.wait_for_timeout(400)
    except Exception:
        pass
    try:
        # Generic close buttons
        for sel in [
            "button.close", "[class*='close']", "[aria-label='Close']",
            "[aria-label='Cerrar']", "button[id*='cancel']",
        ]:
            btns = page.locator(sel)
            count = await btns.count()
            for i in range(count):
                b = btns.nth(i)
                if await b.is_visible():
                    await b.click()
                    await page.wait_for_timeout(300)
                    break
    except Exception:
        pass
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass


class PuntarenasSeOyeScraper(BaseScraper):
    SOURCE_NAME = "puntarenasseoye"
    BASE_URL    = "https://www.puntarenasseoye.com"

    def __init__(self, output_dir, log_dir, test_mode=False):
        super().__init__(output_dir=output_dir, log_dir=log_dir)
        self.test_mode = test_mode

    def scrape(self) -> list[dict]:
        return asyncio.run(self._scrape_async())

    # ------------------------------------------------------------------
    # Phase 1 – collect article links across all sections / pages
    # ------------------------------------------------------------------
    async def _collect_links(self, context) -> dict:
        seen: dict[str, dict] = {}  # url → {url, section}
        sections = SECTIONS[:TEST_MAX_SECTIONS] if self.test_mode else SECTIONS

        for section_name, section_url in sections:
            page_num = 1
            corte = CorteIncremental(self)
            while True:
                if self.tiempo_agotado(0.5):
                    self.logger.warning(f"  [{section_name}] Tiempo del listado agotado, se pasa a los artículos")
                    break
                if self.test_mode and page_num > TEST_MAX_PAGES:
                    break
                url = section_url if page_num == 1 else f"{section_url}page/{page_num}/"
                page = await context.new_page()
                try:
                    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    await _dismiss_popups(page)
                    cards = await page.query_selector_all("h2.post-title a")
                    if not cards:
                        await cerrar_pagina(page)
                        break

                    new_found = 0
                    urls_pagina = []
                    for card in cards:
                        href  = await card.get_attribute("href")
                        title = (await card.inner_text()).strip()
                        if href:
                            urls_pagina.append(href)
                        if href and href not in seen:
                            seen[href] = {"url": href, "title": title, "section": section_name}
                            new_found += 1

                    await cerrar_pagina(page)
                    if self.test_mode and len(seen) >= TEST_MAX_ARTICLES:
                        break
                    if new_found == 0:
                        break
                    # Varias páginas seguidas con solo URLs ya cargadas -> fin de sección
                    if corte.pagina(urls_pagina):
                        self.logger.info(f"  [{section_name}] Pág {page_num}: solo URLs ya conocidas, fin de sección")
                        break
                    page_num += 1
                except Exception as e:
                    self.logger.info(f"Error collecting {url}: {e}")
                    await cerrar_pagina(page)
                    break

            if self.test_mode and len(seen) >= TEST_MAX_ARTICLES:
                break

        return seen

    # ------------------------------------------------------------------
    # Phase 2 – visit each article and extract content
    # ------------------------------------------------------------------
    async def _scrape_article(self, context, link_data: dict) -> dict | None:
        url     = link_data["url"]
        section = link_data["section"]
        page    = await context.new_page()
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await _dismiss_popups(page)

            # Title (from article itself, fallback to collected title)
            title_el = await page.query_selector("h1.entry-title, h1.post-title")
            title = (await title_el.inner_text()).strip() if title_el else link_data.get("title", "")

            # Date: span.date inside .post-meta
            date_raw = ""
            date_el = await page.query_selector(".post-meta span.date")
            if date_el:
                date_raw = (await date_el.inner_text()).strip()
            publication_date = parse_pso_date(date_raw) if date_raw else "NULL"

            # Full text: all <p> inside .entry-content
            content_div = await page.query_selector("div.entry-content")
            full_text = ""
            if content_div:
                paras = await content_div.query_selector_all("p")
                parts = []
                for p in paras:
                    txt = (await p.inner_text()).strip()
                    if txt:
                        parts.append(txt)
                full_text = " ".join(parts)

            await cerrar_pagina(page)

            if len(full_text) < 300:
                self.logger.info(f"Skipping (short content): {url}")
                return None

            return {
                "url":              url,
                "title":            title,
                "section":          section,
                "publication_date": publication_date,
                "full_text":        full_text,
            }
        except Exception as e:
            self.logger.info(f"Error scraping article {url}: {e}")
            await cerrar_pagina(page)
            return None

    # ------------------------------------------------------------------
    # Orchestrator
    # ------------------------------------------------------------------
    async def _scrape_async(self) -> list[dict]:
        results = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ])

            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                locale="es-CR",
                timezone_id="America/Costa_Rica",
                viewport={"width": 1920, "height": 1080},
                extra_http_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "es-CR,es;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept-Encoding": "gzip, deflate, br",
                    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                }
            )
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                Object.defineProperty(navigator, 'languages', {get: () => ['es-CR', 'es', 'en']});
                window.chrome = { runtime: {} };
            """)

            # Phase 1
            self.logger.info("Phase 1: collecting article links…")
            links = await self._collect_links(context)
            self.logger.info(f"Found {len(links)} unique articles.")

            # Limit in test mode
            link_list = [l for l in links.values() if not self.debe_omitir(l["url"])]
            if self.incremental:
                self.logger.info(
                    f"Artículos nuevos por visitar: {len(link_list)} "
                    f"({self.omitidos_conocidos} ya estaban en la base)"
                )
            if self.test_mode:
                link_list = link_list[:TEST_MAX_ARTICLES]

            # Phase 2
            self.logger.info("Phase 2: scraping articles…")
            for i, link_data in enumerate(link_list, 1):
                if self.tiempo_agotado():
                    break
                self.logger.info(f"  [{i}/{len(link_list)}] {link_data['url']}")
                article = await self.articulo_seguro(context, link_data)
                if article:
                    results.append(article)
                    self.checkpoint(results)

            await browser.close()

        self.logger.info(f"Done. {len(results)} articles collected.")
        return results
