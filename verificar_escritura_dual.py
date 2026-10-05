#!/usr/bin/env python3
"""Verificación diaria de la escritura dual CSV + PostgreSQL (SCRUM-28).

Corre al final del pipeline, después de que corpus_updater.py / corpus_builder.py
actualizaron el corpus CSV y lo enviaron a PostgreSQL por el webhook de N8N.
Compara lo que esta corrida aportó al corpus CSV contra lo que hay en la base:

  1. Toma las URLs de los CSV del día (output/*.csv) que quedaron en el corpus
     CSV más reciente (corpus/corpus_observatorio_v*.csv), es decir, lo que el
     CSV aceptó tras la limpieza.
  2. Pide a N8N (GET ?source=<fuente>, el mismo endpoint del modo incremental)
     las URLs que ya están en PostgreSQL.
  3. Cualquier URL del paso 1 que no esté en PostgreSQL es una desviación de la
     escritura dual. También se reportan los lotes pendientes en contingencia/.

Sale con código 1 si hay desviación (el job queda en rojo y es la alerta), y deja
un JSON con el detalle y un resumen en Markdown (GITHUB_STEP_SUMMARY). Los JSON
diarios son la evidencia del período de operación en escritura dual.

    python verificar_escritura_dual.py --output output/ --corpus corpus/ --salida logs/
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from output_cleaner import url_key

SEPARATOR = "|"


def _corpus_mas_reciente(corpus_dir: Path) -> Path | None:
    candidatos = sorted(corpus_dir.glob("corpus_observatorio_v*.csv"), key=lambda p: p.stat().st_mtime)
    return candidatos[-1] if candidatos else None


def urls_del_dia(output_dir: Path) -> dict[str, set[str]]:
    """{fuente: {url_key}} con lo que scrapearon los grupos hoy."""
    por_fuente: dict[str, set[str]] = {}
    for f in sorted(output_dir.glob("*.csv")):
        try:
            df = pd.read_csv(f, sep=SEPARATOR, dtype=str, usecols=["source", "url"],
                             on_bad_lines="skip", encoding="utf-8-sig")
        except (ValueError, pd.errors.EmptyDataError):
            continue
        for fuente, url in zip(df["source"], df["url"]):
            if isinstance(url, str) and isinstance(fuente, str):
                por_fuente.setdefault(fuente, set()).add(url_key(url))
    return por_fuente


def urls_aceptadas_por_el_corpus(corpus_csv: Path, del_dia: dict[str, set[str]]) -> dict[str, set[str]]:
    """Recorta lo del día a lo que sobrevivió a la limpieza del corpus CSV."""
    todas = set().union(*del_dia.values()) if del_dia else set()
    aceptadas: dict[str, set[str]] = {}
    for chunk in pd.read_csv(corpus_csv, sep=SEPARATOR, dtype=str, usecols=["source", "url"],
                             on_bad_lines="skip", encoding="utf-8-sig", chunksize=100_000):
        for fuente, url in zip(chunk["source"], chunk["url"]):
            if not isinstance(url, str):
                continue
            k = url_key(url)
            if k in todas and k in del_dia.get(fuente, ()):
                aceptadas.setdefault(fuente, set()).add(k)
    return aceptadas


def urls_en_postgres(fuente: str, url: str, token: str) -> set[str]:
    r = requests.get(url, params={"source": fuente}, headers={"X-Webhook-Token": token}, timeout=(10, 120))
    r.raise_for_status()
    return set(r.json()["urls"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", default="output")
    ap.add_argument("--corpus", default="corpus")
    ap.add_argument("--contingencia", default="contingencia")
    ap.add_argument("--salida", default="logs")
    ap.add_argument("--urls-conocidas", default=os.environ.get("N8N_URLS_CONOCIDAS_URL"))
    ap.add_argument("--token", default=os.environ.get("N8N_WEBHOOK_TOKEN"))
    a = ap.parse_args()

    if not a.urls_conocidas or not a.token:
        print("Faltan N8N_URLS_CONOCIDAS_URL / N8N_WEBHOOK_TOKEN: no se puede verificar", file=sys.stderr)
        return 2

    corpus_csv = _corpus_mas_reciente(Path(a.corpus))
    if corpus_csv is None:
        print("No hay corpus CSV: nada que verificar", file=sys.stderr)
        return 2

    del_dia = urls_del_dia(Path(a.output))
    aceptadas = urls_aceptadas_por_el_corpus(corpus_csv, del_dia)
    pendientes_contingencia = sorted(p.name for p in Path(a.contingencia).glob("*.csv")) if Path(a.contingencia).is_dir() else []

    filas, faltan_total = [], 0
    for fuente in sorted(aceptadas):
        claves = aceptadas[fuente]
        try:
            en_db = urls_en_postgres(fuente, a.urls_conocidas, a.token)
        except Exception as e:  # N8N caído: es una desviación que hay que ver, no un silencio
            filas.append({"fuente": fuente, "en_corpus_csv": len(claves), "en_postgres": None,
                          "faltan": len(claves), "error": str(e), "ejemplos": []})
            faltan_total += len(claves)
            continue
        faltan = sorted(claves - en_db)
        faltan_total += len(faltan)
        filas.append({"fuente": fuente, "en_corpus_csv": len(claves), "en_postgres": len(claves) - len(faltan),
                      "faltan": len(faltan), "error": None, "ejemplos": faltan[:3]})

    ok = faltan_total == 0 and not pendientes_contingencia
    reporte = {
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus_csv": corpus_csv.name,
        "fuentes_verificadas": len(filas),
        "urls_verificadas": sum(f["en_corpus_csv"] for f in filas),
        "urls_faltantes_en_postgres": faltan_total,
        "lotes_en_contingencia": pendientes_contingencia,
        "resultado": "OK" if ok else "DESVIACION",
        "detalle": filas,
    }
    salida = Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)
    (salida / f"verificacion_dual_{datetime.now(timezone.utc):%Y%m%d}.json").write_text(
        json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")

    lineas = [f"## Escritura dual CSV + PostgreSQL: {reporte['resultado']}",
              f"- Fuentes verificadas: {reporte['fuentes_verificadas']}",
              f"- URLs del día en el corpus CSV: {reporte['urls_verificadas']:,}",
              f"- Faltan en PostgreSQL: {faltan_total:,}",
              f"- Lotes en contingencia: {len(pendientes_contingencia)}"]
    desviadas = [f for f in filas if f["faltan"]]
    if desviadas:
        lineas += ["", "| Fuente | En corpus CSV | Faltan en PostgreSQL | Ejemplo |", "|---|---:|---:|---|"]
        lineas += [f"| {f['fuente']} | {f['en_corpus_csv']} | {f['faltan']} | {(f['ejemplos'] or [f['error']])[0]} |" for f in desviadas]
    texto = "\n".join(lineas)
    print(texto)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(texto + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
