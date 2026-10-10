"""
scrapers_registry.py
Registro de fuentes del Observatorio Democrático.

Define qué scrapers existen (SCRAPERS_REGISTRY: modernos con Playwright; LEGACY_SCRAPERS:
scripts procedurales/WordPress vía LegacyScraperAdapter), cuáles están pausados
(PAUSED_SCRAPERS) y cómo instanciar cada uno (get_scraper_instance).
La ejecución la orquesta pipeline_runner.py.

    python pipeline_runner.py --only teletica --test
"""

import sys
import os
import importlib
import inspect

# Agregar el directorio raíz al path para importaciones
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scrapers.legacy_adapter import LegacyScraperAdapter, get_legacy_registry

# -----------------------------------------------------------------------
# REGISTRO DE SCRAPERS
# -----------------------------------------------------------------------
SCRAPERS_REGISTRY = {
    ##Alli
    "elperiodicocr": ("scrapers.elperiodicocr", "ElPeriodicoCRScraper"),
    "lareaccioncr":  ("scrapers.lareaccioncr",  "LaReaccionCRScraper"),
    "larevistacr":   ("scrapers.larevistacr",   "LaRevistaCRScraper"),
    "latejacr":      ("scrapers.latejacr",      "LaTejaCRScraper"),
    "lavozdegoicoechea": ("scrapers.lavozdegoicoechea", "LaVozDeGoicoecheaScraper"),
    "monumental":        ("scrapers.monumental",        "MonumentalScraper"),
    "ncrnoticias":       ("scrapers.ncrnoticias",       "NCRNoticiasScraper"),
    "noticiasenlineacr": ("scrapers.noticiasenlineacr", "NoticiasEnLineaCRScraper"),
    "costaricastar":     ("scrapers.costaricastar",     "CostaRicaStarScraper"),
    "genteopa":          ("scrapers.genteopa",          "GenteOPAScraper"),
    "pulsocr":           ("scrapers.pulsocr",           "PulsoCRScraper"),
    "puroperiodismo":    ("scrapers.puroperiodismo",    "PuroPeriodismoScraper"),
    "repretel":          ("scrapers.repretel",          "RepretelScraper"),
    "rumboeconomico":    ("scrapers.rumboeconomico",    "RumboEconomicoScraper"),
    "sinartdigital":     ("scrapers.sinartdigital",     "SinartDigitalScraper"),
    "telediario":        ("scrapers.telediario",        "TelediarioCRScraper"),
    "teletica":          ("scrapers.teletica",          "TeleticaScraper"),
    "theglobalcr":       ("scrapers.theglobalcr",       "TheGlobalCRScraper"),
    "ticotimes":         ("scrapers.ticotimes",         "TicoTimesScraper"),
    "trivisioncr":       ("scrapers.trivisioncr",       "TrivisionCRScraper"),

    # Scrapers adicionales detectados en scrapers/
    "caribeactual":    ("scrapers.caribeactual_scraper", "CarribeActualScraper"),
    "presidencia":     ("scrapers.presidencia_scraper",  "PresidenciaScraper"),
    "puntarenasseoye": ("scrapers.puntarenasseoye",      "PuntarenasSeOyeScraper"),
    "ticosland":       ("scrapers.ticosland",            "TicosLandScraper"),
    "vozdeguanacaste": ("scrapers.vozdeguanacaste",      "VozDeGuanacaste"),

    # ------------------------------------------------------------------
    # Los 45 scrapers legacy (19 sitios "Gre" / wordpress_sites, los 24
    # scripts procedurales/Colab, eljornalcr y observatorio_democratico)
    # se removieron de este registro. Ahora se ejecutan a través de
    # LegacyScraperAdapter + get_legacy_registry() — ver LEGACY_SCRAPERS
    # y get_scraper_instance() más abajo, y scrapers/legacy_adapter.py.
    # ------------------------------------------------------------------
}

# Scrapers legacy: se manejan vía LegacyScraperAdapter, no vía SCRAPERS_REGISTRY
LEGACY_SCRAPERS = set(get_legacy_registry().keys())


def get_scraper_instance(name: str, output_dir: str, log_dir: str,
                          test_mode: bool = False):
    """
    Retorna una instancia del scraper correcto para el nombre dado.
    Si es legacy, usa LegacyScraperAdapter.
    Si es moderno, usa la clase registrada en SCRAPERS_REGISTRY.
    """
    legacy_registry = get_legacy_registry()

    if name in legacy_registry:
        return LegacyScraperAdapter(
            source_name=name,
            script_path=legacy_registry[name],
            output_dir=output_dir,
            log_dir=log_dir,
            test_mode=test_mode
        )

    if name not in SCRAPERS_REGISTRY:
        raise ValueError(f"Scraper '{name}' no encontrado en ningún registro")

    module_path, class_name = SCRAPERS_REGISTRY[name]
    module = importlib.import_module(module_path)
    ScraperClass = getattr(module, class_name)

    if "test_mode" in inspect.signature(ScraperClass.__init__).parameters:
        return ScraperClass(output_dir=output_dir, log_dir=log_dir,
                           test_mode=test_mode)
    if test_mode:
        print(f"        ⚠ {name} no soporta test_mode — ejecutando completo")
    return ScraperClass(output_dir=output_dir, log_dir=log_dir)


# -----------------------------------------------------------------------
# FUENTES PAUSADAS
# No corren en el pipeline diario (ni están en .github/workflows/pipeline.yml) hasta
# resolver el motivo. El código y las notas ya cargadas se conservan. Para reintegrar una
# fuente: arreglar su causa, quitarla de este diccionario y agregarla a un grupo del workflow.
# tests/test_consistencia.py exige que registro = workflow + pausadas.
# -----------------------------------------------------------------------
PAUSED_SCRAPERS: dict[str, str] = {
    "ncrnoticias":    "Cloudflare muestra el desafío 'Un momento…' a la IP del runner (0 notas desde que entró al pipeline)",
    "trivisioncr":    "Cloudflare muestra el desafío 'Un momento…' a la IP del runner",
    "elperiodicocr":  "Desde el runner la portada no entrega artículos (Cloudflare); desde una red normal sí funciona",
    "acontecer_cr":   "Cloudflare bloquea al runner de forma intermitente (falló 3 de 4 corridas)",
    "el_seminario":   "El sitio responde con error HTTP solo a la IP del runner; mismo medio que 'seminario'",
    "seminario":      "El sitio responde con error HTTP solo a la IP del runner; mismo medio que 'el_seminario'",
    "rumboeconomico": "Timeouts constantes en el runner (348 en 4 corridas) y sin notas nuevas desde el 5/10",
    "enlamira":       "El sitio no responde (conexión rechazada) de forma intermitente",
    "costaricastar":  "Corpus completo (17,566 notas) y el sitio no publica desde 2022",
}
