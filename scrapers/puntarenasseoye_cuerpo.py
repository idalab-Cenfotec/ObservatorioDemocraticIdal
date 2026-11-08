"""
Extracción del cuerpo de las notas de puntarenasseoye.com.

Vive en un módulo aparte, sin Playwright, para poder probarlo con BeautifulSoup.

Por qué existe: el scraper tomaba solo las etiquetas <p> de `div.entry-content`. En algunas publicaciones
(sobre todo sucesos) la redacción pone los párrafos en <div> sueltos y solo el ÚLTIMO es un <p>, así que la
nota quedaba guardada con su último párrafo ("El caso queda ahora en manos del OIJ...") y sin el hecho.
Cuando ese último párrafo medía menos de 300 caracteres la nota se descartaba sin aviso.
"""
from bs4 import BeautifulSoup

# Contenido que no es texto de la nota y se elimina en cualquier posición: publicidad y la invitación a WhatsApp
# (`.stream-item`), etiquetas y compartir (`.post-bottom-meta`), listas de enlaces ("LEA TAMBIÉN") y multimedia.
# Se eliminan antes de leer porque, si un <p> queda sin cerrar, estos bloques quedan anidados dentro de él.
_IGNORADOS = (
    ".stream-item, .prb, .prb-ad, .post-bottom-meta, .post-tags-modern, .post-bottom-tags, .share-links, "
    ".wp-video, video, "
    "script, style, iframe, figure, figcaption, ul, ol, nav, aside, form, noscript"
)


_BLOQUES = ["div", "p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "section", "table", "tr", "td"]


def _texto(tag) -> str:
    # Se separa solo en los límites de bloque y <br>; entre etiquetas en línea no (un enlace o una negrita no debe
    # dejar "Mata , de 61").
    for br in tag.find_all("br"):
        br.replace_with(" ")
    for bloque in tag.find_all(_BLOQUES):
        bloque.insert_before(" ")
        bloque.insert_after(" ")
    return " ".join(tag.get_text().split())


def extraer_cuerpo(html_contenedor: str) -> str:
    """Texto de la nota a partir del HTML interno de `div.entry-content`, en orden de documento.

    - Un <p> directo aporta su texto.
    - Un bloque que contiene <p> aporta cada uno de ellos (comportamiento anterior).
    - Un <div> sin <p> (párrafo suelto) aporta su texto.
    """
    raiz = BeautifulSoup(html_contenedor or "", "html.parser")
    for ajeno in raiz.select(_IGNORADOS):
        ajeno.decompose()
    partes = []
    for hijo in raiz.find_all(True, recursive=False):
        bloques = [hijo] if hijo.name == "p" or not hijo.find("p") else hijo.find_all("p")
        for b in bloques:
            t = _texto(b)
            if t:
                partes.append(t)
    return " ".join(partes)
