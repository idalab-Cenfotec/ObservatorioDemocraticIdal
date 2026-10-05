"""
scrapers_registry.py
Orquestador principal del Observatorio Democrático.

Ejecuta todos los scrapers registrados de forma secuencial,
consolida los resultados y genera un reporte de ejecución.

Uso:
    python scrapers_registry.py
    python scrapers_registry.py --only elperiodicocr
    python scrapers_registry.py --list
    python scrapers_registry.py --only elperiodicocr --test
"""

import argparse
import sys
import os
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
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


CR_TZ = timezone(timedelta(hours=-6))


# -----------------------------------------------------------------------
# EJECUCIÓN DE SCRAPERS
# -----------------------------------------------------------------------
def run_scraper(
    name: str,
    output_dir: str = "output",
    log_dir: str = "logs",
    test_mode: bool = False,
) -> dict:
    """Carga y ejecuta un scraper por nombre (moderno o legacy)."""

    if name not in SCRAPERS_REGISTRY and name not in LEGACY_SCRAPERS:
        return {
            "source": name,
            "status": "ERROR",
            "error": f"Scraper '{name}' no registrado"
        }

    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        scraper = get_scraper_instance(
            name,
            output_dir=output_dir,
            log_dir=log_dir,
            test_mode=test_mode,
        )

        return scraper.run()

    except ImportError as e:
        return {
            "source": name,
            "status": "ERROR",
            "error": f"Error de importación: {e}"
        }

    except Exception as e:
        return {
            "source": name,
            "status": "ERROR",
            "error": str(e)
        }


# -----------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------
def print_banner():
    print("""
╔══════════════════════════════════════════════════════════════╗
║         OBSERVATORIO DEMOCRÁTICO — Pipeline de Datos         ║
║                  Estándar de Datos v1.0                      ║
╚══════════════════════════════════════════════════════════════╝
""")


def print_summary(results: list[dict], start_time: datetime):
    """Imprime el resumen final de ejecución."""

    elapsed = (datetime.now(CR_TZ) - start_time).total_seconds()

    total_valid = sum(
        r.get("total_valid", 0)
        for r in results
        if r.get("status") == "OK"
    )

    total_discarded = sum(
        r.get("total_discarded", 0)
        for r in results
        if r.get("status") == "OK"
    )

    ok_count = sum(
        1 for r in results
        if r.get("status") == "OK"
    )

    error_count = sum(
        1 for r in results
        if r.get("status") == "ERROR"
    )

    print("\n" + "═" * 60)
    print("  RESUMEN DE EJECUCIÓN")
    print("═" * 60)

    print(f"  Scrapers ejecutados : {len(results)}")
    print(f"  Exitosos            : {ok_count}")
    print(f"  Con errores         : {error_count}")
    print(f"  Artículos válidos   : {total_valid}")
    print(f"  Artículos descart.  : {total_discarded}")
    print(f"  Tiempo total        : {elapsed:.1f}s")

    print("═" * 60)

    print("\n  Detalle por fuente:")
    print("  " + "-" * 55)

    for r in results:

        status_icon = "✓" if r.get("status") == "OK" else "✗"

        if r.get("status") == "OK":

            print(
                f"  {status_icon} {r['source']:<25} "
                f"válidos={r.get('total_valid', 0):>5}  "
                f"descart.={r.get('total_discarded', 0):>4}"
            )

        else:

            print(
                f"  {status_icon} {r['source']:<25} "
                f"ERROR: {r.get('error', 'desconocido')}"
            )

    print()


# -----------------------------------------------------------------------
# REPORTES
# -----------------------------------------------------------------------
def save_execution_report(results: list[dict], log_dir: str):
    """Guarda el reporte de ejecución como JSON."""

    Path(log_dir).mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(CR_TZ).strftime("%Y%m%d_%H%M%S")

    report_path = os.path.join(
        log_dir,
        f"execution_report_{date_str}.json"
    )

    report = {
        "execution_date": datetime.now(CR_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "scrapers_run": len(results),
        "results": results,
    }

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"  Reporte guardado: {report_path}")


# -----------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------
def main():

    print_banner()

    parser = argparse.ArgumentParser(
        description="Orquestador de scrapers — Observatorio Democrático"
    )

    parser.add_argument(
        "--only",
        type=str,
        nargs="+",
        metavar="SCRAPER",
        help="Ejecutar solo scrapers específicos"
    )

    parser.add_argument(
        "--list",
        action="store_true",
        help="Listar scrapers disponibles"
    )

    parser.add_argument(
        "--output",
        type=str,
        default="output",
        help="Directorio de salida"
    )

    parser.add_argument(
        "--logs",
        type=str,
        default="logs",
        help="Directorio de logs"
    )

    parser.add_argument(
        "--test",
        action="store_true",
        help="Modo prueba"
    )

    args = parser.parse_args()

    # -------------------------------------------------
    # LISTAR SCRAPERS
    # -------------------------------------------------
    if args.list:

        print("Scrapers disponibles:")

        for name in SCRAPERS_REGISTRY:

            module, cls = SCRAPERS_REGISTRY[name]

            print(
                f"  - {name:<25} ({module}.{cls})"
            )

        print("\nScrapers legacy (vía LegacyScraperAdapter):")

        legacy_registry = get_legacy_registry()

        for name in sorted(LEGACY_SCRAPERS):

            print(
                f"  - {name:<25} ({legacy_registry[name]})"
            )

        sys.exit(0)

    # -------------------------------------------------
    # DETERMINAR SCRAPERS A EJECUTAR
    # -------------------------------------------------
    if args.only:

        to_run = []

        for name in args.only:

            if name not in SCRAPERS_REGISTRY and name not in LEGACY_SCRAPERS:

                print(
                    f"  ADVERTENCIA: Scraper '{name}' no encontrado."
                )

            else:

                to_run.append(name)

        if not to_run:

            print("  No hay scrapers válidos.")
            sys.exit(1)

    else:

        to_run = list(SCRAPERS_REGISTRY.keys()) + list(LEGACY_SCRAPERS)

    print(f"  Scrapers a ejecutar: {', '.join(to_run)}")
    print(f"  Modo prueba        : {'SÍ' if args.test else 'No'}")
    print(f"  Directorio salida  : {args.output}/")
    print(f"  Directorio logs    : {args.logs}/\n")

    start_time = datetime.now(CR_TZ)

    results = []

    # -------------------------------------------------
    # EJECUTAR SCRAPERS
    # -------------------------------------------------
    for i, name in enumerate(to_run):

        print(f"  [{i+1}/{len(to_run)}] Iniciando: {name}")

        try:
            result = run_scraper(
                name,
                output_dir=args.output,
                log_dir=args.logs,
                test_mode=args.test
            )
        except Exception as e:
            result = {
                "source": name,
                "status": "ERROR",
                "error": str(e)
            }

        results.append(result)

        if result.get("status") == "OK":

            print(
                f"        → {result.get('total_valid', 0)} artículos válidos, "
                f"{result.get('total_discarded', 0)} descartados"
            )

        else:

            print(
                f"        → ERROR: {result.get('error', 'desconocido')}"
            )

    # -------------------------------------------------
    # RESUMEN FINAL
    # -------------------------------------------------
    print_summary(results, start_time)

    save_execution_report(results, args.logs)


if __name__ == "__main__":
    main()
