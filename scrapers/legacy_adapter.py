"""
legacy_adapter.py
─────────────────
Adapter que hace compatibles los scrapers legacy con el pipeline moderno.
Ejecuta cada scraper legacy como subprocess, captura su CSV output
y lo retorna al pipeline sin modificar ningún scraper existente.
"""

import os
import sys
import glob
import time
import subprocess
import pandas as pd

from scrapers.base_scraper import BaseScraper

SEPARATOR = "|"
SCHEMA = ['source', 'url', 'title', 'publication_date',
          'scraping_date', 'section', 'full_text', 'language']

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OBSERVATORIO_SCRIPT = os.path.join(PROJECT_ROOT, "scrapers", "observatorio_democratico.py")

SUBPROCESS_TIMEOUT = 600
TEST_MODE_LIMIT = 20

# A CSV modified up to this many seconds before subprocess start is still
# accepted (handles filesystem timestamp rounding / clock skew).
_FRESHNESS_TOLERANCE_S = 10

# Colab scripts that write CSVs with names different from the source name.
# Maps source_name -> list of glob patterns to also search in _find_csv.
_CSV_OUTPUT_MAP: dict[str, list[str]] = {
    # ── G6 — Colab scripts con nombre de archivo distinto al source name ──
    "acontecer_cr":              ["acontecercr_*.csv", "acontecerCR_*.csv"],
    "alajuela_digital":          ["alajueladigital.csv"],
    "crc_89_1":                  ["crc891.csv"],
    "digital506":                ["DIGITAL506.csv", "digital506.csv"],
    "el_jilguero":               ["jilgueromedia.csv"],
    "el_jornal":                 ["eljornalcr.csv"],
    "el_observador":             ["observadorcr_*.csv"],
    "el_sol_de_occidente":       ["elsoldeoccidente.csv"],

    # ── G7 — Scripts con nombre de archivo distinto al source name ────────
    "el_financiero":             ["elfinancierocr.csv"],
    "el_mundo":                  ["noticias_elmundo_*.csv"],
    "el_seminario":              ["noticias_semanario_*.csv"],
    "eldelfino":                 ["noticias_delfino_*.csv"],
    "elnortehoy":                ["elnortehoycr.csv"],
    "lanacion":                  ["noticias_nacion_*.csv"],
    "larepublica":               ["noticias_larepublica_*.csv"],
    "noticias_la_garita_costa_rica": ["noticiaslagarita_*.csv"],
    "periodico_el_mundo":        ["elmundocr.csv"],
    "periodico_mi_tierra":       ["mitierra_*.csv"],
    "seminario":                 ["semanariouniversidad.csv"],
}


class LegacyScraperAdapter(BaseScraper):
    """
    Adapter para scrapers legacy procedurales (Tipo 1) y WordPress (Tipo 2).

    Tipo 1: scripts procedurales (con o sin Colab) que ejecutan toda su lógica
            al correr el archivo y escriben su propio CSV en disco.
    Tipo 2: sitios WordPress procesados por observatorio_democratico.py, que
            acepta el nombre del sitio como sys.argv[1] para filtrar a uno solo.

    El adapter corre el script como subprocess independiente, localiza el CSV
    resultante y lo convierte al formato estándar (lista de dicts con schema
    v1.0) para que BaseScraper.run() haga el resto.

    Uso:
        adapter = LegacyScraperAdapter(
            source_name="acontecercr",
            script_path="scrapers/acontecercr.py",
            output_dir="output",
            log_dir="logs"
        )
        result = adapter.run()
    """

    def __init__(self, source_name: str, script_path: str,
                 output_dir: str, log_dir: str, test_mode: bool = False):
        self.SOURCE_NAME = source_name
        self.script_path = script_path
        self.test_mode = test_mode
        # Tipo 2 detection: script is observatorio_democratico.py AND this is
        # an individual WordPress site (not the "run-all" meta-key).
        self.is_wordpress = (
            os.path.basename(script_path) == "observatorio_democratico.py"
            and source_name != "observatorio_democratico"
        )
        super().__init__(output_dir=output_dir, log_dir=log_dir)

    def scrape(self) -> list[dict]:
        # 1. Record start time for CSV freshness check
        subprocess_start = time.time()

        # 2. Ejecutar el script legacy como subprocess
        self._run_legacy_script()

        # 3. Buscar el CSV fresco que escribió en disco
        csv_path = self._find_csv(min_mtime=subprocess_start)
        if not csv_path:
            raise RuntimeError(
                f"No se encontró CSV de salida para '{self.SOURCE_NAME}' "
                f"tras ejecutar el script (buscado en PROJECT_ROOT, output/ y output_dir)"
            )

        # 4. Leerlo con pandas
        df = self._read_csv(csv_path)
        if df.empty and self.is_wordpress and self.incremental:
            # Sitios WordPress en modo incremental: el script deja un CSV solo con
            # encabezado cuando no hay notas nuevas desde la última corrida.
            self.logger.info(f"'{self.SOURCE_NAME}': sin notas nuevas desde la última corrida")
            return []
        if df.empty:
            raise RuntimeError(
                f"CSV vacío para '{self.SOURCE_NAME}': {csv_path} — "
                "el script corrió pero no produjo registros"
            )

        # 5. Validar que tiene el schema correcto
        df = self._ensure_schema(df)

        # 6. Limitar en modo prueba
        if self.test_mode:
            df = df.head(TEST_MODE_LIMIT)

        self.logger.info(f"'{self.SOURCE_NAME}': {len(df)} registros leídos desde {csv_path}")
        return df.to_dict('records')

    # ------------------------------------------------------------------
    # Paso 1 — ejecutar el script legacy en un proceso separado
    # ------------------------------------------------------------------

    def _run_legacy_script(self) -> None:
        if self.is_wordpress:
            # observatorio_democratico.py acepta el nombre del sitio como
            # sys.argv[1] para procesar solo ese sitio.
            cmd = [sys.executable, OBSERVATORIO_SCRIPT, self.SOURCE_NAME]
        else:
            cmd = [sys.executable, self.script_path]

        _env = os.environ.copy()
        _env["PYTHONIOENCODING"] = "utf-8"

        try:
            result = subprocess.run(
                cmd,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT,
                env=_env,
            )

            if result.returncode != 0:
                # Esperado: scripts con `from google.colab import files` fallan
                # al final, después de haber escrito su CSV en disco.
                self.logger.warning(
                    f"'{self.SOURCE_NAME}' subprocess terminó con código "
                    f"{result.returncode} (puede ser normal si el script usa "
                    f"google.colab). stderr: {(result.stderr or '').strip()[-1000:]}"
                )
            else:
                self.logger.info(f"'{self.SOURCE_NAME}' subprocess finalizado correctamente.")

            if result.stdout:
                self.logger.debug(f"'{self.SOURCE_NAME}' stdout: {result.stdout.strip()[-1000:]}")

        except subprocess.TimeoutExpired:
            self.logger.warning(
                f"'{self.SOURCE_NAME}' excedió el timeout de {SUBPROCESS_TIMEOUT}s. "
                "Se intentará leer el CSV ya escrito en disco hasta el momento."
            )

    # ------------------------------------------------------------------
    # Paso 2 — localizar el CSV escrito por el script legacy
    # ------------------------------------------------------------------

    def _find_csv(self, min_mtime: float | None = None) -> str | None:
        output_dir = self.output_dir
        if not os.path.isabs(output_dir):
            output_dir = os.path.join(PROJECT_ROOT, output_dir)

        search_dirs = {PROJECT_ROOT, os.path.join(PROJECT_ROOT, "output"), output_dir}

        candidates = set()
        for directory in search_dirs:
            for pattern in (f"{self.SOURCE_NAME}_*.csv", f"{self.SOURCE_NAME}.csv"):
                candidates.update(glob.glob(os.path.join(directory, pattern)))

        # For Colab scripts whose output filename differs from source name
        for alias_pattern in _CSV_OUTPUT_MAP.get(self.SOURCE_NAME, []):
            for directory in search_dirs:
                candidates.update(glob.glob(os.path.join(directory, alias_pattern)))

        # Exclude backup files — they are intermediate artifacts, not final output
        candidates = {p for p in candidates if "_backup" not in os.path.basename(p)}

        # Only accept files written during (or just before) this subprocess run
        if min_mtime is not None:
            candidates = {
                p for p in candidates
                if os.path.getmtime(p) >= min_mtime - _FRESHNESS_TOLERANCE_S
            }

        if not candidates:
            return None

        return max(candidates, key=os.path.getmtime)

    # ------------------------------------------------------------------
    # Paso 3 — leer el CSV (separador pipe o coma)
    # ------------------------------------------------------------------

    def _read_csv(self, path: str) -> pd.DataFrame:
        try:
            df = pd.read_csv(path, sep=SEPARATOR, encoding="utf-8-sig")
            if df.shape[1] <= 1:
                df = pd.read_csv(path, sep=",", encoding="utf-8-sig")
        except Exception:
            df = pd.read_csv(path, sep=",", encoding="utf-8-sig")
        return df

    # ------------------------------------------------------------------
    # Paso 4 — validar/normalizar contra el schema v1.0
    # ------------------------------------------------------------------

    def _ensure_schema(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in SCHEMA:
            if col not in df.columns:
                df[col] = "NULL"
        return df.fillna("NULL")


# Module-level cache — get_legacy_registry() builds 45+ os.path.join entries;
# the dict never changes at runtime so we build it once.
_registry_cache: dict | None = None


def get_legacy_registry() -> dict:
    """
    Retorna el registro de todos los scrapers legacy con su script_path.

    - Tipo 1 (procedurales, con o sin Colab): apuntan a su propio archivo
      dentro de scrapers/.
    - Tipo 2 (WordPress individuales): apuntan a observatorio_democratico.py;
      LegacyScraperAdapter detecta esto automáticamente y pasa el nombre del
      sitio como sys.argv[1] al subprocess.
    - "observatorio_democratico": meta-clave Tipo 1 que corre el script sin
      filtro de sitio, procesando los 19 sitios WordPress en una sola pasada.
    """
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    scrapers_dir = os.path.join(base, "scrapers")
    wordpress_script = os.path.join(scrapers_dir, "observatorio_democratico.py")

    _registry_cache = {
        # ---- Tipo 1: scripts procedurales Colab (24) ----
        "acontecercr":                   os.path.join(scrapers_dir, "acontecercr.py"),
        "acontecer_cr":                  os.path.join(scrapers_dir, "acontecer_cr.py"),
        "alajuela_digital":              os.path.join(scrapers_dir, "alajuela_digital.py"),
        "amprensa":                      os.path.join(scrapers_dir, "amprensa.py"),
        "crc_89_1":                      os.path.join(scrapers_dir, "crc_89_1.py"),
        "crhoy":                         os.path.join(scrapers_dir, "crhoy.py"),
        "diarioextra":                   os.path.join(scrapers_dir, "diarioextra.py"),
        "digital506":                    os.path.join(scrapers_dir, "digital506.py"),
        "el_jilguero":                   os.path.join(scrapers_dir, "el_jilguero.py"),
        "el_jornal":                     os.path.join(scrapers_dir, "el_jornal.py"),
        "el_observador":                 os.path.join(scrapers_dir, "el_observador.py"),
        "el_sol_de_occidente":           os.path.join(scrapers_dir, "el_sol_de_occidente.py"),
        "elnortehoy":                    os.path.join(scrapers_dir, "elnortehoy.py"),
        "noticias_la_garita_costa_rica": os.path.join(scrapers_dir, "noticias_la_garita_costa_rica.py"),
        "periodico_mi_tierra":           os.path.join(scrapers_dir, "periódico_mi_tierra.py"),
        "sancarlosdigital":              os.path.join(scrapers_dir, "sancarlosdigital.py"),
        "el_financiero":                 os.path.join(scrapers_dir, "el_financiero.py"),
        "el_mundo":                      os.path.join(scrapers_dir, "el_mundo.py"),
        "el_seminario":                  os.path.join(scrapers_dir, "el_seminario.py"),
        "eldelfino":                     os.path.join(scrapers_dir, "eldelfino.py"),
        "lanacion":                      os.path.join(scrapers_dir, "lanacion.py"),
        "larepublica":                   os.path.join(scrapers_dir, "larepublica.py"),
        "periodico_el_mundo":            os.path.join(scrapers_dir, "periodico_el_mundo.py"),
        "seminario":                     os.path.join(scrapers_dir, "seminario.py"),

        # ---- Tipo 1: procedural sin Colab ----
        "eljornalcr":                    os.path.join(scrapers_dir, "eljornal.py"),

        # ---- Tipo 1: meta-clave que corre todos los sitios WordPress de una vez ----
        "observatorio_democratico":      wordpress_script,

        # ---- Tipo 2: sitios WordPress individuales ----
        # Fuera del registro por estar muertos (DNS caído en julio de 2026, ver
        # observatorio_democratico.SITES): anexioncr, yambaradio, actualidaddeloeste,
        # alajuelitahoy; tamarindonews es una página vacía de cPanel.
        "guanacastealaaltura": wordpress_script,
        "periodicomensaje":    wordpress_script,
        "radiolapampa":        wordpress_script,
        "miprensacr":          wordpress_script,
        "radiopuertotv":       wordpress_script,
        "tvsur":               wordpress_script,
        "ustedseinforma":      wordpress_script,
        "adiariocr":           wordpress_script,
        "alajuelitasoy":       wordpress_script,
        "buzonderodrigo":      wordpress_script,
        "elcolectivo506":      wordpress_script,
        "elmonitorcr":         wordpress_script,
        "elmundo":             wordpress_script,
        "enlamira":            wordpress_script,
    }
    return _registry_cache
