"""
limpieza_relleno.py
───────────────────
Quita del texto de las notas el relleno que los sitios repiten en todas sus páginas (invitaciones a
suscribirse, datos de contacto, biografías de autor, avisos de IA, widgets) y descarta las notas que son
plantillas de prueba. Ese relleno entra por el selector de contenido de cada sitio, no es parte de la
noticia, y distorsiona lo que hacen después los agentes (embeddings, clasificación, entidades).

Se aplica en output_cleaner.clean_dataframe, o sea, a todo lo que se envía a PostgreSQL. El texto original
queda intacto en `raw_text`; `full_text` guarda la versión normalizada.

Tipos de regla (por fuente):
  inicio     el patrón (anclado con ^) se elimina del comienzo del texto
  fin        el patrón (anclado con $) se elimina del final del texto
  cualquiera el patrón se elimina donde aparezca (frases fijas que a veces quedan a medio texto)
  corte      se elimina desde donde empieza el patrón hasta el final (todo lo que sigue es del sitio:
             listas de otras noticias, widgets)

Cómo agregar una regla: copiar el texto exacto del relleno de 2 o 3 notas reales de la fuente, escribir el
patrón y agregar un caso en tests/test_limpieza_relleno.py. Las reglas son deliberadamente específicas:
es preferible dejar relleno que recortar contenido.
"""
import re

# Versión de la normalización; se guarda en articles.normalization_version cuando se aplican estas reglas.
NORMALIZATION_VERSION = "n1"

# No se recorta si lo que queda es más corto que esto: una nota que es casi solo relleno se deja como está
# para que la decisión (descartarla o no) sea visible y no un recorte silencioso.
MIN_RESTANTE = 100

_S = re.S    # DOTALL
_I = re.I

REGLAS: dict[str, dict[str, list[re.Pattern]]] = {
    "puntarenasseoye": {"fin": [re.compile(
        r"\s*CANAL OFICIAL.{0,80}?Puntarenas Se Oye en WhatsApp.{0,40}?Recibí las noticias más importantes de "
        r"Puntarenas directo en tu teléfono\.\s*Sin spam, solo lo que importa\..{0,40}?Gratis · Sin costo · Podés salir "
        r"cuando quieras.{0,40}$", _S)]},
    "miprensacr": {
        "inicio": [re.compile(
            r"^(?:Foto[^.]{0,80}\.\s*)?Franklin Castro R(?:amírez|\.)?\s+franklindecostaricagmail\.com\s+Whatsapp\s*"
            r"\(506\)\s*\d{8}\s*")],
        "cualquiera": [re.compile(
            r"\s*(?:Vea|ea)\s+aquí más información de Mi Prensa\s*,?\s*Juntos Somos más!\s*Horarios y tarifas de servicio "
            r"de ferry a Paquera aquí\s*Visite nuestro canal de Youtube")],
    },
    "repretel": {"cualquiera": [re.compile(
        r"\s*Si quiere estar atento a otras informaciones, puede seguir las ediciones de Noticias Repretel en Canal 6, "
        r"Repretel\.com, Facebook Live y YouTube de Grupo Repretel Noticias\.")]},
    "lavozdegoicoechea": {"fin": [re.compile(
        r"\s*Teléfono:\s*\+506\s*6226-2075\s*Correo:\s*info@lavozdegoicoechea\.com\s*Dirección:\s*San José, Goicoechea, "
        r"Guadalupe\s*$")]},
    "eldelfino": {
        # Biografía del autor + invitación al newsletter, siempre al inicio de la nota
        "inicio": [re.compile(
            r"^.{0,700}?De martes a viernes le contamos las noticias más relevantes del acontecer nacional como solo "
            r"Delfino\.cr puede hacerlo\.(?:\s*En cualquier momento puede salirse de la lista de correos\.)?\s*", _S)],
        "fin": [re.compile(
            r"\s*(?:Este artículo representa el criterio de quien lo firma\.\s*)?Los artículos de opinión publicados no "
            r"reflejan necesariamente la posición editorial de este medio\.\s*Delfino\.CR es un medio independiente, "
            r"abierto a la opinión de sus lectores\.\s*Si desea publicar en Teclado Abierto, consulte nuestra guía para "
            r"averiguar cómo hacerlo\.\s*$")],
    },
    "digital506": {"inicio": [re.compile(
        r"^Por Redacción\.\s*Esta nota fue elaborada con asistencia de IA\s*(?:/\s*)?(?:Imagen tomada de internet\s*)?")]},
    # Después de "Noticias de X en Whatsapp / en Facebook" el sitio pega titulares de otras notas
    "caribeactual": {"corte": [re.compile(
        r"\s*(?:Something went wrong\. Please refresh the page and/or try again\.\s*)?"
        r"Noticias de [^\n]{1,40}? en Whatsapp\s*Noticias de [^\n]{1,40}? en Facebook.*$", _S)]},
    # Notas de BBC News Mundo republicadas: el pie con invitaciones (newsletter, app, redes) cambia de redacción
    "teletica": {"corte": [re.compile(
        r"\s*Haz clic (?:aquí )?para leer más historias de BBC News Mundo\..{0,800}$", _S)],
        "fin": [re.compile(
        r"\s*Suscríbete aquí a nuestro (?:nuevo )?newsletter para recibir cada viernes.{0,600}?"
        r"Descarga la última versión y actívalas\.?\s*$", _S)]},
    "adiariocr": {"fin": [re.compile(
        r"\s*Proponga su solución\s*Queremos que usted sea parte de la solución\.\s*Coméntenos de qué forma se podría "
        r"mejorar la situación descrita en la nota\.\s*Cada semana destacaremos y le daremos seguimiento a las "
        r"principales soluciones propuestas por nuestros lectores\.\s*Nombre\s*Su solución\s*$")]},
    # "loadmoduleid 212" es un marcador del gestor de módulos del sitio, puede quedar al final o a medio texto
    "guanacastealaaltura": {"cualquiera": [re.compile(r"\s*loadmoduleid\s+\d+")]},
    "acontecer_cr": {"cualquiera": [re.compile(
        r"\s*Apoye al periodismo independiente y objetivo que hace Acontecer\.co\.cr/?\s*con nuestras suscripciones "
        r"solidarias", _I)]},
    "vozdeguanacaste": {"inicio": [re.compile(
        r"^\(Unite a nuestro canal de WhatsApp y recibí las noticias directo en tu cel\)\.\s*")]},
    "ticotimes": {"fin": [re.compile(
        r"\s*Live prediction market odds via Kalshi\.\s*Updates every 60 seconds\.\s*Kalshi is available to US residents "
        r"18\+\.\s*The Tico Times may earn a commission from new signups\.\s*$")]},
}

# Idioma de cada fuente. Los scrapers lo ponían con langdetect nota por nota (falla con textos cortos:
# marcaba como inglés notas en español) o fijo y mal (ticosland salía como español). Todo el corpus es de medios
# costarricenses en español salvo estos tres medios en inglés, así que el idioma se decide por la fuente.
IDIOMA_POR_FUENTE = {"ticotimes": "en", "ticosland": "en", "costaricastar": "en"}
IDIOMA_POR_DEFECTO = "es"


def idioma_de_fuente(fuente: str) -> str:
    return IDIOMA_POR_FUENTE.get(fuente, IDIOMA_POR_DEFECTO)


# Una nota en inglés dentro de un medio en español (p. ej. un comunicado) sí debe quedar como inglés, pero solo si
# el detector está casi seguro: langdetect sobre textos cortos o con citas confunde idiomas.
PROB_MINIMA_INGLES = 0.99
LARGO_MINIMO_DETECCION = 300
_MUESTRA_DETECCION = 1500


def idioma_de_nota(fuente: str, texto: str) -> str:
    """Idioma de una nota: el de su fuente, salvo que sea un texto largo que el detector da como inglés
    con probabilidad >= PROB_MINIMA_INGLES en una fuente en español."""
    base = idioma_de_fuente(fuente)
    if fuente in IDIOMA_POR_FUENTE or not isinstance(texto, str) or len(texto) < LARGO_MINIMO_DETECCION:
        return base
    try:
        from langdetect import DetectorFactory, detect_langs
        DetectorFactory.seed = 0
        mejor = detect_langs(texto[:_MUESTRA_DETECCION])[0]
    except Exception:
        return base
    return "en" if mejor.lang == "en" and mejor.prob >= PROB_MINIMA_INGLES else base


# Notas que son contenido de plantilla del tema del sitio (no noticias).
_PLANTILLA = re.compile(r"lorem ipsum|mauris mattis auctor cursus", re.IGNORECASE)


def es_plantilla(texto: str) -> bool:
    """True si el texto es relleno de plantilla ("lorem ipsum"), no una noticia."""
    return bool(texto) and bool(_PLANTILLA.search(texto))


def limpiar_relleno(fuente: str, texto: str) -> str:
    """Devuelve el texto sin el relleno conocido de la fuente (o el mismo texto si no aplica)."""
    reglas = REGLAS.get(fuente)
    if not reglas or not isinstance(texto, str) or not texto:
        return texto
    actual = texto
    for tipo in ("inicio", "fin", "cualquiera", "corte"):
        for patron in reglas.get(tipo, []):
            nuevo = patron.sub("", actual, count=0 if tipo == "cualquiera" else 1).strip()
            if nuevo != actual.strip() and len(nuevo) >= MIN_RESTANTE:
                actual = nuevo
    return actual
