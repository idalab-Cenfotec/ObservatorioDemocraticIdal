#!/usr/bin/env python
"""
output_cleaner.py
Valida —y opcionalmente corrige— los archivos CSV generados por ScrapingObservatorio.

VALIDACION (por defecto):
  Comprueba:
  - columnas obligatorias
  - ausencia de valores nulos o vacios
  - duplicados por URL
  - formato correcto de fechas
  - ausencia de HTML en los campos de texto
  - longitud minima de full_text

CORRECCION (con --fix):
  Aplica:
  1. Normaliza publication_date y scraping_date a %Y-%m-%d %H:%M:%S
     (convierte fechas relativas en espanol: "Hace X dias/semanas/horas/meses")
  2. Elimina etiquetas HTML de title, section y full_text
  3. Completa section vacia con "Sin seccion"
  4. Descarta filas con title vacio/nulo
  5. Elimina URL duplicadas (keep=first)
  6. Guarda el CSV corregido (--output o <input>_clean.csv)

Formatos de fecha reconocidos al corregir:
  - "2026-05-30 10:15:00", "2026-05-30", "30/05/2026" y variantes
  - ISO 8601 con "T" y zona horaria ("2026-07-03T07:09:26.000Z"); si trae zona
    se convierte a hora de Costa Rica (UTC-6), igual que el resto del corpus
  - "mayo 30, 2026", "Ago 10, 2026" (mes completo o abreviado) y "30 de mayo de 2026"
  - Relativas: "Hace 53 minutos", "Hace 2 horas", "Hace 3 dias", etc.
Si la fecha no se puede interpretar ("Sin fecha", vacia, desconocida) se usa la
fecha de extraccion (scraping_date). Con --inferred-out se guarda la lista de
URLs en las que eso ocurrio, para marcarlas como date_source = 'inferido'.

Ejemplos:
  python output_cleaner.py output/
  python output_cleaner.py output/lareaccioncr_20260516.csv
  python output_cleaner.py corpus/corpus_observatorio_v6.csv --fix
  python output_cleaner.py corpus/corpus_observatorio_v6.csv --fix --output corpus/corpus_v6_clean.csv
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from limpieza_relleno import es_plantilla, idioma_de_nota, limpiar_relleno

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = [
    "source", "url", "title", "publication_date",
    "scraping_date", "section", "full_text", "language",
]
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MIN_TEXT_LENGTH = 300
HTML_PATTERN = re.compile(r"<[^>]+>")

_URL_ESQUEMA_WWW = re.compile(r"^https?://(www\.)?")
_URL_FRAGMENTO = re.compile(r"#.*$")
_URL_BARRAS_FINALES = re.compile(r"/+$")


def url_key(url: str) -> str:
    """Clave de comparación de URLs: sin http(s)://, sin www., sin #fragmento,
    sin barras finales y en minúsculas. Es la MISMA expresión del índice único
    ux_articles_url_norm de PostgreSQL: dos URLs con la misma clave son para la
    base de datos el mismo artículo (ej. https://www.x.com/nota/ y
    https://x.com/nota). No altera la URL que se guarda."""
    u = _URL_ESQUEMA_WWW.sub("", str(url).strip())
    u = _URL_FRAGMENTO.sub("", u)
    return _URL_BARRAS_FINALES.sub("", u).lower()

EXCLUSIONES_URLS = Path(__file__).resolve().parent / "db" / "exclusiones_urls.txt"


def cargar_exclusiones(ruta: Path | None = None) -> set[str]:
    """url_key de las notas que se eliminaron a propósito de PostgreSQL (duplicados,
    basura, fuentes extranjeras). El corpus CSV debe omitirlas igual que la base,
    si no vuelven a entrar cada vez que el scraper las encuentra y las dos copias
    se desvían. Archivo: db/exclusiones_urls.txt, líneas "fuente<TAB>url_key"."""
    ruta = Path(ruta) if ruta else EXCLUSIONES_URLS
    if not ruta.is_file():
        return set()
    claves = set()
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if linea and not linea.startswith("#") and "	" in linea:
            claves.add(linea.split("	", 1)[1].strip())
    return claves


MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

# Abreviaturas de mes (tres letras). Se aceptan tambien las de ingles que
# no chocan con el espanol (jan, apr, aug, dec).
MESES_ABREV = {
    "ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "ago": 8, "aug": 8, "sep": 9,
    "set": 9, "oct": 10, "nov": 11, "dic": 12, "dec": 12,
}

# Hora de Costa Rica (UTC-6, sin horario de verano)
CR_TZ = timezone(timedelta(hours=-6))

RELATIVE_RE = re.compile(
    r"hace\s+(?P<n>\d+)\s+(?P<unit>segundos?|minutos?|horas?|d\xedas?|dias?|semanas?|mes(?:es)?|a\xf1os?|anos?)",
    re.IGNORECASE,
)

DATE_FORMATS_TO_TRY = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
    "%d-%m-%Y",
]

# ---------------------------------------------------------------------------
# Helpers — carga
# ---------------------------------------------------------------------------

def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        path, sep="|", dtype=str,
        keep_default_na=False, na_values=["NULL"],
        encoding="utf-8",
    )
    return df.where(pd.notna(df), None)


# ---------------------------------------------------------------------------
# Helpers — validacion
# ---------------------------------------------------------------------------

def format_issue(issue_type: str, value: str, count: int, details: str = "") -> dict:
    return {"type": issue_type, "value": value, "count": count, "details": details}


def validate_file(path: Path) -> dict:
    report = {"file": str(path), "ok": True, "issues": [], "counts": {}}

    if not path.exists():
        report["ok"] = False
        report["issues"].append(format_issue("missing_file", str(path), 1, "El archivo no existe"))
        return report

    try:
        df = load_csv(path)
    except Exception as exc:
        report["ok"] = False
        report["issues"].append(format_issue("read_error", str(exc), 1, "No se pudo leer el CSV"))
        return report

    missing_columns = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_columns:
        report["ok"] = False
        report["issues"].append(format_issue(
            "missing_columns", ", ".join(missing_columns),
            len(missing_columns), f"Columnas obligatorias ausentes: {missing_columns}",
        ))
        return report

    report["counts"]["rows"] = len(df)
    report["counts"]["columns"] = len(df.columns)

    for col in REQUIRED_COLUMNS:
        if df[col].isna().any() or df[col].astype(str).str.strip().eq("").any():
            invalid = df[df[col].isna() | df[col].astype(str).str.strip().eq("")]
            count = len(invalid)
            report["ok"] = False
            report["issues"].append(format_issue(
                "missing_value", col, count,
                f"{count} filas con valor nulo o vacio en '{col}'",
            ))

    if "url" in df.columns:
        dup_count = int(df["url"].duplicated(keep=False).sum())
        if dup_count:
            report["ok"] = False
            report["issues"].append(format_issue(
                "duplicate_url", "url", dup_count,
                f"{dup_count} filas duplicadas en columna 'url'",
            ))

    for col in ["publication_date", "scraping_date"]:
        raw = df[col].astype(str).fillna("")
        parsed = pd.to_datetime(raw, format=DATE_FORMAT, errors="coerce")
        invalid = parsed.isna() & raw.ne("")
        count = int(invalid.sum())
        if count:
            bad = raw[invalid].unique().tolist()[:5]
            report["ok"] = False
            report["issues"].append(format_issue(
                "invalid_date_format", col, count,
                f"{count} valores no siguen el formato {DATE_FORMAT}; ejemplos: {bad}",
            ))

    for col in ["title", "section", "full_text"]:
        mask = df[col].astype(str).fillna("").str.contains(HTML_PATTERN, na=False)
        count = int(mask.sum())
        if count:
            report["ok"] = False
            report["issues"].append(format_issue(
                "html_detected", col, count,
                f"{count} filas contienen etiquetas HTML en '{col}'",
            ))

    if "full_text" in df.columns:
        length_mask = df["full_text"].astype(str).fillna("").str.len() < MIN_TEXT_LENGTH
        count = int(length_mask.sum())
        if count:
            report["ok"] = False
            report["issues"].append(format_issue(
                "short_full_text", "full_text", count,
                f"{count} filas con full_text menor a {MIN_TEXT_LENGTH} caracteres",
            ))

    report["counts"]["missing_columns"] = len(missing_columns)
    return report


# ---------------------------------------------------------------------------
# Helpers — correcciones de fecha
# ---------------------------------------------------------------------------

def _try_parse_date(raw: str) -> datetime | None:
    for fmt in DATE_FORMATS_TO_TRY:
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def _try_parse_iso8601(raw: str) -> datetime | None:
    """ISO 8601 con 'T' ("2026-07-03T07:09:26.000Z"). Si trae zona horaria se
    convierte a hora de Costa Rica y se devuelve sin zona, como el resto del corpus."""
    s = raw.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}T", s):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(CR_TZ).replace(tzinfo=None)
    return dt


def _mes_desde_texto(token: str) -> int | None:
    """Numero de mes a partir de 'mayo', 'Ago', 'sept.', etc."""
    t = token.lower().rstrip(".")
    mes = MESES_ES.get(t)
    if mes:
        return mes
    if len(t) <= 4:  # abreviatura: 'ago', 'sept'
        return MESES_ABREV.get(t[:3])
    return None


def _try_parse_spanish(raw: str) -> datetime | None:
    raw = raw.strip()
    # "mayo 30, 2026" / "Ago 10, 2026"
    m = re.fullmatch(r"([^\W\d_]+\.?)\s+(\d{1,2}),?\s+(\d{4})", raw, re.IGNORECASE)
    if m:
        mes = _mes_desde_texto(m.group(1))
        if mes:
            try:
                return datetime(int(m.group(3)), mes, int(m.group(2)))
            except ValueError:
                pass
    # "30 de mayo de 2026"
    m = re.fullmatch(r"(\d{1,2})\s+de\s+([^\W\d_]+)\s+de\s+(\d{4})", raw, re.IGNORECASE)
    if m:
        mes = _mes_desde_texto(m.group(2))
        if mes:
            try:
                return datetime(int(m.group(3)), mes, int(m.group(1)))
            except ValueError:
                pass
    return None


def _resolve_relative(raw: str, reference: datetime) -> datetime | None:
    m = RELATIVE_RE.search(raw)
    if not m:
        return None
    n = int(m.group("n"))
    unit = m.group("unit").lower()
    if unit.startswith("segundo"):
        return reference - timedelta(seconds=n)
    if unit.startswith("minuto"):
        return reference - timedelta(minutes=n)
    if unit.startswith("hora"):
        return reference - timedelta(hours=n)
    if unit.startswith("d"):  # dia / dias
        return reference - timedelta(days=n)
    if unit.startswith("semana"):
        return reference - timedelta(weeks=n)
    if unit.startswith("mes"):
        return reference - timedelta(days=n * 30)
    if unit.startswith("a"):  # ano / anos
        return reference - timedelta(days=n * 365)
    return None


def normalize_date(raw: str, reference: datetime | None = None) -> str:
    if not raw or str(raw).strip() in ("", "NULL", "None", "nan", "NaT", "Sin fecha"):
        return "NULL"
    raw = str(raw).strip()

    dt = _try_parse_date(raw)
    if dt:
        return dt.strftime(DATE_FORMAT)

    dt = _try_parse_iso8601(raw)
    if dt:
        return dt.strftime(DATE_FORMAT)

    dt = _try_parse_spanish(raw)
    if dt:
        return dt.strftime(DATE_FORMAT)

    ref = reference or datetime.now()
    dt = _resolve_relative(raw, ref)
    if dt:
        return dt.strftime(DATE_FORMAT)

    return "NULL"


# ---------------------------------------------------------------------------
# Helpers — correcciones de texto
# ---------------------------------------------------------------------------

def strip_html(text: str) -> str:
    if not text or str(text).strip() in ("NULL", "None", "nan"):
        return text
    cleaned = HTML_PATTERN.sub(" ", str(text))
    return re.sub(r"\s{2,}", " ", cleaned).strip()


# ---------------------------------------------------------------------------
# Pipeline de correccion
# ---------------------------------------------------------------------------

def clean_dataframe(df: pd.DataFrame, verbose: bool = True) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """Aplica la limpieza completa (fechas, HTML, secciones vacías, filas sin
    título, duplicados) a un DataFrame con el esquema v1.0 y lo devuelve junto
    con las estadísticas y la lista de fechas inferidas.

    Es la misma lógica que usa fix_file para los CSV; está separada para que el
    envío diario a PostgreSQL (corpus_builder.py / corpus_updater.py) limpie los
    artículos nuevos exactamente igual que la carga histórica.

    Los valores faltantes salen como el literal "NULL" (convención del corpus).
    Retorna (df_limpio, stats, inferred) donde `inferred` lista las URL cuya
    publication_date se reemplazó por scraping_date.
    """
    log = print if verbose else (lambda *args, **kwargs: None)
    df = df.copy()
    initial_rows = len(df)
    stats: dict = {"initial": initial_rows}
    original_pub = df["publication_date"].copy()

    # 1. Normalizar scraping_date
    log("  Normalizando scraping_date...")
    df["scraping_date"] = df["scraping_date"].apply(
        lambda x: normalize_date(str(x) if x else "")
    )

    # 2. Normalizar publication_date usando scraping_date como referencia
    log("  Normalizando publication_date...")

    def _norm_pub(row):
        ref = None
        sd = row.get("scraping_date", "")
        if sd and sd != "NULL":
            try:
                ref = datetime.strptime(sd, DATE_FORMAT)
            except ValueError:
                pass
        return normalize_date(str(row["publication_date"]) if row["publication_date"] else "", ref)

    df["publication_date"] = df.apply(_norm_pub, axis=1)

    # Fallback: si publication_date sigue siendo NULL, usar scraping_date
    null_pub = df["publication_date"] == "NULL"
    inferred = df.loc[null_pub, ["url", "source"]].copy()
    inferred["publication_date_original"] = original_pub[null_pub].fillna("NULL")
    df.loc[null_pub, "publication_date"] = df.loc[null_pub, "scraping_date"]
    stats["pub_date_set_null"] = int((df["publication_date"] == "NULL").sum())
    stats["pub_date_fallback_to_scraping"] = int(null_pub.sum()) - stats["pub_date_set_null"]

    # 3. Limpiar HTML
    log("  Eliminando HTML...")
    html_stripped: dict = {}
    for col in ("title", "section", "full_text"):
        mask = df[col].astype(str).fillna("").str.contains(HTML_PATTERN, na=False)
        html_stripped[col] = int(mask.sum())
        df[col] = df[col].apply(lambda x: strip_html(x) if x else x)
    stats["html_stripped"] = html_stripped

    # 3b. Quitar el relleno que cada sitio repite en todas sus notas (ver limpieza_relleno.py)
    log("  Quitando relleno de los sitios...")
    antes = df["full_text"].copy()
    df["full_text"] = [
        limpiar_relleno(fuente, texto) for fuente, texto in zip(df["source"], df["full_text"])
    ]
    stats["relleno_recortado"] = int((antes != df["full_text"]).sum())

    # 3c. Idioma: el de la fuente, salvo notas largas que el detector da como inglés casi con certeza
    idioma = pd.Series([idioma_de_nota(f, t) for f, t in zip(df["source"], df["full_text"])], index=df.index)
    stats["idioma_corregido"] = int((idioma != df["language"]).sum())
    df["language"] = idioma

    # 4. Rellenar section vacia
    log("  Completando secciones vacias...")
    empty_sec = df["section"].isna() | df["section"].astype(str).str.strip().isin(["", "None", "nan"])
    stats["section_filled"] = int(empty_sec.sum())
    df.loc[empty_sec, "section"] = "Sin seccion"

    # 5. Descartar filas sin titulo
    log("  Descartando filas sin titulo...")
    no_title = df["title"].isna() | df["title"].astype(str).str.strip().isin(["", "None", "nan"])
    stats["dropped_no_title"] = int(no_title.sum())
    df = df[~no_title].copy()

    # 5b. Descartar notas de plantilla ("lorem ipsum" del tema del sitio)
    plantilla = df["full_text"].apply(lambda t: es_plantilla(t) if isinstance(t, str) else False)
    stats["dropped_plantilla"] = int(plantilla.sum())
    df = df[~plantilla].copy()

    # 6. Deduplicar por URL
    log("  Deduplicando por URL...")
    before_dedup = len(df)
    df = df.drop_duplicates(subset=["url"], keep="first")
    stats["deduped"] = before_dedup - len(df)

    # 7. Rellenar NULLs restantes con literal "NULL"
    for col in REQUIRED_COLUMNS:
        if col in df.columns:
            df[col] = df[col].fillna("NULL")

    stats["final"] = len(df)

    # Fechas inferidas: solo las filas que sobrevivieron a la limpieza
    inferred = inferred[inferred["url"].isin(df["url"])]
    stats["pub_date_inferred"] = len(inferred)

    return df, stats, inferred


def fix_file(path: Path, output_path: Path, inferred_out: Path | None = None) -> dict:
    print(f"Cargando: {path}")
    df = load_csv(path)
    df, stats, inferred = clean_dataframe(df)

    if inferred_out is not None:
        inferred_out.parent.mkdir(parents=True, exist_ok=True)
        inferred.to_csv(inferred_out, sep="|", index=False, encoding="utf-8")
        print(f"  Fechas inferidas listadas en: {inferred_out}")

    # 8. Guardar
    print(f"  Guardando corpus corregido: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, sep="|", index=False, encoding="utf-8")

    return stats


def print_fix_summary(stats: dict, output_path: Path):
    print()
    print("-" * 55)
    print("RESUMEN DE CORRECCION")
    print("-" * 55)
    print(f"  Filas iniciales:                   {stats['initial']:>7,}")
    print(f"  Filas descartadas (sin titulo):    {stats['dropped_no_title']:>7,}")
    print(f"  Duplicados URL eliminados:         {stats['deduped']:>7,}")
    print(f"  Filas finales:                     {stats['final']:>7,}")
    print()
    print(f"  Fechas pub. sin parsear -> scraping_date: {stats.get('pub_date_fallback_to_scraping', 0):>5,}")
    print(f"  (fechas inferidas en filas finales:       {stats.get('pub_date_inferred', 0):>5,})")
    print(f"  Fechas pub. -> NULL (sin referencia):    {stats['pub_date_set_null']:>5,}")
    print(f"  Secciones vacias rellenadas:         {stats['section_filled']:>5,}")
    html = stats.get("html_stripped", {})
    print(f"  HTML eliminado en title:             {html.get('title', 0):>5,}")
    print(f"  HTML eliminado en section:           {html.get('section', 0):>5,}")
    print(f"  HTML eliminado en full_text:         {html.get('full_text', 0):>5,}")
    print("-" * 55)
    print(f"  Guardado en: {output_path}")


# ---------------------------------------------------------------------------
# Reporte de validacion
# ---------------------------------------------------------------------------

def print_report(report: dict):
    print(f"Validando: {report['file']}")
    print(f"  Resultado: {'OK' if report['ok'] else 'ERROR'}")
    print(f"  Filas: {report['counts'].get('rows', 0)}")
    print(f"  Columnas: {report['counts'].get('columns', 0)}")
    if report["issues"]:
        for issue in report["issues"]:
            print(f"    - {issue['type']} [{issue['value']}]: {issue['details']}")
    print()


def save_report(report: dict, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"validation_report_{ts}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report_path


def collect_csv_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(path.glob("*.csv"))
    raise ValueError(f"Ruta desconocida: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Valida y/o corrige los CSV del Observatorio."
    )
    parser.add_argument("path", type=str, help="Archivo CSV o directorio a validar/corregir")
    parser.add_argument("--report", type=str, default="logs",
                        help="Directorio para el reporte JSON de validacion")
    parser.add_argument("--fix", action="store_true",
                        help="Corrige los problemas encontrados y guarda el archivo limpio")
    parser.add_argument("--output", type=str, default=None,
                        help="Ruta de salida al usar --fix (por defecto: <input>_clean.csv)")
    parser.add_argument("--inferred-out", type=str, default=None,
                        help="Con --fix: CSV donde se listan las URLs cuya fecha de publicacion "
                             "no se pudo interpretar y se reemplazo por scraping_date")
    args = parser.parse_args()

    target = Path(args.path)
    output_dir = Path(args.report)

    # Modo correccion
    if args.fix:
        if target.is_dir():
            sys.exit("ERROR: --fix solo acepta un archivo, no un directorio.")
        if not target.exists():
            sys.exit(f"ERROR: archivo no encontrado: {target}")

        out_path = Path(args.output) if args.output else target.with_name(target.stem + "_clean" + target.suffix)
        stats = fix_file(target, out_path, Path(args.inferred_out) if args.inferred_out else None)
        print_fix_summary(stats, out_path)

        # Validar el resultado
        print()
        print("Validando corpus corregido...")
        report = validate_file(out_path)
        print_report(report)
        report_path = save_report({"checked_files": 1, "files": [report]}, output_dir)
        print(f"Reporte guardado en: {report_path}")
        if not report["ok"]:
            sys.exit(1)
        return

    # Modo validacion
    files = collect_csv_files(target)
    if not files:
        raise SystemExit(f"No se encontraron archivos CSV en: {target}")

    overall = {
        "checked_files": len(files),
        "valid_files": 0,
        "invalid_files": 0,
        "files": [],
    }

    for file_path in files:
        report = validate_file(file_path)
        overall["files"].append(report)
        if report["ok"]:
            overall["valid_files"] += 1
        else:
            overall["invalid_files"] += 1
        print_report(report)

    overall_report_path = save_report(overall, output_dir)
    print(f"Reporte global guardado en: {overall_report_path}")
    print(f"Archivos validados: {overall['checked_files']}")
    print(f"Archivos OK: {overall['valid_files']}")
    print(f"Archivos con errores: {overall['invalid_files']}")

    if overall["invalid_files"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
