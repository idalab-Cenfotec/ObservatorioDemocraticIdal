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
    "elperiodicocr":      "https://elperiodicocr.com",
    "lareaccioncr":       "https://lareaccioncr.com",
    "larevistacr":        "https://larevistacr.com",
    "latejacr":           "https://lateja.cr",
    "lavozdegoicoechea":  "https://lavozdegoicoechea.info",
    "monumental":         "https://www.monumental.co.cr",
    "mundiario":          "https://www.mundiario.com",
    "ncrnoticias":        "https://www.ncrnoticias.com",
    "noticiasenlineacr":  "https://www.noticiasenlineacr.com",
    "costaricastar":      "https://www.costaricastar.com",
    "genteopa":           "https://genteopa.com",
    "pulsocr":            "https://pulsocr.com",
    "puroperiodismo":     "https://puroperiodismo.com",
    "repretel":           "https://repretel.com/noticias",
    "rumboeconomico":     "https://rumboeconomico.net",
    "sinartdigital":      "https://sinartdigital.com",
    "telediario":         "https://telediario.cr",
    "teletica":           "https://teletica.com/noticias",
    "theglobalcr":        "https://theglobalcr.com",
    "ticotimes":          "https://ticotimes.net",
    "trivisioncr":        "https://trivisioncr.com",
    "vozdeguanacaste":    "https://vozdeguanacaste.com",
    "ticosland":          "https://ticosland.com",
    "puntarenasseoye":    "https://puntarenasseoye.com",
    "caribeactual":       "https://caribeactual.com",
    "presidencia":        "https://www.presidencia.go.cr/noticias",
    "anexioncr":          "https://anexioncr.com",
    "guanacastealaaltura":"https://guanacastealaaltura.com",
    "periodicomensaje":   "https://periodicomensaje.com",
    "radiolapampa":       "https://radiolapampa.com",
    "tamarindonews":      "https://tamarindonews.com",
    "yambaradio":         "https://yambaradio.com",
    "miprensacr":         "https://miprensacr.com",
    "radiopuertotv":      "https://radiopuertotv.com",
    "tvsur":              "https://tvsur.cr",
    "ustedseinforma":     "https://ustedseinforma.com",
    "adiariocr":          "https://adiariocr.com",
    "actualidaddeloeste": "https://actualidaddeloeste.com",
    "alajuelitahoy":      "https://alajuelitahoy.com",
    "alajuelitasoy":      "https://alajuelitasoy.com",
    "buzonderodrigo":     "https://buzonderodrigo.com",
    "elcolectivo506":     "https://elcolectivo506.com",
    "elmonitorcr":        "https://elmonitorcr.com",
    "elmundo":            "https://elmundo.cr",
    "enlamira":           "https://enlamira.com",
    "acontecercr":        "https://acontecercr.com",
    "eljornalcr":         "https://eljornalcr.com",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


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
