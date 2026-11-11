"""Pruebas de los dos arreglos de SCRUM-26 en corpus_updater.py.
Corren sobre un directorio temporal: no tocan corpus, contingencia ni logs reales."""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import pandas as pd
import requests

from tests._util import RAIZ  # noqa: F401  (ajusta sys.path como el resto de las pruebas)
import corpus_updater as cu

COLS = cu.REQUIRED_COLUMNS


def _fila(url, title="Titulo"):
    return {"source": "s", "url": url, "title": title, "publication_date": "2026-10-01",
            "scraping_date": "2026-10-02", "section": "x", "full_text": "t", "language": "es"}


def _csv(path, filas):
    pd.DataFrame(filas, columns=COLS).to_csv(path, sep=cu.SEPARATOR, index=False, encoding="utf-8")


def _limpieza_que_falla_con_boom(real):
    """clean_dataframe real, salvo que algún título sea BOOM: simula un dato que rompe la limpieza."""
    def f(df, *a, **k):
        if df["title"].astype(str).str.contains("BOOM").any():
            raise ValueError("fallo simulado de limpieza")
        return real(df, *a, **k)
    return f


class Base(unittest.TestCase):
    def setUp(self):
        real = cu.clean_dataframe
        p = mock.patch.object(cu, "clean_dataframe", side_effect=_limpieza_que_falla_con_boom(real))
        p.start()
        self.addCleanup(p.stop)
        self._cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        self.hoy = datetime.now(cu.CR_TZ).strftime("%Y%m%d")
        Path("corpus").mkdir()
        Path("output").mkdir()
        _csv(f"corpus/corpus_observatorio_v1_{self.hoy}.csv", [_fila("https://a.cr/viejo")])

    def tearDown(self):
        os.chdir(self._cwd)
        self.tmp.cleanup()

    def nuevos(self, filas):
        _csv(f"output/medio_{self.hoy}.csv", filas)

    def contingencia(self):
        d = Path("contingencia")
        return sorted(d.glob("*.csv")) if d.is_dir() else []


class DryRunSinEfectos(Base):
    def test_dry_run_no_llama_a_n8n_ni_borra_contingencia(self):
        Path("contingencia").mkdir()
        _csv("contingencia/2026-10-01_nuevos.csv", [_fila("https://a.cr/pendiente")])
        self.nuevos([_fila("https://a.cr/nuevo")])
        with mock.patch.object(cu.requests, "post") as post:
            post.return_value.json.return_value = {}
            cu.update_corpus("output", "corpus", dry_run=True,
                             webhook_url="http://n8n/x", webhook_token="t")
        post.assert_not_called()
        self.assertEqual(len(self.contingencia()), 1, "dry-run no debe vaciar contingencia/")

    def test_corrida_normal_si_reintenta_contingencia(self):
        Path("contingencia").mkdir()
        _csv("contingencia/2026-10-01_nuevos.csv", [_fila("https://a.cr/pendiente")])
        self.nuevos([_fila("https://a.cr/nuevo")])
        with mock.patch.object(cu.requests, "post") as post:
            post.return_value.json.return_value = {}
            cu.update_corpus("output", "corpus", dry_run=False,
                             webhook_url="http://n8n/x", webhook_token="t")
        self.assertTrue(post.called)
        self.assertEqual(self.contingencia(), [], "la corrida normal sí reenvía y limpia contingencia/")


class ErroresNoTumbanElArranque(Base):
    def test_error_de_limpieza_en_contingencia_vieja_no_aborta(self):
        Path("contingencia").mkdir()
        _csv("contingencia/2026-10-01_nuevos.csv", [_fila("https://a.cr/p", title="BOOM")])
        self.nuevos([_fila("https://a.cr/nuevo")])
        with mock.patch.object(cu.requests, "post") as post:
            post.return_value.json.return_value = {}
            stats = cu.update_corpus("output", "corpus", dry_run=False,
                                     webhook_url="http://n8n/x", webhook_token="t")
        self.assertEqual(stats["nuevos"], 1)
        self.assertEqual(len(self.contingencia()), 1, "el lote defectuoso queda en contingencia")

    def test_error_de_limpieza_en_envio_deja_lote_en_contingencia(self):
        self.nuevos([_fila("https://a.cr/malo", title="BOOM")])
        with mock.patch.object(cu.requests, "post") as post:
            stats = cu.update_corpus("output", "corpus", dry_run=False,
                                     webhook_url="http://n8n/x", webhook_token="t")
        post.assert_not_called()
        self.assertTrue(Path(stats["output_file"]).exists(), "el CSV del corpus se guarda igual")
        self.assertEqual(len(self.contingencia()), 1)

    def test_fallo_de_red_sigue_funcionando(self):
        self.nuevos([_fila("https://a.cr/nuevo")])
        with mock.patch.object(cu.requests, "post", side_effect=requests.exceptions.ConnectionError("x")):
            stats = cu.update_corpus("output", "corpus", dry_run=False,
                                     webhook_url="http://n8n/x", webhook_token="t")
        self.assertEqual(len(self.contingencia()), 1)
        self.assertEqual(stats["nuevos"], 1)


if __name__ == "__main__":
    unittest.main()
