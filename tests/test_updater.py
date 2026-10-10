"""corpus_updater de punta a punta con un webhook falso: exclusiones, envío y reporte para la verificación."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pandas as pd

from tests._util import RAIZ
import corpus_updater as cu
import output_cleaner as oc


class _Webhook(BaseHTTPRequestHandler):
    perder_una = False
    recibidas: list[str] = []

    def do_POST(self):
        cuerpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        urls = [a["url"] for a in cuerpo["articulos"]]
        type(self).recibidas.extend(urls)
        k = len(urls)
        resp = {"recibidos": k, "insertados": k - (1 if type(self).perder_una else 0), "ya_existian": 0, "rechazados": 0}
        b = json.dumps(resp).encode()
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


class TestUpdater(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _Webhook)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}/ingesta"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _correr(self, perder_una=False):
        _Webhook.perder_una = perder_una
        _Webhook.recibidas = []
        excluida = "https://" + sorted(oc.cargar_exclusiones())[0]
        previo = os.getcwd()
        d = tempfile.mkdtemp()
        os.chdir(d)
        try:
            for sub in ("output", "corpus", "logs"):
                os.makedirs(sub)
            hoy = datetime.now(cu.CR_TZ).strftime("%Y%m%d")
            pd.DataFrame([_fila("https://prueba.cr/vieja-1"), _fila(excluida)]).to_csv(
                "corpus/corpus_observatorio_v1_20260101.csv", sep="|", index=False)
            pd.DataFrame([_fila("https://prueba.cr/nueva-1"), _fila("https://prueba.cr/nueva-2"), _fila(excluida)]).to_csv(
                f"output/prueba_{hoy}.csv", sep="|", index=False)
            stats = cu.update_corpus("output", "corpus", False, self.url, "token")
            return d, stats
        finally:
            os.chdir(previo)

    def test_excluida_se_quita_y_no_se_envia(self):
        _, stats = self._correr()
        self.assertEqual(stats["nuevos"], 2)
        self.assertEqual(stats["corpus_nuevo"], 3)           # vieja + 2 nuevas; la excluida no queda
        self.assertEqual(sorted(_Webhook.recibidas), ["https://prueba.cr/nueva-1", "https://prueba.cr/nueva-2"])

    def test_verificador_ok_y_desviacion(self):
        for perder, esperado in ((False, 0), (True, 1)):
            d, _ = self._correr(perder_una=perder)
            cmd = [sys.executable, "-B", os.path.join(RAIZ, "verificar_escritura_dual.py"),
                   "--output", "output", "--corpus", "corpus", "--salida", "logs", "--contingencia", "contingencia"]
            r = subprocess.run(cmd, cwd=d, capture_output=True, text=True, encoding="utf-8",
                               env={**os.environ, "N8N_URLS_CONOCIDAS_URL": "", "N8N_WEBHOOK_TOKEN": ""})
            self.assertEqual(r.returncode, esperado, r.stdout)


if __name__ == "__main__":
    unittest.main()
