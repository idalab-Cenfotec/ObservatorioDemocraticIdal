#!/usr/bin/env python
"""
corpus_consolidator.py
Reconstruye el corpus consolidado del Observatorio (Fase 1) a partir de:

  1) uno o mas corpus BASE ya limpios (p. ej. corpus_observatorio_v7_clean_20260703.csv), y
  2) las versiones diarias que deja el pipeline, corpus_observatorio_v{N}_{YYYYMMDD}.csv,
     en un directorio o dentro de un .zip (el artefacto "corpus-maestro" de GitHub Actions).

Por que hace falta
------------------
Las versiones diarias del pipeline NO son acumulativas: corpus_updater.py toma como
"ultimo corpus" el primer nombre en orden alfabetico inverso, y
corpus_observatorio_v5_20260702.csv le gana a corpus_observatorio_v1_20260905.csv, asi que
cada dia parte de la misma base del 2 de julio. El corpus completo es la UNION de todos
los archivos, y ademas hay articulos (p. ej. adiariocr, tvsur) que solo existen en la base.

Que hace
--------
  1. Une base(s) + versiones del pipeline, sin repetir articulos. Dos URL son la misma si
     coinciden tras normalizar (sin http/https, sin www, sin '#fragmento', sin '/' final,
     host en minusculas). Prioridad: primero las bases, en el orden dado; luego el
     pipeline de la version mas antigua a la mas reciente (gana la primera aparicion).
  2. Guarda el resultado bruto.
  3. Le aplica output_cleaner.fix_file (fechas, HTML, secciones, filas sin titulo, duplicados).
  4. Valida el resultado y deja un reporte JSON.

Salidas (en --output, por defecto corpus_consolidado/):
  corpus_consolidado_bruto_<fecha>.csv     union sin limpiar
  corpus_consolidado_clean_<fecha>.csv     corpus limpio: el que se carga a PostgreSQL
  fechas_inferidas_<fecha>.csv             URLs cuya publication_date se reemplazo por
                                           scraping_date (marcar como date_source='inferido')
  consolidacion_reporte_<fecha>.json       cifras de la consolidacion

Ejemplos:
  python corpus_consolidator.py --base corpus_observatorio_v7_clean_20260703.csv \\
         --pipeline corpus-maestro.zip
  python corpus_consolidator.py --base v7_clean.csv --pipeline corpus/ --output salida/ --dry-run

Nota: los CSV consolidados pesan cientos de MB; no se suben al repositorio (*.csv esta
en .gitignore). Se comparten por el almacenamiento institucional.
"""

import argparse
import io
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd

from output_cleaner import REQUIRED_COLUMNS, fix_file, validate_file

SEPARATOR = "|"
PIPELINE_NAME_RE = re.compile(r"^corpus_observatorio_v(?P<ver>\d+)_(?P<fecha>\d{8})\.csv$")
READ_KWARGS = dict(
    sep=SEPARATOR, dtype=str, keep_default_na=False, na_values=["NULL"],
    on_bad_lines="skip", encoding="utf-8-sig",
)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def normalize_url(url: str) -> str:
    """Clave de comparacion de URLs (no altera la URL guardada)."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}"


def _log(msg: str):
    print(f"  {msg}", flush=True)


def _read_frame(source, label: str) -> pd.DataFrame:
    df = pd.read_csv(source, **READ_KWARGS)
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[REQUIRED_COLUMNS]
    return df[df["url"].notna() & (df["url"].str.strip() != "")]


def list_pipeline_files(pipeline: Path) -> list[tuple[str, str, int]]:
    """Devuelve [(nombre, fecha, version)] ordenado de mas antiguo a mas reciente."""
    if pipeline.is_dir():
        names = [p.name for p in pipeline.glob("corpus_observatorio_v*.csv")]
    elif zipfile.is_zipfile(pipeline):
        with zipfile.ZipFile(pipeline) as z:
            names = [Path(i.filename).name for i in z.infolist() if i.filename.endswith(".csv")]
    else:
        raise SystemExit(f"ERROR: --pipeline debe ser un directorio o un .zip: {pipeline}")
    files = []
    for n in names:
        m = PIPELINE_NAME_RE.match(n)
        if m:
            files.append((n, m.group("fecha"), int(m.group("ver"))))
    files.sort(key=lambda t: (t[1], t[2]))
    return files


def open_pipeline_file(pipeline: Path, name: str):
    if pipeline.is_dir():
        return pipeline / name
    z = zipfile.ZipFile(pipeline)
    member = next(i.filename for i in z.infolist() if Path(i.filename).name == name)
    return io.BytesIO(z.read(member))


# ---------------------------------------------------------------------------
# Consolidacion
# ---------------------------------------------------------------------------

def consolidate(bases: list[Path], pipeline: Path) -> tuple[pd.DataFrame, dict]:
    seen: set[str] = set()
    frames: list[pd.DataFrame] = []
    info: dict = {"bases": [], "pipeline_files": 0, "pipeline_new": {}}

    for base in bases:
        _log(f"Base: {base.name}")
        df = _read_frame(base, base.name)
        keys = df["url"].map(normalize_url)
        mask = ~keys.isin(seen) & ~keys.duplicated()
        frames.append(df[mask])
        seen |= set(keys[mask])
        info["bases"].append({"archivo": base.name, "filas_leidas": len(df), "filas_aportadas": int(mask.sum())})
        _log(f"  {len(df):,} filas leidas, {int(mask.sum()):,} aportadas")

    files = list_pipeline_files(pipeline)
    if not files:
        raise SystemExit(f"ERROR: no se encontraron archivos corpus_observatorio_v*_YYYYMMDD.csv en {pipeline}")
    info["pipeline_files"] = len(files)
    info["fecha_pipeline_mas_reciente"] = files[-1][1]
    _log(f"Pipeline: {len(files)} archivos, del {files[0][1]} al {files[-1][1]}")

    for name, _fecha, _ver in files:
        df = _read_frame(open_pipeline_file(pipeline, name), name)
        keys = df["url"].map(normalize_url)
        mask = ~keys.isin(seen) & ~keys.duplicated()
        n_new = int(mask.sum())
        if n_new:
            frames.append(df[mask])
            seen |= set(keys[mask])
            info["pipeline_new"][name] = n_new
        _log(f"  {name}: +{n_new:,} (acumulado {len(seen):,})")

    return pd.concat(frames, ignore_index=True), info


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Consolida corpus base + versiones diarias del pipeline en un unico corpus limpio."
    )
    parser.add_argument("--base", action="append", required=True, type=Path,
                        help="Corpus base ya limpio (repetible; se respeta el orden de prioridad)")
    parser.add_argument("--pipeline", required=True, type=Path,
                        help="Directorio o .zip con corpus_observatorio_v{N}_{YYYYMMDD}.csv")
    parser.add_argument("--output", type=Path, default=Path("corpus_consolidado"),
                        help="Directorio de salida (por defecto: corpus_consolidado/)")
    parser.add_argument("--fecha", type=str, default=None,
                        help="Sufijo YYYYMMDD de los archivos de salida (por defecto: la fecha del "
                             "archivo mas reciente del pipeline)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo cuenta lo que se consolidaria; no escribe archivos")
    args = parser.parse_args()

    for b in args.base:
        if not b.is_file():
            sys.exit(f"ERROR: base no encontrada: {b}")
    if not args.pipeline.exists():
        sys.exit(f"ERROR: pipeline no encontrado: {args.pipeline}")

    print("Consolidando corpus...")
    bruto, info = consolidate(args.base, args.pipeline)
    fecha = args.fecha or info["fecha_pipeline_mas_reciente"]

    print(f"\nTotal bruto (URLs unicas): {len(bruto):,}")
    if args.dry_run:
        print("Modo dry-run: no se escribio ningun archivo.")
        return

    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    bruto_path = out / f"corpus_consolidado_bruto_{fecha}.csv"
    clean_path = out / f"corpus_consolidado_clean_{fecha}.csv"
    inferred_path = out / f"fechas_inferidas_{fecha}.csv"

    bruto.to_csv(bruto_path, sep=SEPARATOR, index=False, na_rep="NULL", encoding="utf-8")
    _log(f"Bruto guardado: {bruto_path}")

    stats = fix_file(bruto_path, clean_path, inferred_out=inferred_path)
    report_val = validate_file(clean_path)

    clean = pd.read_csv(clean_path, **READ_KWARGS)
    reporte = {
        "generado": datetime.now().isoformat(timespec="seconds"),
        "fecha_archivos": fecha,
        "entradas": info,
        "filas_bruto": len(bruto),
        "limpieza": stats,
        "filas_finales": len(clean),
        "urls_unicas_finales": int(clean["url"].nunique()),
        "fuentes": int(clean["source"].nunique()),
        "articulos_por_fuente": clean["source"].value_counts().to_dict(),
        "validacion": {"ok": report_val["ok"], "issues": report_val["issues"]},
    }
    report_path = out / f"consolidacion_reporte_{fecha}.json"
    report_path.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print("CONSOLIDACION TERMINADA")
    print("=" * 60)
    print(f"  Filas bruto:                {len(bruto):>9,}")
    print(f"  Descartadas (sin titulo):   {stats['dropped_no_title']:>9,}")
    print(f"  Duplicados URL eliminados:  {stats['deduped']:>9,}")
    print(f"  FILAS FINALES:              {len(clean):>9,}   ({reporte['fuentes']} fuentes)")
    print(f"  Fechas inferidas:           {stats['pub_date_inferred']:>9,}   -> {inferred_path.name}")
    print(f"  Validacion del limpio:      {'OK' if report_val['ok'] else 'CON OBSERVACIONES'}")
    for issue in report_val["issues"]:
        print(f"    - {issue['type']} [{issue['value']}]: {issue['details']}")
    print(f"  Reporte: {report_path}")


if __name__ == "__main__":
    main()
