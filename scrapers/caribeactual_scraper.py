import asyncio
import re
from playwright.async_api import async_playwright
from scrapers.base_scraper import BaseScraper, CorteIncremental, cerrar_pagina

MONTHS_ES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
}

SECTIONS = [
    ("cantonal",   "https://caribeactual.com/category/cantones/"),
    ("nacionales", "https://caribeactual.com/category/nacionales/"),
]

TEST_MAX_SECTIONS = 2
TEST_MAX_PAGES    = 2
TEST_MAX_ARTICLES = 8


def parse_caribe_date(raw: str) -> str:
    """'abril 14, 2026' → '2026-04-14'"""
    raw = raw.strip().lower()
    # Pattern: "mes D, YYYY"
    m = re.match(r"(\w+)\s+(\d{1,2}),\s+(\d{4})", raw)
    if m:
        month_str, day, year = m.groups()
        month = MONTHS_ES.get(month_str)
        if month:
            return f"{year}-{month}-{day.zfill(2)}"
    return raw


class CarribeActualScraper(BaseScraper):
    SOURCE_NAME = "caribeactual"
    BASE_URL    = "https://caribeactual.com"

    def __init__(self, output_dir, log_dir, test_mode=False):
        super().__init__(output_dir=output_dir, log_dir=log_dir)
        self.test_mode = test_mode

    def scrape(self) -> list[dict]:
        return asyncio.run(self._scrape_async())

    # ------------------------------------------------------------------
    # Phase 1 – collect article links
    # ------------------------------------------------------------------
    async def _collect_links(self, browser) -> dict:
        seen: dict[str, dict] = {}
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
                page = await browser.new_page()
                try:
                    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(800)

                    # h2.entry-title a  (listing pages use h2 or h3)
                    cards = await page.query_selector_all(
                        "h2.entry-title a, h3.entry-title a"
                    )
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
                            seen[href] = {
                                "url":     href,
                                "title":   title,
                                "section": section_name,
                            }
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
    # Phase 2 – visit each article
    # ------------------------------------------------------------------
    async def _scrape_article(self, browser, link_data: dict) -> dict | None:
        url     = link_data["url"]
        section = link_data["section"]
        page    = await browser.new_page()
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_timeout(500)

            # Title
            title_el = await page.query_selector("h1.entry-title")
            title = (await title_el.inner_text()).strip() if title_el else link_data.get("title", "")

            # Date: <time class="entry-date published" …>abril 14, 2026</time>
            date_raw = ""
            date_el = await page.query_selector("time.entry-date.published")
            if date_el:
                date_raw = (await date_el.inner_text()).strip()
            publication_date = parse_caribe_date(date_raw) if date_raw else "NULL"

            # Full text: all <p> inside div.entry-content
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

            self.logger.info("Phase 1: collecting article links…")
            links = await self._collect_links(browser)
            self.logger.info(f"Found {len(links)} unique articles.")

            link_list = [l for l in links.values() if not self.debe_omitir(l["url"])]
            if self.incremental:
                self.logger.info(
                    f"Artículos nuevos por visitar: {len(link_list)} "
                    f"({self.omitidos_conocidos} ya estaban en la base)"
                )
            if self.test_mode:
                link_list = link_list[:TEST_MAX_ARTICLES]

            self.logger.info("Phase 2: scraping articles…")
            for i, link_data in enumerate(link_list, 1):
                if self.tiempo_agotado():
                    break
                self.logger.info(f"  [{i}/{len(link_list)}] {link_data['url']}")
                article = await self.articulo_seguro(browser, link_data)
                if article:
                    results.append(article)
                    self.checkpoint(results)

            await browser.close()

        self.logger.info(f"Done. {len(results)} articles collected.")
        return results
