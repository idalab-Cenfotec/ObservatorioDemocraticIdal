"""
corpus_builder.py
─────────────────
Agente Actualizador — Fase 1: Constructor del corpus maestro.

Lee todos los CSVs individuales de output/ y los consolida en un
único archivo corpus maestro versionado, sin duplicados por URL.

Uso:
    python corpus_builder.py                         # usa output/ y corpus/
    python corpus_builder.py --output output/        # directorio de CSVs
    python corpus_builder.py --corpus data/master/   # directorio destino
    python corpus_builder.py --dry-run               # muestra stats sin guardar
"""

import os
import sys
import argparse
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone, timedelta

from output_cleaner import clean_dataframe

CR_TZ = timezone(timedelta(hours=-6))

# Artículos por llamada al webhook de N8N (ver enviar_a_n8n)
BATCH_SIZE = 200

# Schema v1.0 obligatorio
REQUIRED_COLUMNS = [
    "source", "url", "title", "publication_date",
    "scraping_date", "section", "full_text", "language",
]
SEPARATOR = "|"


# ─────────────────────────────────────────────────────────────────────────────
def _log(msg: str):
    ts = datetime.now(CR_TZ).strftime("%H:%M:%S")
    print(f"  [{ts}] {msg}")


def _find_csvs(output_dir: str) -> list[Path]:
    """Encuentra todos los CSVs en el directorio de output."""
    p = Path(output_dir)
    if not p.exists():
        _log(f"ERROR: directorio {output_dir} no existe")
        sys.exit(1)
    csvs = sorted(p.glob("*.csv"))
    # Excluir archivos de descartados
    csvs = [f for f in csvs if "_discarded" not in f.name]
    return csvs


def _read_csv_safe(path: Path) -> pd.DataFrame | None:
    """Lee un CSV con manejo de errores."""
    try:
        df = pd.read_csv(
            path,
            sep=SEPARATOR,
            dtype=str,
            on_bad_lines="skip",
            encoding="utf-8-sig",
        )
        # Verificar columnas mínimas
        missing = [c for c in ["source", "url", "full_text"] if c not in df.columns]
        if missing:
            _log(f"  ⚠ {path.name} — columnas faltantes: {missing} — omitiendo")
            return None
        return df
    except Exception as e:
        _log(f"  ✗ {path.name} — error de lectura: {e}")
        return None


def _preparar_registros(df: pd.DataFrame) -> list[dict]:
    """Limpia los artículos con output_cleaner.clean_dataframe (la misma limpieza
    de la carga histórica: fechas, HTML, secciones vacías, filas sin título) y
    arma los registros a enviar. Donde la fecha de publicación no se pudo
    interpretar (o coincide exactamente con scraping_date) se marca
    date_source = 'inferido' (Anexo Técnico 3.4); en el resto va None."""
    cols = df[REQUIRED_COLUMNS]
    base = cols.astype(object).where(pd.notna(cols), None)
    limpio, _stats, inferidas = clean_dataframe(base, verbose=False)
    urls_inferidas = set(inferidas["url"])
    registros = limpio.to_dict(orient="records")
    for r in registros:
        for k, v in r.items():
            if v == "NULL":
                r[k] = None
        inferido = r["url"] in urls_inferidas or r["publication_date"] == r["scraping_date"]
        r["date_source"] = "inferido" if inferido else None
    return registros


def enviar_a_n8n(df: pd.DataFrame, webhook_url: str, webhook_token: str) -> dict:
    """Envía el corpus a PostgreSQL vía el webhook de N8N (Anexo Técnico 3.7.1).

    GitHub Actions y la VM institucional no están en la misma red, así que este
    script (corre en un runner de GitHub Actions) no se conecta directo a
    PostgreSQL. En su lugar, N8N recibe este webhook desde dentro de la red
    interna de la VM, donde sí tiene acceso a la base.

    Se envía en lotes de BATCH_SIZE artículos: un solo POST con miles de
    artículos supera el tamaño máximo de cuerpo de N8N y el timeout. Si algún
    lote falla se propaga la excepción y el llamador guarda todo en
    contingencia/; reenviar lotes ya cargados es inocuo (ON CONFLICT DO NOTHING).
    Devuelve los totales que reporta N8N (recibidos, insertados, ya_existian, rechazados).
    """
    registros = _preparar_registros(df)
    totales = {"recibidos": 0, "insertados": 0, "ya_existian": 0, "rechazados": 0}
    for i in range(0, len(registros), BATCH_SIZE):
        resp = requests.post(
            webhook_url,
            json={"articulos": registros[i:i + BATCH_SIZE]},
            headers={"X-Webhook-Token": webhook_token},
            timeout=30,
        )
        resp.raise_for_status()
        try:
            respuesta = resp.json()
        except ValueError:
            respuesta = None
        if isinstance(respuesta, dict):
            for k in totales:
                totales[k] += int(respuesta.get(k) or 0)
    return totales


def registrar_corrida_parcial(ruta_contingencia: str, error_msg: str) -> None:
    """Deja constancia local de que el envío a N8N falló (Anexo Técnico 3.7.2).

    Este script no tiene conexión directa a PostgreSQL (ver enviar_a_n8n), así
    que no puede escribir en agent_runs por su cuenta. Se registra localmente
    para que quede en los logs del run de GitHub Actions; cuando la corrida
    diaria posterior lea contingencia/ y reintente el envío, esos artículos
    quedan asociados a una corrida de agent_runs normal en N8N.
    """
    _log(f"⚠ Envío a N8N falló, corpus guardado en contingencia: {ruta_contingencia}")
    _log(f"  Motivo: {error_msg}")


def guardar_corpus_dual(
    corpus: pd.DataFrame,
    out_path: str,
    webhook_url: str | None = None,
    webhook_token: str | None = None,
) -> None:
    """Escritura dual (Anexo Técnico 3.7): guarda el CSV como siempre y, si hay
    webhook configurado, intenta además enviarlo a PostgreSQL vía N8N. Si el
    envío falla, el CSV de contingencia asegura que el lote no se pierda — se
    reintenta en la siguiente corrida gracias a ON CONFLICT (url) DO NOTHING.
    """
    corpus.to_csv(out_path, sep=SEPARATOR, index=False, encoding="utf-8")

    if webhook_url is None:
        return

    try:
        t = enviar_a_n8n(corpus, webhook_url, webhook_token)
        _log(f"Corpus enviado a PostgreSQL vía N8N: {t['recibidos']:,} recibidos, "
             f"{t['insertados']:,} nuevos, {t['ya_existian']:,} ya existían, {t['rechazados']:,} rechazados")
    except requests.exceptions.RequestException as e:
        Path("contingencia").mkdir(parents=True, exist_ok=True)
        ruta_contingencia = f"contingencia/{datetime.now(CR_TZ):%Y-%m-%d}_corpus.csv"
        corpus.to_csv(ruta_contingencia, sep=SEPARATOR, index=False, encoding="utf-8")
        registrar_corrida_parcial(ruta_contingencia, str(e))


def reintentar_contingencia(webhook_url: str | None, webhook_token: str | None) -> None:
    """Reintenta enviar los lotes que quedaron pendientes en contingencia/ por
    una falla anterior del webhook (Anexo Técnico 3.7.2). Los que se envían
    con éxito se eliminan; los que vuelven a fallar quedan para el próximo intento."""
    if webhook_url is None:
        return
    cdir = Path("contingencia")
    if not cdir.is_dir():
        return
    for f in sorted(cdir.glob("*.csv")):
        try:
            df = pd.read_csv(f, sep=SEPARATOR, dtype=str, on_bad_lines="skip", encoding="utf-8-sig")
            enviar_a_n8n(df, webhook_url, webhook_token)
            f.unlink()
            _log(f"Contingencia reenviada y eliminada: {f.name} ({len(df):,} artículos)")
        except requests.exceptions.RequestException as e:
            _log(f"Contingencia {f.name} sigue sin poder enviarse: {e}")


def build_corpus(
    output_dir:    str = "output",
    corpus_dir:    str = "corpus",
    dry_run:       bool = False,
    webhook_url:   str | None = None,
    webhook_token: str | None = None,
) -> dict:
    """
    Construye el corpus maestro consolidado.

    Retorna un dict con estadísticas de la construcción.
    """
    print("""
╔══════════════════════════════════════════════════════════════╗
║      OBSERVATORIO DEMOCRÁTICO — Corpus Builder               ║
╚══════════════════════════════════════════════════════════════╝
""")

    # ── 0. Reintentar envíos pendientes de una corrida anterior ──────────────
    reintentar_contingencia(webhook_url, webhook_token)

    # ── 1. Encontrar CSVs ────────────────────────────────────────────────────
    csvs = _find_csvs(output_dir)
    _log(f"CSVs encontrados: {len(csvs)}")
    if not csvs:
        _log("ERROR: no hay CSVs en el directorio de output")
        sys.exit(1)

    # ── 2. Leer y consolidar ─────────────────────────────────────────────────
    frames     = []
    read_ok    = 0
    read_error = 0
    total_raw  = 0

    for csv_path in csvs:
        df = _read_csv_safe(csv_path)
        if df is None:
            read_error += 1
            continue
        _log(f"  ✓ {csv_path.name:<45} {len(df):>6} filas")
        total_raw += len(df)
        frames.append(df)
        read_ok += 1

    if not frames:
        _log("ERROR: ningún CSV pudo leerse correctamente")
        sys.exit(1)

    _log(f"\nCSVs leídos: {read_ok} OK, {read_error} con error")
    _log(f"Total filas brutas: {total_raw:,}")

    # ── 3. Concatenar ────────────────────────────────────────────────────────
    corpus = pd.concat(frames, ignore_index=True)

    # ── 4. Eliminar duplicados por URL ───────────────────────────────────────
    antes      = len(corpus)
    corpus     = corpus.drop_duplicates(subset=["url"], keep="first")
    duplicados = antes - len(corpus)
    _log(f"Duplicados eliminados por URL: {duplicados:,}")

    # ── 5. Eliminar filas sin full_text ──────────────────────────────────────
    corpus = corpus[corpus["full_text"].notna() & (corpus["full_text"].str.strip() != "") & (corpus["full_text"] != "NULL")]
    sin_texto = antes - duplicados - len(corpus)
    if sin_texto > 0:
        _log(f"Filas sin full_text eliminadas: {sin_texto:,}")

    # ── 6. Eliminar filas sin URL ────────────────────────────────────────────
    corpus = corpus[corpus["url"].notna() & (corpus["url"].str.strip() != "") & (corpus["url"] != "NULL")]

    # ── 7. Ordenar por fecha descendente ────────────────────────────────────
    if "publication_date" in corpus.columns:
        corpus = corpus.sort_values("publication_date", ascending=False, na_position="last")

    corpus = corpus.reset_index(drop=True)

    # ── 8. Estadísticas por fuente ───────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  {'FUENTE':<25} {'ARTÍCULOS':>10}")
    print(f"{'─'*60}")
    by_source = corpus.groupby("source").size().sort_values(ascending=False)
    for source, count in by_source.items():
        print(f"  {source:<25} {count:>10,}")
    print(f"{'─'*60}")
    print(f"  {'TOTAL':<25} {len(corpus):>10,}")
    print(f"{'─'*60}\n")

    stats = {
        "csvs_leidos":    read_ok,
        "csvs_error":     read_error,
        "total_raw":      total_raw,
        "duplicados":     duplicados,
        "total_final":    len(corpus),
        "fuentes":        int(corpus["source"].nunique()),
    }

    if dry_run:
        _log("Modo dry-run — no se guarda ningún archivo")
        return stats

    # ── 9. Guardar corpus versionado ─────────────────────────────────────────
    Path(corpus_dir).mkdir(parents=True, exist_ok=True)
    fecha     = datetime.now(CR_TZ).strftime("%Y%m%d")
    version   = _next_version(corpus_dir, fecha)
    filename  = f"corpus_observatorio_v{version}_{fecha}.csv"
    out_path  = os.path.join(corpus_dir, filename)

    guardar_corpus_dual(corpus, out_path, webhook_url, webhook_token)

    _log(f"Corpus guardado: {out_path}")
    _log(f"Versión        : v{version}")
    _log(f"Total artículos: {len(corpus):,}")
    _log(f"Fuentes        : {stats['fuentes']}")

    stats["output_file"] = out_path
    stats["version"]     = version

    return stats


def _next_version(corpus_dir: str, fecha: str) -> int:
    """Determina el siguiente número de versión del corpus."""
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
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Corpus Builder — Observatorio Democrático"
    )
    parser.add_argument("--output",  default="output",
                        help="Directorio con los CSVs individuales")
    parser.add_argument("--corpus",  default="corpus",
                        help="Directorio donde se guarda el corpus maestro")
    parser.add_argument("--dry-run", action="store_true",
                        help="Muestra estadísticas sin guardar")
    parser.add_argument("--webhook-url", default=os.environ.get("N8N_WEBHOOK_URL"),
                        help="URL del webhook de N8N para escritura dual (o var. de entorno N8N_WEBHOOK_URL)")
    parser.add_argument("--webhook-token", default=os.environ.get("N8N_WEBHOOK_TOKEN"),
                        help="Token del webhook (o var. de entorno N8N_WEBHOOK_TOKEN)")

    args = parser.parse_args()

    # Resolver rutas absolutas
    base_dir   = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(base_dir, args.output) if not os.path.isabs(args.output) else args.output
    corpus_dir = os.path.join(base_dir, args.corpus) if not os.path.isabs(args.corpus) else args.corpus

    build_corpus(
        output_dir    = output_dir,
        corpus_dir    = corpus_dir,
        dry_run       = args.dry_run,
        webhook_url   = args.webhook_url,
        webhook_token = args.webhook_token,
    )


if __name__ == "__main__":
    main()
