"""Selector del cuerpo de lavozdegoicoechea.com: debe tomar la nota y no el contenedor que envuelve la página."""
import unittest

from bs4 import BeautifulSoup

from tests._util import RAIZ  # noqa: F401
from scrapers.lavozdegoicoechea_cuerpo import BODY_SELECTORS, REMOVE_SELECTORS

SELECTOR_ANTERIOR = "article, main, div[class*='content'], div[class*='post']"

NOTA = "El bienestar y la belleza ganan protagonismo entre los regalos del Día de la Madre."
AJENO_RELACIONADAS = "Rodrigo Chaves pidió dejar de comer cuento sobre los rumores del IVA."
AJENO_PIE = "Teléfono: +506 6226-2075 Correo: info@lavozdegoicoechea.com"

# Misma estructura que la página real (tema Hestia): los contenedores externos tienen "post" en la clase y aparecen
# ANTES del <article>; las notas relacionadas y el pie quedan fuera del <article>.
HTML = f"""
<html><body>
<div class="wrapper post-10663 post type-post status-publish classic-blog">
  <div class="blog-post blog-post-wrapper">
    <article id="post-10663" class="section section-text">
      <h1>Bienestar y belleza</h1>
      <div class="single-post-wrap entry-content">
        <p>{NOTA}</p>
        <div class="sd-block sd-social"><p>Share on Facebook (Se abre en una ventana nueva)</p></div>
      </div>
    </article>
    <section class="section related-posts"><div class="content"><p>{AJENO_RELACIONADAS}</p></div></section>
  </div>
  <footer><p>{AJENO_PIE}</p></footer>
</div>
</body></html>
"""


def _elegir(soup):
    for selector in BODY_SELECTORS:
        el = soup.select_one(selector)
        if el:
            return el
    return None


class TestCuerpoLaVozDeGoicoechea(unittest.TestCase):
    def setUp(self):
        self.soup = BeautifulSoup(HTML, "html.parser")

    def test_el_selector_anterior_arrastraba_la_pagina_entera(self):
        # Documenta el defecto original: el primer elemento que coincide es el contenedor externo.
        texto = self.soup.select_one(SELECTOR_ANTERIOR).get_text(" ", strip=True)
        self.assertIn(AJENO_RELACIONADAS, texto)
        self.assertIn(AJENO_PIE, texto)

    def test_el_selector_nuevo_toma_solo_la_nota(self):
        texto = _elegir(self.soup).get_text(" ", strip=True)
        self.assertIn(NOTA, texto)
        self.assertNotIn(AJENO_RELACIONADAS, texto)
        self.assertNotIn(AJENO_PIE, texto)

    def test_sin_contenedor_de_nota_no_se_cae_en_la_pagina(self):
        sin_nota = BeautifulSoup(
            '<div class="wrapper post-1 post"><p>Contenido de la pagina</p></div>', "html.parser")
        self.assertIsNone(_elegir(sin_nota))

    def test_no_hay_selectores_amplios(self):
        for selector in BODY_SELECTORS:
            with self.subTest(selector=selector):
                self.assertNotIn("main", selector.split())
                self.assertNotIn("[class*=", selector)

    def test_se_quitan_los_bloques_de_compartir_y_relacionadas(self):
        el = _elegir(self.soup)
        for selector in REMOVE_SELECTORS:
            for e in el.select(selector):
                e.decompose()
        texto = el.get_text(" ", strip=True)
        self.assertNotIn("Share on Facebook", texto)
        self.assertIn(NOTA, texto)


if __name__ == "__main__":
    unittest.main()
