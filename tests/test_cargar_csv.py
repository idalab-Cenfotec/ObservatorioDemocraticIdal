"""cargar_csv_a_postgres.preparar: mismos filtros que BaseScraper."""
import unittest

import pandas as pd

from tests._util import RAIZ  # noqa: F401
from cargar_csv_a_postgres import preparar

COLS = ["source", "url", "title", "publication_date", "scraping_date", "section", "full_text", "language"]


def fila(url, texto="x" * 400, fecha="2026-10-05 10:00:00"):
    return dict(zip(COLS, ["fuente", url, "Titulo", fecha, "2026-10-11 10:00:00", "Seccion", texto, "es"]))


class TestPreparar(unittest.TestCase):
    def test_filtra_corto_sin_fecha_y_repetidas(self):
        df = pd.DataFrame([fila("https://a/1"), fila("https://a/1"), fila("https://a/2", texto="corto"),
                           fila("https://a/3", fecha="N/A"), fila("https://a/4")])
        out, st = preparar(df)
        self.assertEqual(sorted(out["url"]), ["https://a/1", "https://a/4"])
        self.assertEqual((st["descartadas_texto_corto"], st["descartadas_sin_fecha"], st["a_enviar"]), (1, 1, 2))

    def test_columnas_faltantes(self):
        with self.assertRaises(ValueError):
            preparar(pd.DataFrame([{"url": "https://a/1"}]))


if __name__ == "__main__":
    unittest.main()
