"""
pipeline_runner.py
──────────────────
Orquestador paralelo del Observatorio Democrático v2.1

  1. Health check liviano antes de cada scraper
  2. Ejecución paralela controlada (semáforo asyncio)
  3. Reintentos automáticos con backoff exponencial
  4. Estado persistente en SQLite (execution_state.db)
  5. Detección de anomalías comparando contra historial
  6. Modo incremental — omite scrapers que ya corrieron exitosamente hoy
  7. Tabla de resultados detallada con tipos de error entendibles
  8. Fix: output_dir se resuelve como ruta absoluta para evitar output/output/

Uso:
    python pipeline_runner.py                           # todos los scrapers
    python pipeline_runner.py --only teletica repretel  # específicos
    python pipeline_runner.py --test                    # modo prueba
    python pipeline_runner.py --workers 5               # concurrencia
    python pipeline_runner.py --skip-health             # omitir health check
    python pipeline_runner.py --incremental             # solo los que faltan
    python pipeline_runner.py --status                  # ver estado actual
"""

import asyncio
import argparse
import sys
import os
import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from execution_state import (
    init_db, record_execution, check_anomaly,
    get_last_run, print_summary as print_state_summary,
)
from health_check import check_one as health_check_one
from scrapers_registry import SCRAPERS_REGISTRY, LEGACY_SCRAPERS, PAUSED_SCRAPERS, get_scraper_instance

CR_TZ           = timezone(timedelta(hours=-6))
DEFAULT_WORKERS = 5
MAX_RETRIES     = 2
RETRY_DELAY_S   = 15


# ─────────────────────────────────────────────────────────────────────────────
# CLASIFICACIÓN DE ERRORES
# ─────────────────────────────────────────────────────────────────────────────
def _classify_error(error_msg: str, tb: str = "") -> tuple[str, str]:
    if not error_msg:
        return "DESCONOCIDO", "Error sin mensaje"

    msg = (error_msg + " " + (tb or "")).lower()

    if "timeout" in msg or "timed out" in msg:
        return "TIMEOUT", "El sitio tardó demasiado en responder"
    if "connectionerror" in msg or "connection refused" in msg:
        return "CONEXIÓN", "No se pudo conectar al sitio"
    if "dns" in msg or "name or service not known" in msg:
        return "DNS", "El dominio no resuelve — sitio fuera de línea"
    if "ssl" in msg or "certificate" in msg:
        return "SSL", "Error de certificado de seguridad"
    if "importerror" in msg or "modulenotfounderror" in msg:
        return "IMPORTACIÓN", "Error cargando el módulo del scraper"
    if "attributeerror" in msg:
        return "SELECTOR", "HTML del sitio cambió — selector no encontrado"
    if "playwright" in msg and ("browser" in msg or "chromium" in msg):
        return "PLAYWRIGHT", "Error iniciando el navegador"
    if "permissionerror" in msg or "access is denied" in msg:
        return "PERMISOS", "Sin permisos para escribir en el directorio"
    if "memoryerror" in msg:
        return "MEMORIA", "Sin RAM suficiente — reducir --workers"
    if "429" in msg or "rate limit" in msg:
        return "RATE LIMIT", "Sitio bloqueó temporalmente las peticiones"
    if "403" in msg or "forbidden" in msg:
        return "BLOQUEADO", "Sitio rechazó el acceso — posible bot detection"
    if "404" in msg:
        return "URL INVÁLIDA", "La URL del scraper ya no existe"
    if "zerodivision" in msg or "indexerror" in msg or "keyerror" in msg:
        return "CÓDIGO", "Error interno — estructura del sitio cambió"

    return "ERROR GENERAL", error_msg[:120]


# ─────────────────────────────────────────────────────────────────────────────
# EJECUCIÓN SÍNCRONA DE UN SCRAPER
# ─────────────────────────────────────────────────────────────────────────────
def _run_scraper_sync(
    name:       str,
    output_dir: str,   # ya viene como ruta absoluta
    log_dir:    str,   # ya viene como ruta absoluta
    test_mode:  bool,
) -> dict:
    if name not in SCRAPERS_REGISTRY and name not in LEGACY_SCRAPERS:
        return {
            "source": name, "status": "ERROR",
            "error": f"'{name}' no encontrado en SCRAPERS_REGISTRY ni LEGACY_SCRAPERS",
            "duration_s": 0, "total_valid": 0, "total_discarded": 0,
        }

    t0 = time.time()
    try:
        # Usar rutas absolutas — evita el bug output/output/
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        Path(log_dir).mkdir(parents=True, exist_ok=True)

        scraper = get_scraper_instance(
            name=name,
            output_dir=output_dir,
            log_dir=log_dir,
            test_mode=test_mode
        )

        result               = scraper.run()
        result["duration_s"] = round(time.time() - t0, 2)
        return result

    except Exception as exc:
        return {
            "source":          name,
            "status":          "ERROR",
            "error":           str(exc),
            "traceback":       traceback.format_exc(),
            "duration_s":      round(time.time() - t0, 2),
            "total_valid":     0,
            "total_discarded": 0,
        }


# ─────────────────────────────────────────────────────────────────────────────
# WORKER ASYNC
# ─────────────────────────────────────────────────────────────────────────────
async def _worker(
    name, semaphore, executor, output_dir,
    log_dir, test_mode, skip_health, incremental, loop,
) -> dict:

    async with semaphore:
        ts_start = time.time()
        today    = datetime.now(CR_TZ).strftime("%Y-%m-%d")

        # ── 0. Incremental ───────────────────────────────────────────────────
        if incremental:
            last = get_last_run(name)
            if last and last["status"] == "OK" and last["run_date"] == today:
                _log(f"⊘  [{name}] ya corrió hoy ({last['total_valid']} artículos) — saltando")
                return {
                    "source": name, "status": "SKIPPED_INCREMENTAL",
                    "total_valid": last["total_valid"],
                    "total_discarded": last["total_discarded"],
                    "duration_s": 0, "health_ok": True,
                    "reason": "ya corrió exitosamente hoy",
                }

        _log(f"▶  [{name}] iniciando")

        # ── 1. Health check ──────────────────────────────────────────────────
        health_ok = True
        if not skip_health:
            hc        = await loop.run_in_executor(executor, health_check_one, name)
            health_ok = hc["ok"]
            if not health_ok:
                _log(f"✗  [{name}] health check FALLÓ — {hc['reason']}")
                record_execution(
                    source=name, status="SKIPPED",
                    health_ok=False, error_msg=hc["reason"],
                )
                return {
                    "source": name, "status": "SKIPPED",
                    "error": hc["reason"], "error_type": "SITIO CAÍDO",
                    "error_desc": hc["reason"],
                    "total_valid": 0, "total_discarded": 0,
                    "duration_s": round(time.time() - ts_start, 2),
                    "health_ok": False,
                }
            if hc.get("slow"):
                _log(f"⚠  [{name}] sitio lento ({hc['duration_s']:.1f}s) — continuando")

        # ── 2. Ejecución con reintentos ──────────────────────────────────────
        result  = None
        attempt = 0
        while attempt <= MAX_RETRIES:
            if attempt > 0:
                delay = RETRY_DELAY_S * attempt
                _log(f"↺  [{name}] reintento {attempt}/{MAX_RETRIES} en {delay}s...")
                await asyncio.sleep(delay)

            result = await loop.run_in_executor(
                executor, _run_scraper_sync,
                name, output_dir, log_dir, test_mode,
            )
            if result.get("status") == "OK":
                break
            attempt += 1

        result["health_ok"] = health_ok

        # ── 3. Clasificar error ──────────────────────────────────────────────
        if result.get("status") != "OK":
            err_type, err_desc = _classify_error(
                result.get("error", ""), result.get("traceback", "")
            )
            result["error_type"] = err_type
            result["error_desc"] = err_desc

        # ── 4. Registrar en SQLite ───────────────────────────────────────────
        valid     = result.get("total_valid", 0)
        discarded = result.get("total_discarded", 0)
        duration  = result.get("duration_s", round(time.time() - ts_start, 2))
        status    = result.get("status", "ERROR")
        err_msg   = result.get("error") if status != "OK" else None

        record_execution(
            source=name, status=status,
            total_valid=valid, total_discarded=discarded,
            duration_s=duration, health_ok=health_ok,
            error_msg=err_msg,
        )

        # ── 5. Anomalías ─────────────────────────────────────────────────────
        if status == "OK":
            anomaly = check_anomaly(name, valid)
            if anomaly["anomaly"]:
                _log(f"⚠  [{name}] ANOMALÍA [{anomaly['level'].upper()}]: {anomaly['reason']}")
                result["anomaly"] = anomaly
            else:
                result["anomaly"] = None
            _log(f"✓  [{name}] {valid} artículos en {duration:.1f}s")
        else:
            _log(f"✗  [{name}] {result.get('error_type','ERROR')}: {result.get('error_desc','')[:60]}")

        return result


# ─────────────────────────────────────────────────────────────────────────────
# ORQUESTADOR
# ─────────────────────────────────────────────────────────────────────────────
async def run_pipeline(
    to_run, output_dir, log_dir,
    test_mode=False, workers=DEFAULT_WORKERS,
    skip_health=False, incremental=False,
) -> list[dict]:

    init_db()
    semaphore = asyncio.Semaphore(workers)
    loop      = asyncio.get_event_loop()

    _log(f"Pipeline — {len(to_run)} scrapers — {workers} workers — {'incremental' if incremental else 'completo'}")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        tasks = [
            _worker(
                name=name, semaphore=semaphore, executor=executor,
                output_dir=output_dir, log_dir=log_dir,
                test_mode=test_mode, skip_health=skip_health,
                incremental=incremental, loop=loop,
            )
            for name in to_run
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)

    return list(results)


# ─────────────────────────────────────────────────────────────────────────────
# TABLA DE RESULTADOS
# ─────────────────────────────────────────────────────────────────────────────
def _print_pipeline_summary(results: list[dict], elapsed: float):
    paused      = [r for r in results if r.get("status") == "PAUSADO"]
    results     = [r for r in results if r.get("status") != "PAUSADO"]
    ok          = [r for r in results if r.get("status") == "OK"]
    errors      = [r for r in results if r.get("status") == "ERROR"]
    skipped_hc  = [r for r in results if r.get("status") == "SKIPPED"]
    skipped_inc = [r for r in results if r.get("status") == "SKIPPED_INCREMENTAL"]
    anomaly     = [r for r in ok if r.get("anomaly")]

    total_valid     = sum(r.get("total_valid", 0) for r in ok)
    total_discarded = sum(r.get("total_discarded", 0) for r in ok)
    total_valid_inc = sum(r.get("total_valid", 0) for r in skipped_inc)

    print(f"\n{'═'*70}")
    print("  RESUMEN PIPELINE")
    print(f"{'═'*70}")
    print(f"  Total scrapers         : {len(results)}")
    print(f"  ✓ Exitosos             : {len(ok)}")
    print(f"  ⊘ Ya tenían datos hoy  : {len(skipped_inc)}")
    print(f"  ✗ Con error            : {len(errors)}")
    print(f"  ⊘ Omitidos (salud)     : {len(skipped_hc)}")
    print(f"  ⏸ Pausadas             : {len(paused)}")
    print(f"  ⚠ Con anomalía         : {len(anomaly)}")
    print(f"  {'─'*55}")
    print(f"  Artículos esta corrida : {total_valid:,}")
    if skipped_inc:
        print(f"  Artículos prev. hoy    : {total_valid_inc:,}")
        print(f"  Total del día          : {(total_valid + total_valid_inc):,}")
    print(f"  Descartados            : {total_discarded:,}")
    print(f"  Tiempo total           : {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"{'═'*70}")

    # Tabla exitosos
    if ok:
        print(f"\n  {'SCRAPER':<25} {'ARTÍCULOS':>10} {'DESCART.':>9} {'TIEMPO':>8}")
        print(f"  {'─'*55}")
        for r in sorted(ok, key=lambda x: x.get("total_valid", 0), reverse=True):
            dur  = f"{r.get('duration_s', 0):.0f}s"
            anom = " ⚠" if r.get("anomaly") else ""
            print(
                f"  ✓ {r['source']:<23} "
                f"{r.get('total_valid', 0):>10,} "
                f"{r.get('total_discarded', 0):>9,} "
                f"{dur:>8}{anom}"
            )

    # Tabla errores con descripción
    if errors or skipped_hc:
        print(f"\n  {'SCRAPER':<25} {'TIPO':<16} {'DESCRIPCIÓN'}")
        print(f"  {'─'*70}")
        for r in errors:
            etype = r.get("error_type", "ERROR")[:14]
            edesc = r.get("error_desc", r.get("error", ""))[:45]
            print(f"  ✗ {r['source']:<23} {etype:<16} {edesc}")
        for r in skipped_hc:
            edesc = r.get("error", "")[:45]
            print(f"  ⊘ {r['source']:<23} {'SITIO CAÍDO':<16} {edesc}")

    if paused:
        print("\n  PAUSADAS (no corren hasta resolver el motivo):")
        for r in paused:
            print(f"  ⏸ {r['source']:<23} {r.get('reason','')[:70]}")

    # Anomalías
    if anomaly:
        print(f"\n  ANOMALÍAS:")
        for r in anomaly:
            a = r["anomaly"]
            print(f"  ⚠ {r['source']:<23} [{a['level'].upper()}] {a['reason']}")

    print()


def _save_report(results: list[dict], log_dir: str):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    ts   = datetime.now(CR_TZ).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(log_dir, f"pipeline_report_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "run_ts":  datetime.now(CR_TZ).isoformat(),
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    _log(f"Reporte guardado: {path}")


def _log(msg: str):
    print(f"  [{datetime.now(CR_TZ).strftime('%H:%M:%S')}] {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║      OBSERVATORIO DEMOCRÁTICO — Pipeline Paralelo v2.1       ║
╚══════════════════════════════════════════════════════════════╝
""")

    parser = argparse.ArgumentParser()
    parser.add_argument("--only",        nargs="+", metavar="SCRAPER")
    parser.add_argument("--output",      default="output")
    parser.add_argument("--logs",        default="logs")
    parser.add_argument("--test",        action="store_true")
    parser.add_argument("--workers",     type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--skip-health", action="store_true")
    parser.add_argument("--incremental", action="store_true",
                        help="Saltar scrapers que ya corrieron exitosamente hoy")
    parser.add_argument("--status",      action="store_true",
                        help="Ver estado actual de todos los scrapers")

    args = parser.parse_args()

    # Resolver rutas absolutas desde el directorio del script
    # Esto evita el bug output/output/ sin importar desde dónde se ejecute
    base_dir   = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(base_dir, args.output)
    log_dir    = os.path.join(base_dir, args.logs)

    if args.status:
        print_state_summary()
        sys.exit(0)

    if args.only:
        all_known = set(SCRAPERS_REGISTRY.keys()) | LEGACY_SCRAPERS
        to_run  = [n for n in args.only if n in all_known]
        missing = [n for n in args.only if n not in all_known]
        if missing:
            print(f"  ADVERTENCIA: no encontrados → {', '.join(missing)}")
        if not to_run:
            print("  Sin scrapers válidos.")
            sys.exit(1)
    else:
        # Todos los scrapers: modernos + legacy
        to_run = list(SCRAPERS_REGISTRY.keys()) + list(LEGACY_SCRAPERS)

    # Las fuentes pausadas no se ejecutan: quedan en el reporte como PAUSADO, no como error.
    paused_results = [
        {"source": n, "status": "PAUSADO", "reason": PAUSED_SCRAPERS[n], "total_valid": 0, "total_discarded": 0}
        for n in to_run if n in PAUSED_SCRAPERS
    ]
    to_run = [n for n in to_run if n not in PAUSED_SCRAPERS]
    if not to_run and not paused_results:
        print("  Sin scrapers válidos.")
        sys.exit(1)

    print(f"  Scrapers a ejecutar : {len(to_run)}  (pausados omitidos: {len(paused_results)})")
    print(f"  Workers paralelos   : {args.workers}")
    print(f"  Modo prueba         : {'SÍ' if args.test else 'No'}")
    print(f"  Health check        : {'No' if args.skip_health else 'SÍ'}")
    print(f"  Incremental         : {'SÍ' if args.incremental else 'No'}")
    print(f"  Output              : {output_dir}")
    print(f"  Logs                : {log_dir}\n")

    t0      = time.time()
    results = asyncio.run(run_pipeline(
        to_run      = to_run,
        output_dir  = output_dir,
        log_dir     = log_dir,
        test_mode   = args.test,
        workers     = args.workers,
        skip_health = args.skip_health,
        incremental = args.incremental,
    ))

    elapsed = time.time() - t0
    results = results + paused_results
    _print_pipeline_summary(results, elapsed)
    _save_report(results, log_dir)

    # Código de salida distinto de cero si algún scraper falló: el job queda en rojo y el error se ve
    # (antes siempre salía en verde y las fuentes rotas pasaban días sin que nadie lo notara).
    fallidos = [r["source"] for r in results if r.get("status") == "ERROR"]
    if fallidos:
        print(f"  ✗ Scrapers con error: {', '.join(fallidos)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
