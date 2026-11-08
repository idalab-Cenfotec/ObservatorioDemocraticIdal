"""
Selectores del cuerpo de las notas de lavozdegoicoechea.com (tema Hestia de WordPress).

Viven en un módulo aparte, sin dependencias, para poder probarlos con BeautifulSoup sin Playwright.

Por qué no se usan selectores amplios: el selector anterior (`article, main, div[class*='content'],
div[class*='post']`) devolvía el PRIMER elemento en orden de documento, que era
`<div class="wrapper post-NNN post ...">`, un contenedor que envuelve toda la página (la nota, notas
relacionadas y pie). El texto de cada nota salía con 9,000 a 15,000 caracteres de contenido ajeno
(653 de 705 notas, mediana 14,336), lo que contamina los embeddings. La nota está en
`article .entry-content`.
"""

# En orden de preferencia. Se usa el primero que exista; si ninguno existe la nota se descarta
# (se registra) en vez de caer en un contenedor amplio que arrastre la página entera.
BODY_SELECTORS = [
    "article .entry-content",
    "article",
]

# Se eliminan dentro del contenedor antes de extraer los párrafos.
REMOVE_SELECTORS = [
    "script", "style", "iframe", "nav",
    "figure", "figcaption",
    ".sharedaddy", ".sd-sharing", ".sd-block", ".addtoany_share_save_container",
    ".comments", ".related", ".related-posts",
    ".ads", ".ad",
]
