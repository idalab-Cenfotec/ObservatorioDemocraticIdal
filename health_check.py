"""
health_check.py
───────────────
Verificación liviana de cada sitio antes de lanzar el scraper completo.
Usa requests (sin Playwright) para minimizar tiempo y recursos.

Criterio: si el sitio responde HTTP < 400 está vivo.
El umbral de bytes fue eliminado — los redirects y protecciones anti-bot
devuelven respuestas cortas pero Playwright los maneja sin problema.

Uso independiente:
    python health_check.py                        # verifica todos
    python health_check.py teletica elperiodicocr # verifica específicos
"""

import requests
import time
import sys
from datetime import datetime, timezone, timedelta

CR_TZ          = timezone(timedelta(hours=-6))
TIMEOUT_S      = 10
MAX_RESPONSE_S = 8.0

HEALTH_URLS: dict[str, str] = {
    "adiariocr":             "https://adiariocr.com",
    "alajuela_digital":      "https://alajueladigital.net",
    "alajuelitasoy":         "https://alajuelitasoy.com",
    "amprensa":              "https://www.amprensa.com",
    "buzonderodrigo":        "https://buzonderodrigo.com",
    "caribeactual":          "https://caribeactual.com",
    "crc_89_1":              "https://crc891.com",
    "crhoy":                 "https://crhoy.com",
    "diarioextra":           "https://www.diarioextra.com",
    "digital506":            "https://digital506.com",
    "el_financiero":         "https://www.elfinancierocr.com",
    "el_jilguero":           "https://jilgueromedia.com",
    "el_jornal":             "https://eljornalcr.com",
    "el_observador":         "https://observador.cr",
    "el_sol_de_occidente":   "https://elsoldeoccidente.com",
    "elcolectivo506":        "https://elcolectivo506.com",
    "eldelfino":             "https://delfino.cr",
    "elmonitorcr":           "https://elmonitorcr.com",
    "elmundocr":             "https://elmundo.cr",
    "elnortehoy":            "https://elnortehoycr.com",
    "genteopa":              "https://genteopa.com",
    "guanacastealaaltura":   "https://www.guanacastealaaltura.com",
    "lanacion":              "https://www.nacion.com",
    "lareaccioncr":          "https://lareaccioncr.com",
    "larepublica":           "https://www.larepublica.net",
    "larevistacr":           "https://www.larevista.cr",
    "latejacr":              "https://www.lateja.cr",
    "lavozdegoicoechea":     "https://lavozdegoicoechea.com",
    "miprensacr":            "https://www.miprensacr.com",
    "monumental":            "https://www.monumental.co.cr",
    "noticias_la_garita_costa_rica": "https://www.noticiaslagaritacr.com",
    "noticiasenlineacr":     "https://noticiasenlineacr.com",
    "periodico_mi_tierra":   "https://periodicomitierra.com",
    "periodicomensaje":      "https://periodicomensaje.com",
    "presidencia":           "https://www.presidencia.go.cr",
    "pulsocr":               "https://www.pulsocr.com",
    "puntarenasseoye":       "https://www.puntarenasseoye.com",
    "puroperiodismo":        "https://www.puroperiodismo.com",
    "radiolapampa":          "https://radiolapampa.net",
    "radiopuertotv":         "https://radiopuertotv.net",
    "repretel":              "https://www.repretel.com",
    "sancarlosdigital":      "https://sancarlosdigital.com",
    "sinartdigital":         "https://sinartdigital.com",
    "telediario":            "https://www.telediario.cr",
    "teletica":              "https://www.teletica.com",
    "theglobalcr":           "https://theglobalcr.com",
    "ticosland":             "https://ticosland.com",
    "ticotimes":             "https://ticotimes.net",
    "tvsur":                 "https://www.tvsur.co.cr",
    "ustedseinforma":        "https://ustedseinforma.com",
    "vozdeguanacaste":       "https://vozdeguanacaste.com",
}

# User-Agent que se identifica (varios hostings responden 403 a un "Chrome/124" suelto).
HEADERS = {"User-Agent": "ObservatorioDemocratico/1.0 (investigacion academica, IDALab UCenfotec)"}


def check_one(source: str) -> dict:
    url = HEALTH_URLS.get(source)
    if not url:
        return {
            "source": source, "ok": True,
            "status_code": None, "duration_s": 0,
            "reason": "sin URL registrada — se permite pasar", "slow": False,
        }

    t0 = time.time()
    try:
        resp     = requests.get(url, timeout=TIMEOUT_S, headers=HEADERS, allow_redirects=True)
        duration = time.time() - t0
        slow     = duration > MAX_RESPONSE_S

        # Solo falla si el servidor devuelve error real (4xx o 5xx)
        if resp.status_code >= 400:
            return {
                "source": source, "ok": False,
                "status_code": resp.status_code, "duration_s": round(duration, 2),
                "reason": f"servidor devolvió error HTTP {resp.status_code}", "slow": slow,
            }

        return {
            "source": source, "ok": True,
            "status_code": resp.status_code, "duration_s": round(duration, 2),
            "reason": "ok", "slow": slow,
        }

    except requests.exceptions.ConnectionError:
        return {
            "source": source, "ok": False, "status_code": None,
            "duration_s": round(time.time() - t0, 2),
            "reason": "no se pudo conectar — sitio caído o DNS no resuelve",
            "slow": False,
        }
    except requests.exceptions.Timeout:
        return {
            "source": source, "ok": False, "status_code": None,
            "duration_s": TIMEOUT_S,
            "reason": f"sin respuesta después de {TIMEOUT_S}s", "slow": True,
        }
    except Exception as e:
        return {
            "source": source, "ok": False, "status_code": None,
            "duration_s": round(time.time() - t0, 2),
            "reason": str(e), "slow": False,
        }


def check_all(sources: list[str] | None = None) -> dict[str, dict]:
    targets = sources or list(HEALTH_URLS.keys())
    return {src: check_one(src) for src in targets}


def _print_results(results: dict):
    ok_count   = sum(1 for r in results.values() if r["ok"])
    fail_count = len(results) - ok_count
    slow_count = sum(1 for r in results.values() if r.get("slow"))

    print(f"\n{'─'*70}")
    print(f"  HEALTH CHECK — {datetime.now(CR_TZ).strftime('%Y-%m-%d %H:%M:%S CR')}")
    print(f"  Verificados: {len(results)}  ✓ {ok_count}  ✗ {fail_count}  ⚠ lentos: {slow_count}")
    print(f"{'─'*70}")
    for src, r in sorted(results.items()):
        icon = "✓" if r["ok"] else "✗"
        slow = " ⚠LENTO" if r.get("slow") else ""
        code = f"[{r['status_code']}]" if r["status_code"] else "[---]"
        print(f"  {icon} {src:<25} {code:^7} {r['duration_s']:>5.1f}s  {r['reason']}{slow}")
    print(f"{'─'*70}\n")


if __name__ == "__main__":
    sources = sys.argv[1:] if len(sys.argv) > 1 else None
    print("  Ejecutando health check...")
    results = check_all(sources)
    _print_results(results)
