"""Interruptor ESCRIBIR_CSV (SCRUM-29): con el CSV apagado no se crea una versión
nueva del corpus, pero lo nuevo sigue llegando a PostgreSQL y la verificación pasa."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

import pandas as pd

from tests._util import RAIZ
import corpus_builder as cb
import corpus_updater as cu
from escritura_csv import debe_escribir_csv, escribir_csv_activo


class _Webhook(BaseHTTPRequestHandler):
    recibidas: list[str] = []

    def do_POST(self):
        cuerpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        urls = [a["url"] for a in cuerpo["articulos"]]
        type(self).recibidas.extend(urls)
        b = json.dumps({"recibidos": len(urls), "insertados": len(urls), "ya_existian": 0, "rechazados": 0}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


def _fila(url):
    return {"source": "prueba", "url": url, "title": "Titulo de prueba " + url[-6:],
            "publication_date": "2026-10-05 10:00:00", "scraping_date": "2026-10-05 11:00:00",
            "section": "Noticias", "full_text": "texto de la nota " * 40, "language": "es"}


class TestInterruptor(unittest.TestCase):
    def test_valores(self):
        for valor, esperado in ((None, True), ("true", True), ("1", True),
                                ("false", False), ("FALSE", False), ("0", False), ("no", False)):
            env = {} if valor is None else {"ESCRIBIR_CSV": valor}
            with mock.patch.dict(os.environ, env, clear=False):
                if valor is None:
                    os.environ.pop("ESCRIBIR_CSV", None)
                self.assertEqual(escribir_csv_activo(), esperado, valor)

    def test_sin_webhook_el_csv_se_escribe_igual(self):
        with mock.patch.dict(os.environ, {"ESCRIBIR_CSV": "false"}):
            self.assertTrue(debe_escribir_csv(None))
            self.assertFalse(debe_escribir_csv("http://n8n/ingesta"))


class TestCsvApagado(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _Webhook)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}/ingesta"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        _Webhook.recibidas = []
        self.previo = os.getcwd()
        self.d = tempfile.mkdtemp()
        os.chdir(self.d)
        for sub in ("output", "corpus", "logs"):
            os.makedirs(sub)
        self.hoy = datetime.now(cu.CR_TZ).strftime("%Y%m%d")

    def tearDown(self):
        os.chdir(self.previo)

    def test_updater_no_crea_version_nueva_pero_envia(self):
        pd.DataFrame([_fila("https://prueba.cr/vieja-1")]).to_csv(
            "corpus/corpus_observatorio_v1_20260101.csv", sep="|", index=False)
        pd.DataFrame([_fila("https://prueba.cr/vieja-1"), _fila("https://prueba.cr/nueva-1")]).to_csv(
            f"output/prueba_{self.hoy}.csv", sep="|", index=False)
        with mock.patch.dict(os.environ, {"ESCRIBIR_CSV": "false"}):
            stats = cu.update_corpus("output", "corpus", False, self.url, "token")
        self.assertEqual(stats["nuevos"], 1)
        self.assertIsNone(stats["output_file"])
        self.assertEqual([p.name for p in Path("corpus").glob("*.csv")], ["corpus_observatorio_v1_20260101.csv"])
        self.assertEqual(_Webhook.recibidas, ["https://prueba.cr/nueva-1"])
        self.assertTrue(list(Path("logs").glob("envio_n8n_*.json")))

        # La verificación pasa y deja constancia de que el CSV estaba apagado.
        cmd = [sys.executable, "-B", os.path.join(RAIZ, "verificar_escritura_dual.py"),
               "--output", "output", "--corpus", "corpus", "--salida", "logs", "--contingencia", "contingencia"]
        r = subprocess.run(cmd, cwd=self.d, capture_output=True, text=True, encoding="utf-8",
                           env={**os.environ, "ESCRIBIR_CSV": "false", "N8N_URLS_CONOCIDAS_URL": "",
                                "N8N_WEBHOOK_TOKEN": "", "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("Escritura a CSV apagada", r.stdout)

    def test_updater_con_csv_encendido_sigue_igual(self):
        pd.DataFrame([_fila("https://prueba.cr/vieja-1")]).to_csv(
            "corpus/corpus_observatorio_v1_20260101.csv", sep="|", index=False)
        pd.DataFrame([_fila("https://prueba.cr/nueva-1")]).to_csv(
            f"output/prueba_{self.hoy}.csv", sep="|", index=False)
        with mock.patch.dict(os.environ, {"ESCRIBIR_CSV": "true"}):
            stats = cu.update_corpus("output", "corpus", False, self.url, "token")
        self.assertTrue(Path(stats["output_file"]).exists())
        self.assertEqual(len(list(Path("corpus").glob("*.csv"))), 2)

    def test_builder_no_guarda_csv_pero_envia(self):
        df = pd.DataFrame([_fila("https://prueba.cr/a"), _fila("https://prueba.cr/b")])
        ruta = "corpus/corpus_observatorio_v1_20261012.csv"
        with mock.patch.dict(os.environ, {"ESCRIBIR_CSV": "false"}):
            cb.guardar_corpus_dual(df, ruta, self.url, "token")
        self.assertFalse(Path(ruta).exists())
        self.assertEqual(sorted(_Webhook.recibidas), ["https://prueba.cr/a", "https://prueba.cr/b"])


if __name__ == "__main__":
    unittest.main()
