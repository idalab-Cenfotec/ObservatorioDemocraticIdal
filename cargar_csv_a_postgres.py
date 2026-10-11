#!/usr/bin/env python3
"""Carga a PostgreSQL (vía el webhook de N8N) uno o varios CSV de scrapers que no pasaron por el pipeline diario:
descargas históricas locales, rescates, recuperaciones de un sitio.

Aplica los mismos filtros que BaseScraper (texto de al menos 300 caracteres, con fecha) y el envío usa la misma
limpieza que la carga diaria (fechas, HTML, relleno de los sitios, idioma, publicidad de apuestas). Es seguro
repetirlo: los índices únicos de la base descartan lo que ya existe.

    python cargar_csv_a_postgres.py output/periodicomensaje_20261011.csv --dry-run
    python cargar_csv_a_postgres.py output/*.csv          # N8N_WEBHOOK_URL y N8N_WEBHOOK_TOKEN en el entorno
"""
import argparse
import glob
import os
import sys

import pandas as pd

from corpus_updater import SEPARATOR, enviar_a_n8n
from output_cleaner import REQUIRED_COLUMNS

MIN_TEXT_LENGTH = 300
SIN_FECHA = {"", "N/A", "Sin fecha", "NULL", "nan", "None"}


def preparar(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Filtra las filas que BaseScraper habría descartado y deja solo las columnas del esquema."""
    stats = {"leidas": len(df)}
    faltan = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if faltan:
        raise ValueError(f"al CSV le faltan columnas: {faltan}")
    df = df.drop_duplicates(subset="url")
    stats["sin_url_repetida"] = len(df)
    corto = df["full_text"].fillna("").str.len() < MIN_TEXT_LENGTH
    sin_fecha = df["publication_date"].fillna("").isin(SIN_FECHA)
    stats["descartadas_texto_corto"] = int(corto.sum())
    stats["descartadas_sin_fecha"] = int((sin_fecha & ~corto).sum())
    df = df[~corto & ~sin_fecha]
    stats["a_enviar"] = len(df)
    return df[REQUIRED_COLUMNS], stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archivos", nargs="+", help="CSV separados por | con el esquema del pipeline")
    ap.add_argument("--dry-run", action="store_true", help="solo cuenta, no envía nada")
    ap.add_argument("--webhook-url", default=os.environ.get("N8N_WEBHOOK_URL"))
    ap.add_argument("--webhook-token", default=os.environ.get("N8N_WEBHOOK_TOKEN"))
    a = ap.parse_args()

    rutas = sorted({r for patron in a.archivos for r in glob.glob(patron)})
    if not rutas:
        print("Ningún archivo coincide", file=sys.stderr)
        return 2
    if not a.dry_run and not (a.webhook_url and a.webhook_token):
        print("Faltan N8N_WEBHOOK_URL / N8N_WEBHOOK_TOKEN", file=sys.stderr)
        return 2

    total = {"recibidos": 0, "insertados": 0, "ya_existian": 0, "rechazados": 0}
    for ruta in rutas:
        df = pd.read_csv(ruta, sep=SEPARATOR, dtype=str, keep_default_na=False, encoding="utf-8-sig", on_bad_lines="skip")
        if df.empty:
            print(f"{os.path.basename(ruta)}: sin filas")
            continue
        df, st = preparar(df)
        print(f"{os.path.basename(ruta)}: {st}")
        if a.dry_run or df.empty:
            continue
        t = enviar_a_n8n(df, a.webhook_url, a.webhook_token)
        for k in total:
            total[k] += t.get(k, 0)
        print(f"   -> N8N: {t}")
    if not a.dry_run:
        print("TOTAL:", total)
        if total["recibidos"] != total["insertados"] + total["ya_existian"] + total["rechazados"]:
            print("ERROR: los conteos de N8N no cuadran", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
