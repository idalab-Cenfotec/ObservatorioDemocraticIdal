"""El registro de fuentes, el workflow diario y la lista de pausadas deben cuadrar."""
import re
import unittest

from tests._util import RAIZ
import os

from scrapers_registry import LEGACY_SCRAPERS, PAUSED_SCRAPERS, SCRAPERS_REGISTRY

# Script que procesa los sitios WordPress; no es una fuente
NO_FUENTES = {"observatorio_democratico"}


def fuentes_del_workflow() -> set[str]:
    with open(os.path.join(RAIZ, ".github", "workflows", "pipeline.yml"), encoding="utf-8") as f:
        texto = f.read()
    nombres = set()
    for bloque in re.findall(r"--only(.*?)--workers", texto, re.S):
        nombres |= set(re.findall(r"[a-z][a-z0-9_]*", bloque))
    return nombres


class TestConsistencia(unittest.TestCase):
    def setUp(self):
        self.registro = (set(SCRAPERS_REGISTRY) | set(LEGACY_SCRAPERS)) - NO_FUENTES
        self.workflow = fuentes_del_workflow()
        self.pausadas = set(PAUSED_SCRAPERS)

    def test_todo_lo_del_workflow_esta_registrado(self):
        self.assertEqual(self.workflow - self.registro, set())

    def test_las_pausadas_estan_registradas(self):
        self.assertEqual(self.pausadas - self.registro, set())

    def test_una_pausada_no_corre_en_el_workflow(self):
        self.assertEqual(self.pausadas & self.workflow, set())

    def test_registro_es_workflow_mas_pausadas(self):
        self.assertEqual(self.registro - self.workflow - self.pausadas, set(),
                         "fuente registrada que no corre ni está pausada")

    def test_toda_pausada_tiene_motivo(self):
        for nombre, motivo in PAUSED_SCRAPERS.items():
            self.assertTrue(motivo.strip(), nombre)


if __name__ == "__main__":
    unittest.main()
