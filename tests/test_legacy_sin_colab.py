"""Los scrapers heredados de Colab no deben depender de google.colab.

En el runner `files` era None y `files.download(...)` terminaba el script con
AttributeError (el CSV ya estaba escrito). Se revisa el codigo fuente con ast,
sin ejecutar scraping ni red.
"""
import ast
import unittest
from pathlib import Path

SCRAPERS = Path(__file__).resolve().parent.parent / "scrapers"
ARCHIVOS = [
    "alajuela_digital.py", "crhoy.py", "digital506.py",
    "el_jornal.py", "el_sol_de_occidente.py", "sancarlosdigital.py",
]


class TestLegacySinColab(unittest.TestCase):
    def test_sin_files_download_ni_google_colab(self):
        for nombre in ARCHIVOS:
            with self.subTest(scraper=nombre):
                arbol = ast.parse((SCRAPERS / nombre).read_text(encoding="utf-8"))
                for nodo in ast.walk(arbol):
                    if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute):
                        if nodo.func.attr == "download" and getattr(nodo.func.value, "id", "") == "files":
                            self.fail(f"{nombre}:{nodo.lineno} llama files.download")
                    if isinstance(nodo, ast.ImportFrom) and (nodo.module or "").startswith("google.colab"):
                        self.fail(f"{nombre}:{nodo.lineno} importa google.colab")
                    if isinstance(nodo, ast.Import) and any(a.name.startswith("google.colab") for a in nodo.names):
                        self.fail(f"{nombre}:{nodo.lineno} importa google.colab")


if __name__ == "__main__":
    unittest.main()
