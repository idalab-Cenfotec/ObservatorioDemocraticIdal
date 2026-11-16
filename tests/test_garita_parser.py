"""noticiaslagaritacr.com: selectores del tema nuevo (nov-2026) y respaldo del anterior."""
import unittest

from bs4 import BeautifulSoup

from tests._util import RAIZ  # noqa: F401
from scrapers.garita_parser import extraer_enlaces, extract_article_data

LISTADO_NUEVO = """
<h2>Nacionales</h2>
<article class="tarjeta"><a class="img" href="https://x.cr/2026/11/12/a/"><img></a>
  <div class="cuerpo"><a class="cat" href="https://x.cr/category/n/">Nac</a>
  <h3><a href="https://x.cr/2026/11/12/a/">Nota A</a></h3></div></article>
<article class="tarjeta"><div class="cuerpo"><h3><a href="https://x.cr/2026/03/17/b/">Nota B</a></h3></div></article>
"""
LISTADO_VIEJO = '<h4 class="title"><a href="https://x.cr/vieja/">Vieja</a></h4>'

NOTA_NUEVA = """
<html><head><meta property="article:published_time" content="2026-11-13T00:51:14+00:00"></head><body>
<article class="articulo"><a class="cat">Espectáculos</a><h1>Título  de la nota</h1><p class="meta">12 nov</p>
<div class="contenido"><p>Primer párrafo.</p><script>var x=1;</script><p>Segundo   párrafo.</p></div></article>
</body></html>
"""
NOTA_VIEJA = """
<html><head><meta property="article:published_time" content="2025-01-02T10:00:00"></head><body>
<div class="mg-header"><h1 class="title">Título viejo</h1></div>
<article class="small single"><p>Cuerpo viejo.</p></article></body></html>
"""


def sopa(h):
    return BeautifulSoup(h, "html.parser")


class TestGaritaParser(unittest.TestCase):
    def test_enlaces_tema_nuevo(self):
        self.assertEqual(extraer_enlaces(sopa(LISTADO_NUEVO)),
                         ["https://x.cr/2026/11/12/a/", "https://x.cr/2026/03/17/b/"])

    def test_enlaces_tema_anterior_como_respaldo(self):
        self.assertEqual(extraer_enlaces(sopa(LISTADO_VIEJO)), ["https://x.cr/vieja/"])

    def test_pagina_sin_notas_no_devuelve_enlaces(self):
        self.assertEqual(extraer_enlaces(sopa("<h2>Nacionales</h2>")), [])

    def test_nota_tema_nuevo(self):
        t, f, c = extract_article_data(sopa(NOTA_NUEVA))
        self.assertEqual((t, f, c), ("Título de la nota", "2026-11-13", "Primer párrafo. Segundo párrafo."))

    def test_nota_tema_anterior(self):
        self.assertEqual(extract_article_data(sopa(NOTA_VIEJA)),
                         ("Título viejo", "2025-01-02", "Cuerpo viejo."))

    def test_nota_sin_estructura_conocida(self):
        self.assertEqual(extract_article_data(sopa("<p>nada</p>")), (None, None, None))


if __name__ == "__main__":
    unittest.main()
