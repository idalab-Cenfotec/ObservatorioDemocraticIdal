"""
corpus_updater.py
─────────────────
Agente Actualizador — Fase 2: Actualización diaria del corpus maestro.

Compara los CSVs nuevos del pipeline contra el corpus maestro existente
y agrega solo los artículos que no estaban previamente (por URL única).

No modifica el corpus anterior — genera una nueva versión versionada.

Uso:
    python corpus_updater.py                        # modo normal
    python corpus_updater.py --output output/       # directorio de CSVs nuevos
    python corpus_updater.py --corpus corpus/       # directorio del corpus
    python corpus_updater.py --dry-run              # muestra stats sin guardar
    python corpus_updater.py --stats                # muestra estado del corpus
"""

import os
import re
import sys
import argparse
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone, timedelta

CR_TZ      = timezone(timedelta(hours=-6))
SEPARATOR  = "|"

# corpus_observatorio_v{version}_{YYYYMMDD}.csv
CORPUS_NAME_RE = re.compile(r"corpus_observatorio_v(\d+)_(\d{8})\.csv$")


# ─────────────────────────────────────────────────────────────────────────────
def _log(msg: str):
    ts = datetime.now(CR_TZ).strftime("%H:%M:%S")
    print(f"  [{ts}] {msg}")


def _find_latest_corpus(corpus_dir: str) -> Path | None:
    """Encuentra el corpus maestro más reciente.

    IMPORTANTE: se ordena por (fecha, versión) numéricos extraídos del nombre,
    NO alfabéticamente por el nombre completo del archivo. Ordenar por texto
    hacía que corpus_observatorio_v5_20260702.csv (versión 5, 2 de julio) le
    ganara a corpus_observatorio_v1_20260905.csv (versión 1, 5 de septiembre),
    porque "v5" es alfabéticamente mayor que "v1" — el corpus quedó "atascado"
    en la base del 2 de julio durante meses en vez de acumular lo de cada día.
    """
    p = Path(corpus_dir)
    if not p.exists():
        return None
    candidatos = []
    for f in p.glob("corpus_observatorio_v*.csv"):
        m = CORPUS_NAME_RE.search(f.name)
        if m:
            version, fecha = int(m.group(1)), m.group(2)
            candidatos.append((fecha, version, f))
    if not candidatos:
        return None
    candidatos.sort()
    return candidatos[-1][2]


def _find_new_csvs(output_dir: str) -> list[Path]:
    """Encuentra CSVs del día de hoy en output/."""
    p    = Path(output_dir)
    hoy  = datetime.now(CR_TZ).strftime("%Y%m%d")
    csvs = sorted(p.glob(f"*_{hoy}.csv"))
    csvs = [f for f in csvs if "_discarded" not in f.name and "_backup" not in f.name]
    return csvs


def _read_safe(path: Path, label: str = "") -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path, sep=SEPARATOR, dtype=str,
                         on_bad_lines="skip", encoding="utf-8-sig")
        return df
    except Exception as e:
        _log(f"  ✗ Error leyendo {label or path.name}: {e}")
        return None


def _next_version(corpus_dir: str, fecha: str) -> int:
    existing = list(Path(corpus_dir).glob(f"corpus_observatorio_v*_{fecha}.csv"))
    if not existing:
        return 1
    versions = []
    for f in existing:
        try:
            v = int(f.stem.split("_v")[1].split("_")[0])
            versions.append(v)
        except Exception:
            pass
    return max(versions) + 1 if versions else 1


# ─────────────────────────────────────────────────────────────────────────────
def show_stats(corpus_dir: str):
    """Muestra estadísticas del corpus maestro actual."""
    latest = _find_latest_corpus(corpus_dir)
    if not latest:
        print("  No hay corpus maestro todavía. Corre corpus_builder.py primero.")
        return

    df = _read_safe(latest, "corpus maestro")
    if df is None:
        return

    print(f"\n{'═'*60}")
    print(f"  CORPUS MAESTRO — {latest.name}")
    print(f"{'═'*60}")
    print(f"  Total artículos : {len(df):,}")
    print(f"  Fuentes         : {df['source'].nunique()}")
    if "publication_date" in df.columns:
        fechas = df["publication_date"].dropna()
        if not fechas.empty:
            print(f"  Fecha más reciente: {fechas.max()}")
            print(f"  Fecha más antigua : {fechas.min()}")
    print(f"\n  {'FUENTE':<25} {'ARTÍCULOS':>10}")
    print(f"  {'─'*38}")
    by_source = df.groupby("source").size().sort_values(ascending=False)
    for source, count in by_source.items():
        print(f"  {source:<25} {count:>10,}")
    print(f"{'═'*60}\n")


# ─────────────────────────────────────────────────────────────────────────────
def update_corpus(
    output_dir: str = "output",
    corpus_dir: str = "corpus",
    dry_run:    bool = False,
) -> dict:
    """
    Actualiza el corpus maestro con artículos nuevos del día.

    Retorna estadísticas de la actualización.
    """
    print("""
╔══════════════════════════════════════════════════════════════╗
║      OBSERVATORIO DEMOCRÁTICO — Corpus Updater               ║
╚══════════════════════════════════════════════════════════════╝
""")

    # ── 1. Encontrar corpus maestro existente ────────────────────────────────
    latest = _find_latest_corpus(corpus_dir)
    if not latest:
        _log("No hay corpus maestro. Ejecuta corpus_builder.py primero.")
        sys.exit(1)

    _log(f"Corpus maestro: {latest.name}")
    corpus = _read_safe(latest, "corpus maestro")
    if corpus is None:
        sys.exit(1)

    urls_existentes = set(corpus["url"].dropna().str.strip())
    _log(f"Artículos en corpus: {len(corpus):,}")
    _log(f"URLs únicas en corpus: {len(urls_existentes):,}")

    # ── 2. Encontrar CSVs nuevos del día ────────────────────────────────────
    new_csvs = _find_new_csvs(output_dir)
    if not new_csvs:
        _log("No hay CSVs nuevos del día de hoy en output/")
        _log("Nada que actualizar.")
        return {"nuevos": 0, "corpus_actual": len(corpus)}

    _log(f"CSVs nuevos encontrados hoy: {len(new_csvs)}")

    # ── 3. Leer CSVs nuevos y filtrar por URL ───────────────────────────────
    nuevos_frames = []
    total_nuevos_brutos = 0
    total_duplicados    = 0

    for csv_path in new_csvs:
        df = _read_safe(csv_path, csv_path.name)
        if df is None or "url" not in df.columns:
            continue

        total_nuevos_brutos += len(df)

        # Filtrar solo URLs que no existen en el corpus
        df_nuevo = df[~df["url"].str.strip().isin(urls_existentes)]
        duplicados = len(df) - len(df_nuevo)
        total_duplicados += duplicados

        if len(df_nuevo) > 0:
            nuevos_frames.append(df_nuevo)
            _log(f"  ✓ {csv_path.name:<40} +{len(df_nuevo):>4} nuevos  ({duplicados} ya existían)")
        else:
            _log(f"  ⊘ {csv_path.name:<40}  0 nuevos  ({duplicados} ya existían)")

    # ── 4. Si no hay nada nuevo, salir ──────────────────────────────────────
    if not nuevos_frames:
        _log(f"\nNo hay artículos nuevos. Corpus sin cambios ({len(corpus):,} artículos).")
        return {
            "nuevos":         0,
            "duplicados":     total_duplicados,
            "corpus_actual":  len(corpus),
        }

    # ── 5. Concatenar nuevos con corpus existente ────────────────────────────
    df_nuevos = pd.concat(nuevos_frames, ignore_index=True)
    df_nuevos = df_nuevos.drop_duplicates(subset=["url"], keep="first")

    corpus_nuevo = pd.concat([corpus, df_nuevos], ignore_index=True)
    corpus_nuevo = corpus_nuevo.drop_duplicates(subset=["url"], keep="first")

    # Ordenar por fecha descendente
    if "publication_date" in corpus_nuevo.columns:
        corpus_nuevo = corpus_nuevo.sort_values(
            "publication_date", ascending=False, na_position="last"
        )
    corpus_nuevo = corpus_nuevo.reset_index(drop=True)

    # ── 6. Resumen ───────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  Artículos previos   : {len(corpus):>10,}")
    print(f"  Artículos nuevos    : {len(df_nuevos):>10,}")
    print(f"  Duplicados omitidos : {total_duplicados:>10,}")
    print(f"  Total corpus nuevo  : {len(corpus_nuevo):>10,}")
    print(f"{'─'*60}\n")

    stats = {
        "nuevos":          len(df_nuevos),
        "duplicados":      total_duplicados,
        "corpus_anterior": len(corpus),
        "corpus_nuevo":    len(corpus_nuevo),
    }

    if dry_run:
        _log("Modo dry-run — no se guarda ningún archivo")
        return stats

    # ── 7. Guardar nueva versión del corpus ──────────────────────────────────
    Path(corpus_dir).mkdir(parents=True, exist_ok=True)
    fecha    = datetime.now(CR_TZ).strftime("%Y%m%d")
    version  = _next_version(corpus_dir, fecha)
    filename = f"corpus_observatorio_v{version}_{fecha}.csv"
    out_path = os.path.join(corpus_dir, filename)

    corpus_nuevo.to_csv(out_path, sep=SEPARATOR, index=False, encoding="utf-8")

    _log(f"Corpus actualizado: {out_path}")
    _log(f"Versión           : v{version}")
    _log(f"Total artículos   : {len(corpus_nuevo):,}")
    _log(f"Artículos nuevos  : {len(df_nuevos):,}")

    stats["output_file"] = out_path
    stats["version"]     = version

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Corpus Updater — Observatorio Democrático"
    )
    parser.add_argument("--output",  default="output",
                        help="Directorio con CSVs nuevos del pipeline")
    parser.add_argument("--corpus",  default="corpus",
                        help="Directorio del corpus maestro")
    parser.add_argument("--dry-run", action="store_true",
                        help="Muestra estadísticas sin guardar")
    parser.add_argument("--stats",   action="store_true",
                        help="Muestra estado actual del corpus y sale")

    args = parser.parse_args()

    base_dir   = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(base_dir, args.output) if not os.path.isabs(args.output) else args.output
    corpus_dir = os.path.join(base_dir, args.corpus) if not os.path.isabs(args.corpus) else args.corpus

    if args.stats:
        show_stats(corpus_dir)
        sys.exit(0)

    update_corpus(
        output_dir = output_dir,
        corpus_dir = corpus_dir,
        dry_run    = args.dry_run,
    )


if __name__ == "__main__":
    main()
