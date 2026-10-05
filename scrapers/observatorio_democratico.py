# ============================================================
# SCRAPER: Observatorio_Democratico
# ============================================================
#
# REQUISITOS PARA EJECUTAR:
#
# 1. Instalar dependencias Python:
#      pip install requests beautifulsoup4 pandas urllib3
#
# Ejecutar con:
#      python Observatorio_Democratico.py
#
# NOTAS IMPORTANTES:
#   - Este scraper usa la API REST de WordPress (/wp-json/wp/v2/posts)
#     por lo que funciona únicamente con sitios basados en WordPress.
#   - Genera un CSV individual por cada sitio de la lista SITES.
#   - Para limitar la cantidad de páginas descargadas (modo test),
#     cambiar max_pages=None por max_pages=2 en la función main().
#   - Los archivos de salida y el log se guardan en el directorio
#     desde donde se ejecute el script.
# ============================================================

import os
import sys
import requests
import re
import datetime
import pandas as pd
from bs4 import BeautifulSoup
import time
import urllib3

# Disable SSL warnings for pages with misconfigured certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Modo incremental: misma clave de URL que PostgreSQL y que BaseScraper
# (output_cleaner.url_key). El script corre como subproceso, así que se agrega
# la raíz del repo al path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from output_cleaner import url_key

# =========================================================
# CONFIGURACIÓN MAESTRA
# =========================================================

# Lista de todos los portales noticiosos requeridos
SITES = [
    # DEAD (DNS fail 2026-07): {"url": "https://anexioncr.com", "name": "anexioncr"},
    {"url": "https://guanacastealaaltura.com", "name": "guanacastealaaltura"},
    # Joomla — handled by procesar_periodicomensaje(): {"url": "https://periodicomensaje.com", "name": "periodicomensaje"},
    {"url": "https://radiolapampa.net", "name": "radiolapampa"},
    # DEAD cPanel placeholder (2026-07): {"url": "https://tamarindonews.com", "name": "tamarindonews"},
    # DEAD (DNS fail 2026-07): {"url": "https://yambaradio.com", "name": "yambaradio"},
    {"url": "https://miprensacr.com", "name": "miprensacr"},
    # {"url": "https://radiobahiapuerto.com", "name": "radiobahiapuerto"}, # Requiere Playwright
    {"url": "https://radiopuertotv.net", "name": "radiopuertotv"},
    {"url": "https://tvsur.co.cr", "name": "tvsur"},
    {"url": "https://ustedseinforma.com", "name": "ustedseinforma"},
    {"url": "https://adiariocr.com", "name": "adiariocr"},
    # DEAD (DNS fail 2026-07): {"url": "https://actualidaddeloeste.com", "name": "actualidaddeloeste"},
    # DEAD (DNS fail 2026-07): {"url": "https://alajuelitahoy.com", "name": "alajuelitahoy"},
    {"url": "https://alajuelitasoy.com", "name": "alajuelitasoy"},
    {"url": "https://buzonderodrigo.com", "name": "buzonderodrigo"},
    # {"url": "https://canalaltavision.com", "name": "canalaltavision"}, # En caso de que funcione
    {"url": "https://elcolectivo506.com", "name": "elcolectivo506"},
    # El Jilguero: el script de Colab solo leía la primera página de 2 categorías (19 notas);
    # la API trae las 153 del sitio (2020 a mar-2025, ya sin publicar).
    {"url": "https://jilgueromedia.com", "name": "el_jilguero"},
    {"url": "https://elmonitorcr.com", "name": "elmonitorcr"},
    # "elmundocr" (no "elmundo"): el_mundo era El Mundo de España y se quitó del pipeline;
    # el sufijo cr evita confundirlos. "desde": solo notas desde esa fecha (el sitio tiene
    # 114,000 desde 2015), igual que repretel (desde 2024).
    {"url": "https://elmundo.cr", "name": "elmundocr", "desde": "2024-01-01"},
    {"url": "https://enlamiracr.com", "name": "enlamira"}
]

# Timezone de Costa Rica
TZ_CR = datetime.timezone(datetime.timedelta(hours=-6))

# Log file path — set in main() so each subprocess invocation gets a unique
# name that includes the site filter and timestamp.
LOG_TXT: str | None = None

# Límite absoluto seguro para celdas de Excel (limita a 30,000 caracteres)
MAX_TEXT_LENGTH = 30000

# Limpiador drástico de Emojis que arruinan la codificación
STRICT_PATTERN = re.compile(r'[^\w\s\.,;:!?\-\(\)áéíóúÁÉÍÓÚñÑüÜ"\'/¿¡]', flags=re.UNICODE)

def log_msg(msg):
    if LOG_TXT:
        with open(LOG_TXT, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now(TZ_CR).strftime('%H:%M:%S')}] {msg}\n")
    print(msg)

def clean_extreme(text):
    """
    Rutina blindada contra rupturas de línea en CSV.
    Elimina caracteres de escape ocultos y limita el string.
    """
    if not isinstance(text, str): return ""

    # 1. Quitar HTML
    if '<' in text and '>' in text:
        try:
            soup = BeautifulSoup(text, "html.parser")
            text = soup.get_text(separator=' ')
        except:
            pass

    # 2. Remover Emojis super locos
    text = STRICT_PATTERN.sub('', text)

    # 3. Remover basuras repetitivas de Emojis fallidos (ej. ???)
    text = re.sub(r'\?{2,}\s*', ' ', text)

    # 4. Remover TODO salto de línea, retorno de carro y tabulador (CRÍTICO PARA EXCEL)
    text = text.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')

    # 5. Escapar las comillas dobles y sencillas que suelen romper CSV si se abren y no cierran
    text = text.replace('"', "'").replace('\u201c', "'").replace('\u201d', "'")

    # 6. Transformar el "pipe" en un guion, ya que usaremos pipe como separador de columnas
    text = text.replace('|', '-')

    # 7. Reducción de espacios extra
    text = re.sub(r'\s+', ' ', text).strip()

    # 8. Limitar para que Excel no colapse al volcar a una celda (Límite Excel: 32,767 caracteres)
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH] + "... [truncado]"

    return text

def parse_date(date_str):
    try:
        dt = datetime.datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_CR)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return datetime.datetime.now(TZ_CR).strftime('%Y-%m-%d %H:%M:%S')

def cargar_urls_conocidas(source):
    """
    Pide a N8N (GET ?source=<fuente>) las URLs normalizadas que ya están en
    PostgreSQL, igual que BaseScraper._cargar_urls_conocidas. Devuelve un set, o
    None si no hay variables N8N_URLS_CONOCIDAS_URL / N8N_WEBHOOK_TOKEN o si N8N
    no responde: en ese caso el script descarga todo, como siempre.
    """
    url = os.environ.get("N8N_URLS_CONOCIDAS_URL")
    token = os.environ.get("N8N_WEBHOOK_TOKEN")
    if not url or not token:
        return None
    try:
        r = requests.get(url, params={"source": source}, headers={"X-Webhook-Token": token}, timeout=(10, 120))
        r.raise_for_status()
        urls = r.json()["urls"]
        if not isinstance(urls, list):
            raise ValueError("'urls' no es una lista")
        return set(urls)
    except Exception as e:
        log_msg(f"-> No se pudo obtener la lista de URLs conocidas, se descarga todo ({e})")
        return None


def procesar_sitio(site_dict, max_pages=None):
    base_url = site_dict["url"]
    name = site_dict["name"]
    desde = site_dict.get("desde")   # 'AAAA-MM-DD': solo notas publicadas desde esa fecha
    idioma = site_dict.get("language", "es")   # los sitios en inglés (p. ej. news.co.cr) deben declararlo

    log_msg(f"\n==============================================")
    log_msg(f"Iniciando Extracción API para: {name.upper()}")
    log_msg(f"Target URL: {base_url}")
    if desde:
        log_msg(f"Solo notas desde {desde}")
    log_msg(f"==============================================")

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-CR,es;q=0.9,en;q=0.8",
        # No Accept-Encoding: let requests advertise only gzip/deflate which it can decode
        "DNT": "1",
        "Connection": "keep-alive",
    })

    # Modo incremental y tope de tiempo (mismo contrato que BaseScraper):
    #  - known: URLs ya cargadas en PostgreSQL (None = descargar todo, como antes)
    #  - SCRAPER_MODO_COMPLETO=1: recorre todo el historial pero sigue omitiendo lo ya cargado
    #  - SCRAPER_MAX_MINUTOS: al agotarse se guarda lo reunido hasta ese momento
    known = cargar_urls_conocidas(name)
    modo_completo = os.environ.get("SCRAPER_MODO_COMPLETO") == "1"
    max_min = float(os.environ.get("SCRAPER_MAX_MINUTOS") or 0) or None
    t_fin = time.monotonic() + max_min * 60 if max_min else None
    if known is not None:
        log_msg(f"-> Modo {'completo (sin cortar)' if modo_completo else 'incremental'}: {len(known):,} URLs ya conocidas")

    # Auto-detect API route: modern /wp-json/ or legacy /?rest_route=
    legacy_mode = False

    def _probe_is_wp_json(url):
        for attempt in range(4):
            try:
                r = session.get(url, timeout=15, verify=False)
                if r.status_code in (429, 503):
                    # Límite de frecuencia del hosting (p. ej. elcolectivo506): esperar y reintentar
                    log_msg(f"   [probe {attempt+1}/4] HTTP {r.status_code}, esperando 20s...")
                    time.sleep(20)
                    continue
                if r.status_code != 200:
                    return False
                if not r.content:
                    time.sleep(3)
                    continue
                data = r.json()
                return isinstance(data, list)
            except ValueError:
                time.sleep(3)
                continue
            except Exception:
                return False
        return False

    probe_modern = f"{base_url}/wp-json/wp/v2/posts?per_page=1"
    probe_legacy = f"{base_url}/?rest_route=/wp/v2/posts&per_page=1"
    if _probe_is_wp_json(probe_modern):
        legacy_mode = False
        log_msg(f"-> API mode: /wp-json/ (moderno)")
    elif _probe_is_wp_json(probe_legacy):
        legacy_mode = True
        log_msg(f"-> API mode: /?rest_route= (legado)")
    else:
        log_msg(f"-> No se encontro API WordPress activa. Abortando.")
        return

    def _api_url(path, **params):
        if legacy_mode:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            return f"{base_url}/?rest_route={path}&{qs}" if qs else f"{base_url}/?rest_route={path}"
        else:
            return f"{base_url}/wp-json{path}"

    def _safe_get(url, params=None, timeout=45, retries=5):
        for attempt in range(retries):
            try:
                r = session.get(url, params=(params if not legacy_mode else None), timeout=timeout, verify=False)
                if r.status_code in (429, 503):
                    wait = min(20 * (attempt + 1), 60)
                    if t_fin and time.monotonic() + wait > t_fin:
                        return r   # sin tiempo para esperar: se devuelve el 429 y el sitio se corta
                    log_msg(f"   [retry {attempt+1}/{retries}] HTTP {r.status_code}, esperando {wait}s...")
                    time.sleep(wait)
                    continue
                return r
            except requests.exceptions.Timeout:
                if attempt < retries - 1:
                    log_msg(f"   [retry {attempt+1}/{retries}] Timeout, reintentando...")
                    time.sleep(5)
                    continue
                raise
            except requests.exceptions.ConnectionError:
                if attempt < retries - 1:
                    time.sleep(5)
                    continue
                raise
        return r

    # 1. Obtener Categorías
    cat_mapping = {}
    try:
        c_url = _api_url("/wp/v2/categories", per_page=100)
        c_req = _safe_get(c_url, params={"per_page": 100} if not legacy_mode else None, timeout=15)
        if c_req.status_code == 200:
            for cat in c_req.json():
                cat_mapping[cat['id']] = cat['name']
        log_msg(f"-> Mapeadas {len(cat_mapping)} categorias.")
    except Exception as e:
        log_msg(f"-> Advertencia: No se pudieron mapear categorias ({e}). Todo ira como 'Noticias'.")

    # 2. Descarga Histórica Recursiva
    dataset = []
    page = 1
    posts_url = _api_url("/wp/v2/posts")
    conocidas_seguidas = 0

    while True:
        if t_fin and time.monotonic() > t_fin:
            log_msg(f"-> Tope de tiempo alcanzado ({max_min:g} min): se guarda lo reunido hasta ahora.")
            break
        try:
            after = f"&after={desde}T00:00:00" if desde else ""
            if legacy_mode:
                page_url = f"{base_url}/?rest_route=/wp/v2/posts&per_page=100&page={page}{after}"
                resp = _safe_get(page_url, timeout=45)
            else:
                params = {"per_page": 100, "page": page}
                if desde:
                    params["after"] = f"{desde}T00:00:00"
                resp = _safe_get(posts_url, params=params, timeout=45)

            if resp.status_code != 200:
                if "rest_post_invalid_page_number" in resp.text or resp.status_code == 400:
                    log_msg(f"-> Fin del historial alcanzado en la pagina {page-1}.")
                else:
                    log_msg(f"-> El sitio rechazo la solicitud (codigo {resp.status_code}). Abortando sitio.")
                break

            data = resp.json()
            if not data:
                break

            n_conocidos_pagina = 0
            for post in data:
                link = post.get("link", "")
                if known is not None and url_key(link) in known:
                    n_conocidos_pagina += 1
                    continue
                title_raw = post.get("title", {}).get("rendered", "")
                content_raw = post.get("content", {}).get("rendered", "")
                date_raw = post.get("date", "")

                cats_ids = post.get("categories", [])
                section_raw = "Noticias"
                if cats_ids and len(cat_mapping) > 0:
                    section_raw = cat_mapping.get(cats_ids[0], "Noticias")

                title_clean = clean_extreme(title_raw)
                content_clean = clean_extreme(content_raw)

                if not title_clean and not content_clean:
                    continue

                dataset.append({
                    "source": name,
                    "url": link,
                    "title": title_clean,
                    "publication_date": parse_date(date_raw) if date_raw else "N/A",
                    "scraping_date": datetime.datetime.now(TZ_CR).strftime('%Y-%m-%d %H:%M:%S'),
                    "section": clean_extreme(section_raw),
                    "full_text": content_clean,
                    "language": idioma
                })

            log_msg(f"-> [{name}] Pagina {page} extraida (+{len(data)} items, {n_conocidos_pagina} ya conocidos)")

            # Modo incremental: las páginas vienen de la más nueva a la más vieja;
            # al llegar a 2 páginas seguidas solo con notas ya cargadas, el resto también lo está.
            if known is not None and not modo_completo:
                conocidas_seguidas = conocidas_seguidas + 1 if n_conocidos_pagina == len(data) else 0
                if conocidas_seguidas >= 2:
                    log_msg("-> 2 páginas seguidas con solo notas ya cargadas: fin (modo incremental).")
                    break

            # Respaldo de seguridad intermedio local
            if page % 10 == 0:
                df_temp = pd.DataFrame(dataset)
                df_temp.to_csv(os.path.join("output", f"{name}_backup.csv"), index=False, encoding='utf-8-sig', sep='|')

            if max_pages and page >= max_pages:
                log_msg(f"-> Limite artificial de test ({max_pages} pags) alcanzado.")
                break

            page += 1
            time.sleep(1)  # Precaucion anti-ban

        except requests.exceptions.RequestException as e:
            log_msg(f"-> Falla de conexion critica en pagina {page}: {e}. Abortando {name}.")
            break
        except Exception as e:
            log_msg(f"-> Excepcion en {name} pag {page}: {e}")
            break

    # 3. Guardado final del CSV por sitio
    if dataset:
        df = pd.DataFrame(dataset)
        df = df.drop_duplicates(subset=['url'])
        df = df[['source', 'url', 'title', 'publication_date', 'scraping_date', 'section', 'full_text', 'language']]

        final_filename = os.path.join("output", f"{name}_{datetime.datetime.now(TZ_CR).strftime('%Y%m%d')}.csv")
        df.to_csv(final_filename, index=False, encoding='utf-8-sig', sep='|')
        log_msg(f"SUCCESS: {len(df)} registros totales consolidados en {final_filename}")

        # Descarga automática en Google Colab (ignorado fuera de Colab)
        try:
            from google.colab import files
            log_msg(f"-> Forzando descarga automática del archivo {final_filename} al navegador...")
            files.download(final_filename)
        except ImportError:
            pass

    elif known is not None and page > 1:
        # Modo incremental: ninguna nota nueva desde la última corrida no es un error.
        # Se deja un CSV solo con el encabezado para que el adaptador sepa que el
        # script corrió bien (si la API no respondió, page vale 1 y cae al WARNING).
        final_filename = os.path.join("output", f"{name}_{datetime.datetime.now(TZ_CR).strftime('%Y%m%d')}.csv")
        pd.DataFrame(columns=['source', 'url', 'title', 'publication_date', 'scraping_date',
                              'section', 'full_text', 'language']).to_csv(
            final_filename, index=False, encoding='utf-8-sig', sep='|')
        log_msg(f"SUCCESS: sin notas nuevas para {name} (0 registros) → {final_filename}")

    else:
        log_msg(f"WARNING: API vacía o inalcanzable para {name}.")


CATEGORIES_PERIODICOMENSAJE = [
    "ambientales", "cantonales", "cultura", "deportes", "educacion",
    "eventos", "guanacaste", "otras/finanzas", "otras/social",
    "otras/tecnologia", "otras/valores", "salud", "turismo-negocios",
]

def procesar_periodicomensaje(max_pages_per_cat=None):
    name = "periodicomensaje"
    base_url = "https://periodicomensaje.com"

    log_msg(f"\n==============================================")
    log_msg(f"Iniciando Extraccion Joomla para: {name.upper()}")
    log_msg(f"==============================================")

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-CR,es;q=0.9,en;q=0.8",
        "DNT": "1",
        "Connection": "keep-alive",
    })

    JOOMLA_PAGE_SIZE = 8

    # Modo incremental (igual que procesar_sitio): known = URLs ya cargadas en PostgreSQL,
    # None = descargar todo. SCRAPER_MODO_COMPLETO=1 recorre todo sin cortar; SCRAPER_MAX_MINUTOS
    # limita el tiempo (el listado usa la mitad, el resto es para abrir notas).
    known = cargar_urls_conocidas(name)
    modo_completo = os.environ.get("SCRAPER_MODO_COMPLETO") == "1"
    incremental = known is not None and not modo_completo
    max_min = float(os.environ.get("SCRAPER_MAX_MINUTOS") or 0) or None
    t_ini = time.monotonic()
    t_fin = t_ini + max_min * 60 if max_min else None
    t_listado = t_ini + max_min * 30 if max_min else None
    if known is not None:
        log_msg(f"-> Modo {'completo (sin cortar)' if modo_completo else 'incremental'}: {len(known):,} URLs ya conocidas")

    def _es_conocida(u):
        return known is not None and url_key(u) in known

    def _collect_article_links(html):
        links = []
        for href in re.findall(r'href=["\']([^"\']*?/\d{3,}-[^"\']+)', html):
            if href.startswith("/"):
                href = base_url + href
            if "periodicomensaje.com" in href and re.search(r'/\d{3,}-', href):
                links.append(href.split("?")[0].split("#")[0])
        return list(set(links))

    def _extract_article(url):
        try:
            r = session.get(url, timeout=20, verify=False)
            if r.status_code != 200:
                return None
            soup = BeautifulSoup(r.content, "html.parser")

            h1 = soup.find("h1", itemprop="headline") or soup.find("h1")
            title = clean_extreme(h1.get_text(strip=True)) if h1 else ""

            time_el = soup.find("time", attrs={"datetime": True})
            pub_date = parse_date(time_el["datetime"]) if time_el else datetime.datetime.now(TZ_CR).strftime("%Y-%m-%d %H:%M:%S")

            body_div = soup.find("div", class_="com-content-article__body")
            content = clean_extreme(body_div.get_text(separator=" ", strip=True)) if body_div else ""

            m = re.search(r'periodicomensaje\.com/([^/]+)(?:/[^/]+)?/', url)
            section = m.group(1).replace("-", " ").title() if m else "Noticias"

            if not title and not content:
                return None

            return {
                "source": name,
                "url": url,
                "title": title,
                "publication_date": pub_date,
                "scraping_date": datetime.datetime.now(TZ_CR).strftime("%Y-%m-%d %H:%M:%S"),
                "section": clean_extreme(section),
                "full_text": content,
                "language": "es",
            }
        except Exception as e:
            log_msg(f"   -> Error al extraer {url}: {e}")
            return None

    def _get_max_start(html):
        starts = re.findall(r'start=(\d+)', html)
        return max((int(s) for s in starts), default=0)

    # Phase 1: collect all article URLs via category pagination
    all_urls = set()
    for cat in CATEGORIES_PERIODICOMENSAJE:
        log_msg(f"-> Categoria: /{cat}")
        try:
            r0 = session.get(f"{base_url}/{cat}", timeout=15, verify=False)
            max_start = _get_max_start(r0.text)
            initial_links = _collect_article_links(r0.text)
            all_urls.update(initial_links)
            log_msg(f"   start=0: {len(initial_links)} links, max_start={max_start}")

            if max_pages_per_cat:
                max_start = min(max_start, (max_pages_per_cat - 1) * JOOMLA_PAGE_SIZE)

            start = JOOMLA_PAGE_SIZE
            pages = 1
            # Las categorías van de la nota más nueva a la más vieja: 2 páginas seguidas solo con
            # notas ya cargadas significan que el resto de la categoría también lo está.
            conocidas_seguidas = 1 if incremental and initial_links and all(_es_conocida(l) for l in initial_links) else 0
            while start <= max_start:
                if incremental and conocidas_seguidas >= 2:
                    log_msg(f"   2 páginas seguidas con solo notas ya cargadas: fin de /{cat}")
                    break
                if t_listado and time.monotonic() > t_listado:
                    log_msg(f"   Tope de tiempo del listado alcanzado en /{cat}")
                    break
                try:
                    r = session.get(f"{base_url}/{cat}?start={start}", timeout=15, verify=False)
                    links = _collect_article_links(r.text)
                    all_urls.update(links)
                    conocidas_seguidas = conocidas_seguidas + 1 if incremental and links and all(_es_conocida(l) for l in links) else 0
                    if pages % 20 == 0 or start == max_start:
                        log_msg(f"   start={start}/{max_start}: total_unique={len(all_urls)}")
                    start += JOOMLA_PAGE_SIZE
                    pages += 1
                    time.sleep(0.3)
                except Exception as e:
                    log_msg(f"   Error en /{cat}?start={start}: {e}")
                    break
        except Exception as e:
            log_msg(f"   Error en /{cat}: {e}")
            continue

    log_msg(f"-> URLs unicas colectadas: {len(all_urls)}")
    if known is not None:
        all_urls = {u for u in all_urls if not _es_conocida(u)}
        log_msg(f"-> Nuevas (no cargadas aún): {len(all_urls)}")

    # Phase 2: visit each article and extract content (las más nuevas primero: id más alto)
    def _id_nota(u):
        m = re.search(r'/(\d{3,})-', u)
        return int(m.group(1)) if m else 0

    # Joomla sirve la misma nota bajo varias rutas (/guanacaste/15510-... y /15510-...-repetido):
    # el número de nota es único, así que se conserva una sola URL por número (la más corta).
    por_id = {}
    for u in all_urls:
        k = _id_nota(u) or u
        if k not in por_id or len(u) < len(por_id[k]):
            por_id[k] = u
    all_urls = set(por_id.values())

    dataset = []
    for i, url in enumerate(sorted(all_urls, key=_id_nota, reverse=True)):
        if t_fin and time.monotonic() > t_fin:
            log_msg(f"   Tope de tiempo alcanzado: se guardan {len(dataset)} notas, el resto queda para la próxima corrida")
            break
        article = _extract_article(url)
        if article:
            dataset.append(article)
        if (i + 1) % 50 == 0:
            log_msg(f"   [{i+1}/{len(all_urls)}] extraidos: {len(dataset)}")
        time.sleep(0.5)

    if dataset:
        df = pd.DataFrame(dataset)
        df = df.drop_duplicates(subset=["url"])
        df = df[["source","url","title","publication_date","scraping_date","section","full_text","language"]]
        fname = os.path.join("output", f"{name}_{datetime.datetime.now(TZ_CR).strftime('%Y%m%d')}.csv")
        df.to_csv(fname, index=False, encoding="utf-8-sig", sep="|")
        log_msg(f"SUCCESS: {len(df)} registros en {fname}")
    elif known is not None and not all_urls:
        # Modo incremental: ninguna nota nueva no es un error; CSV solo con encabezado (ver procesar_sitio)
        fname = os.path.join("output", f"{name}_{datetime.datetime.now(TZ_CR).strftime('%Y%m%d')}.csv")
        pd.DataFrame(columns=["source","url","title","publication_date","scraping_date","section","full_text","language"]).to_csv(
            fname, index=False, encoding="utf-8-sig", sep="|")
        log_msg(f"SUCCESS: sin notas nuevas para {name} (0 registros) → {fname}")
    else:
        log_msg(f"WARNING: Sin datos para {name}.")


def main():
    global LOG_TXT

    # Filtro opcional: si se pasa un nombre de sitio como argumento,
    # procesar solo ese sitio. Si no se pasa argumento, procesar todos.
    # IMPORTANTE: max_pages se usa sólo para testear (ej. max_pages=2).
    # Usar max_pages=None para descargar el historial completo.
    _site_filter = sys.argv[1] if len(sys.argv) > 1 else None

    # Compute log path per invocation: includes site name (if filtered) so
    # parallel subprocess calls never share the same log file.
    os.makedirs("logs", exist_ok=True)
    os.makedirs("output", exist_ok=True)
    timestamp = datetime.datetime.now(TZ_CR).strftime('%Y%m%d_%H%M%S')
    suffix = f"_{_site_filter}" if _site_filter else "_all"
    LOG_TXT = os.path.join("logs", f"log_master_{timestamp}{suffix}.txt")

    with open(LOG_TXT, "w", encoding="utf-8") as f:
        f.write(f"=== MASTER SCRAPER INICIADO ({timestamp}{suffix}) ===\n")

    # Joomla sites
    if not _site_filter or _site_filter.lower() == "periodicomensaje":
        procesar_periodicomensaje()

    # WordPress sites
    for site in SITES:
        if _site_filter and site.get("name", "").lower() != _site_filter.lower():
            continue
        procesar_sitio(site)


if __name__ == "__main__":
    main()
