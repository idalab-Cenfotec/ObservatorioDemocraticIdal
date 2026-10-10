"""Una fuente que solo recibe páginas de desafío anti-bot debe terminar en ERROR, no en 'OK con 0 notas'."""
import tempfile
import unittest

from tests._util import RAIZ  # noqa: F401
from scrapers.base_scraper import BaseScraper, UMBRAL_BLOQUEOS


class FuenteBloqueada(BaseScraper):
    SOURCE_NAME = "prueba_bloqueo"
    BASE_URL = "https://ejemplo.invalid"

    def __init__(self, titulos, **kw):
        super().__init__(**kw)
        self._titulos = titulos

    def scrape(self):
        for t in self._titulos:
            self.registrar_pagina_sin_contenido(t)
        return []


class TestBloqueo(unittest.TestCase):
    def _correr(self, titulos):
        d = tempfile.mkdtemp()
        return FuenteBloqueada(titulos, output_dir=d, log_dir=d).run()

    def test_desafios_sin_notas_es_error(self):
        r = self._correr(["Un momento…"] * UMBRAL_BLOQUEOS)
        self.assertEqual(r["status"], "ERROR")
        self.assertIn("bloqueada", r["error"])

    def test_pocas_paginas_no_alcanzan(self):
        self.assertEqual(self._correr(["Un momento…"] * (UMBRAL_BLOQUEOS - 1))["status"], "OK")

    def test_titulos_normales_no_cuentan(self):
        self.assertEqual(self._correr(["Ministro dice que un momento histórico"] * 5)["status"], "OK")

    def test_patrones(self):
        f = FuenteBloqueada([], output_dir=tempfile.mkdtemp(), log_dir=tempfile.mkdtemp())
        for t in ["Just a moment...", "Attention Required! | Cloudflare", "403 Forbidden", "Access denied"]:
            self.assertTrue(f.registrar_pagina_sin_contenido(t), t)
        self.assertFalse(f.registrar_pagina_sin_contenido("Inicio - Teletica"))


if __name__ == "__main__":
    unittest.main()
