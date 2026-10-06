#!/usr/bin/env python3
"""Verificación diaria de la escritura dual CSV + PostgreSQL (SCRUM-28).

Corre al final del pipeline, después de que corpus_updater.py / corpus_builder.py
actualizaron el corpus CSV y mandaron lo nuevo a PostgreSQL por el webhook de N8N.

Criterio (lo que deja el job en rojo, código de salida 1):
  1. Lo que el CSV marcó como nuevo y se envió (logs/envio_n8n_<fecha>.json) debe
     cuadrar con lo que N8N contestó: recibidos == enviados e
     insertados + ya_existian + rechazados == recibidos.
  2. No puede haber lotes pendientes en contingencia/ ni un envío fallido.
Avisos (no ponen el job en rojo): notas rechazadas por la validación de N8N.
Informativo: por fuente, URLs de hoy que el corpus CSV tiene y PostgreSQL no
(incluye duplicados de contenido que los índices únicos rechazan a propósito).

Deja un JSON con el detalle y un resumen en Markdown (GITHUB_STEP_SUMMARY). Los
reportes diarios son la evidencia del período de operación en escritura dual.

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


def evaluar_envio(envio: dict | None, pendientes_contingencia: list[str]) -> tuple[list[str], list[str]]:
    """(fallas, avisos) según el reporte de envío del updater y los lotes pendientes."""
    fallas, avisos = [], []
    if pendientes_contingencia:
        fallas.append(f"{len(pendientes_contingencia)} lote(s) pendientes en contingencia/: {', '.join(pendientes_contingencia)}")
    if envio is None:
        return fallas, avisos            # nada nuevo que enviar hoy
    if envio.get("contingencia"):
        fallas.append(f"el envío a N8N falló y quedó en contingencia: {envio.get('error', '')}")
        return fallas, avisos
    recibidos = int(envio.get("recibidos", 0))
    if "enviados" in envio and recibidos != int(envio["enviados"]):
        fallas.append(f"se enviaron {envio['enviados']:,} notas y N8N recibió {recibidos:,}")
    contadas = sum(int(envio.get(k, 0)) for k in ("insertados", "ya_existian", "rechazados"))
    if contadas != recibidos:
        fallas.append(f"N8N recibió {recibidos:,} pero contó {contadas:,} (insertadas + ya existentes + rechazadas)")
    if int(envio.get("rechazados", 0)):
        avisos.append(f"{envio['rechazados']} nota(s) rechazadas por la validación de N8N")
    return fallas, avisos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", default="output")
    ap.add_argument("--corpus", default="corpus")
    ap.add_argument("--contingencia", default="contingencia")
    ap.add_argument("--salida", default="logs")
    ap.add_argument("--urls-conocidas", default=os.environ.get("N8N_URLS_CONOCIDAS_URL"))
    ap.add_argument("--token", default=os.environ.get("N8N_WEBHOOK_TOKEN"))
    a = ap.parse_args()

    salida = Path(a.salida)
    pendientes_contingencia = sorted(p.name for p in Path(a.contingencia).glob("*.csv")) if Path(a.contingencia).is_dir() else []
    envios = sorted(salida.glob("envio_n8n_*.json"))
    envio = json.loads(envios[-1].read_text(encoding="utf-8")) if envios else None
    fallas, avisos = evaluar_envio(envio, pendientes_contingencia)

    # Informativo: URLs de hoy en el corpus CSV que PostgreSQL no tiene, por fuente.
    filas, faltan_total = [], 0
    corpus_csv = _corpus_mas_reciente(Path(a.corpus)) if Path(a.corpus).is_dir() else None
    if corpus_csv and a.urls_conocidas and a.token:
        aceptadas = urls_aceptadas_por_el_corpus(corpus_csv, urls_del_dia(Path(a.output)))
        for fuente in sorted(aceptadas):
            claves = aceptadas[fuente]
            try:
                faltan = sorted(claves - urls_en_postgres(fuente, a.urls_conocidas, a.token))
            except Exception as e:
                avisos.append(f"no se pudo consultar {fuente} en N8N para el conteo informativo: {e}")
                continue
            faltan_total += len(faltan)
            filas.append({"fuente": fuente, "en_corpus_csv": len(claves), "faltan": len(faltan), "ejemplos": faltan[:3]})

    ok = not fallas
    reporte = {
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resultado": "OK" if ok else "DESVIACION",
        "envio_n8n": envio,
        "fallas": fallas,
        "avisos": avisos,
        "informativo_urls_del_dia_sin_fila_en_postgres": faltan_total,
        "informativo_detalle": [f for f in filas if f["faltan"]],
    }
    salida.mkdir(parents=True, exist_ok=True)
    (salida / f"verificacion_dual_{datetime.now(timezone.utc):%Y%m%d}.json").write_text(
        json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")

    lineas = [f"## Escritura dual CSV + PostgreSQL: {reporte['resultado']}"]
    if envio and not envio.get("contingencia"):
        lineas += [f"- Enviadas a PostgreSQL: {envio.get('enviados', envio.get('recibidos', 0)):,} "
                   f"(nuevas {envio.get('insertados', 0):,}, ya existían {envio.get('ya_existian', 0):,}, "
                   f"rechazadas {envio.get('rechazados', 0):,})"]
    elif envio is None:
        lineas.append("- Sin notas nuevas para el corpus hoy: nada que enviar")
    lineas += [f"- Lotes en contingencia: {len(pendientes_contingencia)}",
               f"- Informativo: {faltan_total:,} URLs de hoy en el CSV sin fila en PostgreSQL (duplicados rechazados por los índices, entre otros)"]
    lineas += [f"- ❌ {f}" for f in fallas] + [f"- ⚠ {v}" for v in avisos]
    texto = "\n".join(lineas)
    print(texto)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(texto + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
