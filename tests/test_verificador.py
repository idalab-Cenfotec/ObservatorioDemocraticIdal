"""Criterio de la verificación de escritura dual: cuándo es OK, aviso o desviación."""
import unittest

from tests._util import RAIZ  # noqa: F401
from verificar_escritura_dual import evaluar_envio


class TestEvaluarEnvio(unittest.TestCase):
    def test_sin_envio_es_ok(self):
        self.assertEqual(evaluar_envio(None, []), ([], []))

    def test_cuadra(self):
        envio = {"enviados": 5, "recibidos": 5, "insertados": 4, "ya_existian": 1, "rechazados": 0}
        self.assertEqual(evaluar_envio(envio, []), ([], []))

    def test_rechazadas_son_aviso_no_falla(self):
        envio = {"enviados": 5, "recibidos": 5, "insertados": 4, "ya_existian": 0, "rechazados": 1}
        fallas, avisos = evaluar_envio(envio, [])
        self.assertEqual(fallas, [])
        self.assertEqual(len(avisos), 1)

    def test_recibidos_menos_que_enviados_falla(self):
        envio = {"enviados": 5, "recibidos": 3, "insertados": 3, "ya_existian": 0, "rechazados": 0}
        self.assertEqual(len(evaluar_envio(envio, [])[0]), 1)

    def test_suma_que_no_cuadra_falla(self):
        envio = {"enviados": 5, "recibidos": 5, "insertados": 3, "ya_existian": 0, "rechazados": 0}
        self.assertEqual(len(evaluar_envio(envio, [])[0]), 1)

    def test_contingencia_falla(self):
        self.assertEqual(len(evaluar_envio({"contingencia": True, "error": "x"}, [])[0]), 1)

    def test_lote_pendiente_falla(self):
        self.assertEqual(len(evaluar_envio(None, ["2026-10-05_nuevos.csv"])[0]), 1)


if __name__ == "__main__":
    unittest.main()
