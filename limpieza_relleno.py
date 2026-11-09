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
  reemplazar el patrón se cambia por un texto fijo para conservar la gramática (p. ej. el aviso de "correo
             protegido" que el sitio pone donde iba una dirección)

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
    "repretel": {
        # Invitación a seguir las ediciones, a veces seguida de "Le puede interesar: <titular de otra nota>"
        "fin": [re.compile(
            r"\s*Si quiere estar atento a otras informaciones, puede seguir las ediciones de Noticias Repretel en Canal 6, "
            r"Repretel\.com, Facebook Live y YouTube de (?:Grupo Repretel Noticias|Noticias Grupo Repretel)\."
            r"(?:\s*Le puede interesar:.{0,250})?\s*$", _S)],
        "cualquiera": [re.compile(
            r"\s*Si quiere estar atento a otras informaciones, puede seguir las ediciones de Noticias Repretel en Canal 6, "
            r"Repretel\.com, Facebook Live y YouTube de (?:Grupo Repretel Noticias|Noticias Grupo Repretel)\.")]},
    "lavozdegoicoechea": {
        # Línea del tema de WordPress al inicio de cada nota: "Published by Redacción on 11 agosto, 2026 11 agosto, 2026"
        # (la fecha sale repetida y, tras normalizar espacios, a veces pegada: "202611 agosto, 2026")
        "inicio": [re.compile(
            r"^Published by [^\n]{1,40}? on \d{1,2} [a-záéíóú]+,\s*\d{4}\s*\d{1,2} [a-záéíóú]+,\s*\d{4}\s*", _I)],
        "fin": [re.compile(
            r"\s*Teléfono:\s*\+506\s*6226-2075\s*Correo:\s*info@lavozdegoicoechea\.com\s*Dirección:\s*San José, Goicoechea, "
            r"Guadalupe\s*$")],
    },
    "eldelfino": {
        # Biografía del autor + invitación al newsletter, siempre al inicio de la nota
        "inicio": [re.compile(
            r"^.{0,700}?De martes a viernes le contamos las noticias más relevantes del acontecer nacional como solo "
            r"Delfino\.cr puede hacerlo\.(?:\s*En cualquier momento puede salirse de la lista de correos\.)?\s*", _S)],
        "fin": [re.compile(
            r"\s*(?:Este artículo representa el criterio de quien lo firma\.\s*)?Los artículos de opinión publicados no "
            r"reflejan necesariamente la posición editorial de este medio\.\s*Delfino\.CR es un medio independiente, "
            r"abierto a la opinión de sus lectores\.\s*Si desea publicar en Teclado Abierto, consulte nuestra guía para "
            r"averiguar cómo hacerlo\.\s*$"),
            # Variante corta: solo el aviso de opinión, sin la cadena de frases anterior
            re.compile(
            r"\s*(?:Este artículo representa el criterio de quien lo firma\.\s*)?Los artículos de opinión publicados no "
            r"reflejan necesariamente la posición editorial de este medio\.\s*$"),
            # Despedida fija del resumen internacional
            re.compile(r"\s*¡Gracias por acompañarnos en una entrega más del acontecer internacional!\s*$")],
    },
    # Aviso de opinión al final de las columnas
    "pulsocr": {"fin": [re.compile(
        r"\s*\*?\s*La opinión expresada en este espacio no necesariamente representa la línea editorial de PULSOCR, sino "
        r"exclusivamente responsabilidad de su autor\.\s*$")]},
    # Bloque de la página tras el artículo (etiquetas, artículo anterior/siguiente, más del autor, formulario de comentarios)
    "el_jornal": {"fin": [re.compile(
        r"\s*Etiquetas.{0,1500}?DEJA UNA RESPUESTA\s*Cancelar respuesta.{0,400}?"
        r"Guardar mi nombre, correo electrónico y sitio web en este navegador la próxima vez que comente\.\s*$", _S)]},
    # "Navegación de entradas" + título de la nota anterior/siguiente, al final de la nota
    "alajuela_digital": {"fin": [re.compile(r"\s*Navegación de entradas\s.{0,500}$", _S)]},
    "digital506": {"inicio": [re.compile(
        r"^Por Redacción\.\s*Esta nota fue elaborada con asistencia de IA\s*(?:/\s*)?(?:Imagen tomada de internet\s*)?")]},
    # Después de "Noticias de X en Whatsapp / en Facebook" el sitio pega titulares de otras notas
    "caribeactual": {"corte": [
        re.compile(
            r"\s*(?:Something went wrong\. Please refresh the page and/or try again\.\s*)?"
            r"Noticias de [^\n]{1,40}? en Whatsapp\s*Noticias de [^\n]{1,40}? en Facebook.*$", _S),
        # Redacción nueva del sitio (desde octubre de 2026): tras ella vienen titulares de otras notas y fechas sueltas
        re.compile(
            r"\s*¿Le interesan las noti[a-z]{1,6}s de [^?]{1,40}\?\s*Únase a nuestro grupo de noticias en Whatsapp.*$", _S)]},
    # Notas de BBC News Mundo republicadas: el pie con invitaciones (newsletter, app, redes) cambia de redacción
    "teletica": {"corte": [re.compile(
        r"\s*Haz clic (?:aquí )?para leer más historias de BBC News Mundo\..{0,800}$", _S)],
        "fin": [re.compile(
        r"\s*Suscríbete aquí a nuestro (?:nuevo )?newsletter para recibir cada viernes.{0,600}?"
        r"Descarga la última versión y actívalas\.?\s*$", _S),
        # Invitación de "Buen Día" a repasar las recetas/entrevistas en YouTube, al final de la nota
        re.compile(
        r"\s*(?:No olvide mandarnos sus fotos a las redes sociales de 'Buen Día'\.\s*)?Recuerde que (?:también )?puede "
        r"repasar todas las [^.]{1,80}? en nuestro canal de Youtube\.\s*$")]},
    "adiariocr": {"fin": [re.compile(
        r"\s*Proponga su solución\s*Queremos que usted sea parte de la solución\.\s*Coméntenos de qué forma se podría "
        r"mejorar la situación descrita en la nota\.\s*Cada semana destacaremos y le daremos seguimiento a las "
        r"principales soluciones propuestas por nuestros lectores\.\s*Nombre\s*Su solución\s*$"),
        # Enlace a otra nota + invitación a las redes, al final de la nota
        re.compile(
        r"\s*También le puede interesar(?: nota del \d{4})?\s*:.{0,300}?Síganos en\s*:?\s*Instagram Facebook X Youtube"
        r"(?:\s*Tik\s*Tok)?\s*$", _S),
        # Solo la invitación a las redes, sin enlace a otra nota
        re.compile(r"\s*Síganos en\s*:?\s*Instagram Facebook X Youtube.{0,300}$", _S)]},
    # "loadmoduleid 212" es un marcador del gestor de módulos del sitio, puede quedar al final o a medio texto
    "guanacastealaaltura": {"cualquiera": [re.compile(r"\s*loadmoduleid\s+\d+")]},
    "acontecer_cr": {"cualquiera": [re.compile(
        r"\s*Apoye al periodismo independiente y objetivo que hace Acontecer\.co\.cr/?\s*con nuestras suscripciones "
        r"solidarias", _I)]},
    # Joomla esconde los correos con JavaScript: el scraper solo ve este aviso donde iba la dirección
    "periodicomensaje": {"reemplazar": [(re.compile(
        r"Esta dirección de correo electrónico está siendo protegida contra los robots de spam\.\s*"
        r"Necesita tener JavaScript habilitado para poder verlo\."), "[correo protegido]")]},
    "vozdeguanacaste": {
        "inicio": [re.compile(
            r"^\(Unite a nuestro canal de WhatsApp y recibí las noticias directo en tu cel\)\.\s*")],
        # Invitación a la membresía "Semillas" al final de la nota (dos redacciones)
        "fin": [
            re.compile(
                r"\s*Nuestro periodismo existe por y para personas como vos\. Sumate a Semillas.{0,500}?"
                r"Sumate hoy a Semillas\W*$", _S),
            re.compile(
                r"\s*Sumate a Semillas y ayudá a sostener el periodismo local e independiente en Guanacaste\.{0,1}.{0,400}?"
                r"QUIERO SER PARTE\s*$", _S)],
    },
    "ticotimes": {"fin": [
        re.compile(
            r"\s*Live prediction market odds via Kalshi\.\s*Updates every 60 seconds\.\s*Kalshi is available to US residents "
            r"18\+\.\s*The Tico Times may earn a commission from new signups\.\s*$"),
        # Biografía del autor (columnista de fauna), a veces seguida de sus redes y correo
        re.compile(r"\s*Vincent Losasso, founder of Guanacaste Wildlife Monitoring.{0,450}$", _S)]},
    # Biografías de columnistas al final de la nota
    "costaricastar": {
        # Invitación a publicar notas de prensa de negocios, al inicio de la sección de empresas
        "inicio": [re.compile(
            r"^If you would like The Costa Rica Star to publish your business press release, please contact us\s*\.?\s*")],
        "fin": [
        re.compile(
            r"\s*(?:About the Author\s*:?\s*)?Britt Nelson relocated to Costa Rica in 2011 from Florida.{0,800}$", _S),
        re.compile(
            r"\s*Article by Shannon Farley\s.{0,700}$", _S),
        re.compile(
            r"\s*About the Author\s*:?\s*Allan Garro was incorporated as a lawyer and public notary in 1996\..{0,500}$", _S),
        # Otras biografías de columnistas
        re.compile(r"\s*(?:About the Author\s*:?\s*)?Carol Blair Vaughn (?:holds|has written).{0,700}$", _S),
        re.compile(
            r"\s*(?:About the Author\s*:?\s*)?(?:[A-Z][a-z]+ Donnelly\s+)?[A-Z][a-z]+ Donnelly has been a life-long "
            r"enthusiast of Latin American folk culture\..{0,700}$", _S),
        re.compile(r"\s*Mike Poynton is Owner/Principal of Paradise Social.{0,450}$", _S),
        # Datos de contacto de anunciantes al final de la nota
        re.compile(
            r"\s*(?:If you are in need of immigration assistance )?Laura can be reached by clicking here, Toll[- ]free at "
            r"1-833-733-6337,? [Ll]ocally(?: at)? or by sending an email to \S*immigrationhelpcr\.com\.?\s*$"),
        re.compile(
            r"\s*(?:For more information[^:]{0,100}?|To learn more),?\s*please contact:\s*RE/MAX Ocean Surf and Sun.{0,250}$",
            _S)],
        # Restos de HTML del editor (marcador de posición del cursor) que quedaron como texto
        "cualquiera": [
            re.compile(r"\s*span data-mce-type'bookmark'[^/]{0,250}?/span"),
            # Aviso fijo sobre columnistas invitados, al inicio o tras el crédito de la caricatura/autor
            re.compile(
                r"\s*Editor[’']?s note:\s*The views expressed by guest columnists are not necessarily those of "
                r"The Costa Rica Star\s*,\s*its advertisers, or its staff\.?", re.I)]},
    # Presentación fija del audio semanal "Acontecer Legislativo" que antecede a los titulares
    "larevistacr": {"inicio": [re.compile(
        r"^Asamblea Legislativa de Costa\. Desde la Asamblea Legislativa, compartimos con ustedes «Acontecer Legislativo» "
        r"en archivo de audio\. Esta es la edición que resume los principales acontecimientos en el Congreso de Costa Rica\. "
        r"Producido por el Departamento de Relaciones Públicas, Prensa y Protocolo de la Asamblea Legislativa\.\s*")],
        # Notas republicadas de The Conversation: declaración estándar de conflictos de interés + marca de origen
        "fin": [
            # La declaración empieza en su sujeto (nombres propios o "Las personas firmantes"); no en cualquier palabra
            # anterior, para no llevarse la última oración del artículo cuando esta no termina con punto.
            re.compile(
                r"\s+(?:Las personas firmantes|Los autores|Las autoras|"
                r"(?:(?:[A-ZÁÉÍÓÚÑ]\.|Mª|[A-ZÁÉÍÓÚÑ][\wªº’'-]*),?\s+(?:(?:y|e|de|del|la|las|los|van|von|da|do|di)\s+)*){1,60})"
                r"\s*(?:no reciben? salarios?|no son asalariad[oa]s|no es asalariad[oa]|no trabajan? para)"
                r"[^.]{0,200}?ni poseen? acciones, ni reciben? financiaci[oó]n de ninguna compañía u organización que "
                r"pueda obtener beneficio de este artículo[^.]{0,160}\.\s*Publicado originalmente en The Conversation\s*$"),
            # El resto de declaraciones es propia de cada autor (fondos, proyectos); solo se quita la marca fija
            re.compile(r"\s*Publicado originalmente en The Conversation\s*$")]},
    # Aviso de republicación de una revista de aerolínea, al final de la nota
    "elcolectivo506": {"fin": [re.compile(
        r"\s*(?:_{3,}\s*)?(?:A previous|An earlier) version of this (?:piece|article) was published in Nature Landings, "
        r"(?:the in-flight magazine of Nature Air|Nature Air.?s onboard magazine)\.?\s*$", re.I)]},
    # Biografía del autor de la columna, tras una raya de guiones bajos
    "elmundocr": {"fin": [re.compile(
        r"\s*_{5,}\s*El autor es .{0,400}$", _S)]},
    # Avisos de contenido pagado: no son parte de la nota
    "el_financiero": {"fin": [
        re.compile(
            r"\s*Para BRAND VOICE es la unidad de generación de ideas y contenidos de GRUPO NACIÓN.{0,300}?"
            r"Este contenido fue producido para un anunciante y publicado por Brand Voice\.\s*$", _S),
        re.compile(
            r"\s*.Lab de Ideas.\s*es una sección que destaca iniciativas de marcas y empresas en Costa Rica\.\s*"
            r"El contenido editorial está diseñado para ser útil, y también hay espacio para mensajes comerciales "
            r"curados, acompañados; diferentes\.\s*$", _S),
        # Invitación al podcast, a veces seguida de "LEA MÁS: <titular de otra nota>"
        re.compile(
            r"\s*Acceda a todos los capítulos de este podcast en nuestros canales de Youtube y Spotify\."
            r"(?:\s*LEA MÁS:.{0,300})?\s*$", _S)]},
    # Pie de las notas de BBC traducidas con IA
    "el_observador": {"fin": [re.compile(
        r"\s*Este artículo fue escrito originalmente en inglés y usamos una herramienta de inteligencia artificial para "
        r"traducirlo\.(?:\s*(?:Un periodista|Periodistas?) de la BBC (?:revisó|revisaron) el texto antes de su "
        r"publicación\.)?(?:\s*Más información sobre cómo usamos IA\.?)?\s*$")]},
    # Invitaciones a boletín, WhatsApp y Telegram al final de la nota
    "lanacion": {"fin": [re.compile(
        r"\s*¿Sabía que puede recibir El Boletín [^?]{1,60}\?.{0,800}?"
        r"(?:aquí encontrará el enlace directo \.|en su correo electrónico\.|puede accesarlo aquí \.)\s*$", _S)]},
}

# Boletines del OIJ ("requiere la colaboración de la ciudadanía"): el cuerpo (quién, dónde, cuándo, edad, alias,
# fecha de la denuncia) es información propia de cada nota y se conserva. Se quitan solo las dos frases fijas
# con las que el OIJ cierra todos los boletines: el teléfono del Centro de Información Confidencial y el recordatorio
# de que la persona puede ser requerida como testigo. Pueden ir seguidas de un dato propio ("Denuncia interpuesta
# el ...") o de otro texto del sitio, así que se quitan donde aparezcan.
_OIJ_BOLETIN = [
    # Se conserva el espacio anterior y se consume el posterior, para no pegar la frase siguiente. Si la frase va entre
    # comillas (declaración citada dentro de la nota) no se toca.
    re.compile(
        r"(?<![«“\"])\bCualquier información que pueda brindar es\s+indispensable que se comunique al teléfono\s+[\d\s\-–]{7,16}?\s*"
        r"(?:o (?:escribir )?al\s+(?i:WhatsApp)\s+[\d\s\-–]{8,14}?\s*|o al correo electrónico\s+\S+\s*)?"
        r"(?i:del Centro\s*de\s+Información Confidencial)(?: del OIJ)?\.?(?![»”,\"])\s*"),
    re.compile(
        r"(?<![«“\"])\bRecuerde que una persona es requerida por el OIJ por diversas situaciones, entre ellas para informaciones "
        r"sobre investigaciones o como testigos de un caso y no necesariamente como imputados\.?(?![»”\"])\s*"),
]
for _fuente in ("radiopuertotv", "caribeactual", "tvsur", "puntarenasseoye", "radiolapampa", "latejacr", "teletica", "repretel"):
    REGLAS.setdefault(_fuente, {}).setdefault("cualquiera", []).extend(_OIJ_BOLETIN)

# guanacastealaaltura: los partes del IMN y del reporte diario de COVID terminan con enlaces/redes fijos, sin información
# propia. Los demás enlaces de la fuente son parte legítima de los comunicados y no se tocan.
REGLAS["guanacastealaaltura"].setdefault("fin", []).extend([
    re.compile(
        r"\s*Mant\w{2,8}se informados[^.]{0,60}?(?:redes sociales|canales oficiales)\s*,?\s*:?\s*Twitter: IMNCR,?\s*"
        r"Facebook: Instituto Meteorológico Nacional(?: CR)?\s*,?\s*(?:y\s+)?(?:la Página (?:WEB|Web)|Sitio web)?\s*:?\s*"
        r"(?:(?:https?://)?www\.imn\.ac\.cr/?\s*){1,2}$", _I),
    re.compile(
        r"\s*(?:(?:Datos|Actualización|Información) diari[ao]s? \(nacional, cantonal y hospitalizaci(?:ón|ones)\)|[^.:]{0,100}?siguiente enlace):\s*"
        r"https?://bit\.ly/3hIMPk4\s*(?:Datos )?[Dd]isponibles para descarga:\s*https?://bit\.ly/2UYCtCM\s*$"),
])

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


# Publicidad de apuestas disfrazada de nota (contenido patrocinado/SEO de casinos y casas de apuestas).
# Solo se aplica a las fuentes listadas en FUENTES_CON_PUBLICIDAD_APUESTAS: ahí aparece con regularidad, y en el
# resto de los medios una nota sobre casinos o apuestas es noticia (sanciones, lotería ilegal, torneos).
FUENTES_CON_PUBLICIDAD_APUESTAS = {"adiariocr"}

_APUESTAS_TITULO = re.compile(
    r"casinos?\s+(online|en l[ií]nea|usan)|casas?\s+de\s+apuestas|apuestas\s+(deportivas|en\s+vivo|online)|"
    r"\b1\s?win\b|doradobet|bet365|betsson|\bruleta\b|blackjack|juegos?\s+(online|de\s+azar)|tragamonedas|"
    r"p[oó]ker\s+online|sitios\s+de\s+apuestas|pron[oó]sticos\s+deportivos|apostar\s+(online|en\s+l[ií]nea)",
    re.IGNORECASE,
)
_APUESTAS_TERMINO = re.compile(
    r"\b(casinos?|apuestas?|blackjack|ruleta|tragamonedas|p[oó]ker|slots?|jackpot|1\s?win|doradobet|bet365|betsson|"
    r"cuotas|pron[oó]sticos?|apostadores?|casas?\s+de\s+apuestas)\b",
    re.IGNORECASE,
)
_APUESTAS_COMERCIAL = re.compile(
    r"(casinos?\s+(online|en l[ií]nea)|casas?\s+de\s+apuestas|plataformas?\s+de\s+(apuestas|juego)|apuestas\s+online|"
    r"sitios\s+de\s+apuestas|operadores?\s+de\s+(juego|apuestas)|juego\s+responsable)",
    re.IGNORECASE,
)
# Si el título trae un hecho noticioso, la nota se conserva aunque hable de casinos o apuestas.
_NOTICIA_TITULO = re.compile(
    r"sanciona|multa|clausur|allan|decomis|detien|detenid|OIJ|Hacienda|Sala IV|Fiscal[ií]a|juez|tribunal|condena|"
    r"proyecto de ley|diputad|municipalidad|robo|asalto|homicidio|denuncia|investiga|bingo|rifa|caridad|benéfic|"
    r"fundaci[oó]n|loter[ií]a|JPS|Junta de Protecci[oó]n",
    re.IGNORECASE,
)
MIN_TERMINOS_TITULO = 4     # título comercial + al menos tantos términos en el texto
MIN_TERMINOS_SOLO_TEXTO = 14  # sin señal en el título: el texto debe ser casi todo sobre apuestas
MIN_TERMINOS_COMERCIALES = 3


def es_publicidad_apuestas(fuente: str, titulo: str, texto: str) -> bool:
    """True si una nota de una fuente con patrocinios de casinos es publicidad de apuestas y no una noticia."""
    if fuente not in FUENTES_CON_PUBLICIDAD_APUESTAS or not isinstance(texto, str):
        return False
    titulo = titulo if isinstance(titulo, str) else ""
    if _NOTICIA_TITULO.search(titulo):
        return False
    terminos = len(_APUESTAS_TERMINO.findall(texto))
    if _APUESTAS_TITULO.search(titulo):
        return terminos >= MIN_TERMINOS_TITULO
    # Sin señal en el título: el texto debe ser casi todo sobre apuestas Y con lenguaje comercial del sector
    # (una columna de opinión o una noticia sobre apuestas no cumple lo segundo).
    return terminos >= MIN_TERMINOS_SOLO_TEXTO and len(_APUESTAS_COMERCIAL.findall(texto)) >= MIN_TERMINOS_COMERCIALES


# Notas que son contenido de plantilla del tema del sitio (no noticias).
_PLANTILLA = re.compile(r"lorem ipsum|mauris mattis auctor cursus", re.IGNORECASE)


def es_plantilla(texto: str) -> bool:
    """True si el texto es relleno de plantilla ("lorem ipsum"), no una noticia."""
    return bool(texto) and bool(_PLANTILLA.search(texto))


# Notas que son solo el aviso fijo de la fuente y no traen contenido propio. Se comparan contra el texto COMPLETO
# (fullmatch): una nota que además trae titulares o cuerpo no coincide y se conserva.
_SIN_CONTENIDO = {
    # Publicación semanal del audio "Acontecer Legislativo": el texto es solo la presentación del audio.
    "larevistacr": [re.compile(
        r"Asamblea Legislativa de Costa\. Desde la Asamblea Legislativa, compartimos con ustedes «Acontecer Legislativo» "
        r"en archivo de audio\. Esta es la edición que resume los principales acontecimientos en el Congreso de Costa Rica\. "
        r"Producido por el Departamento de Relaciones Públicas, Prensa y Protocolo de la Asamblea Legislativa\.?")],
    # Solo la nota del editor sobre columnistas invitados (el artículo no se alcanzó a extraer).
    "costaricastar": [re.compile(
        r"Editor[’']s note: The views expressed by guest columnists are not necessarily those of The Costa Rica Star, "
        r"its advertisers, or its staff\.?[^.]{0,80}")],
    # Aviso del sorteo de la Lotería Nacional: los números ganadores van en un video/imagen, no en el texto.
    "repretel": [re.compile(
        r"Durante la noche de este [^.]{1,40} se celebró el sorteo #\d+ de la Lotería Nacional\. "
        r"Detallamos cuáles fueron los números ganadores\."
        r"(?: En caso de querer revivir el sorteo, puede verlo en el video que aparece en el link\.)?")],
}


def es_sin_contenido(fuente: str, texto: str) -> bool:
    """True si el texto es solo el aviso fijo conocido de la fuente (sin información propia de la nota)."""
    if not isinstance(texto, str) or not texto:
        return False
    limpio = " ".join(texto.split())
    return any(p.fullmatch(limpio) for p in _SIN_CONTENIDO.get(fuente, []))


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
    for patron, reemplazo in reglas.get("reemplazar", []):
        nuevo = patron.sub(reemplazo, actual).strip()
        if nuevo != actual.strip() and len(nuevo) >= MIN_RESTANTE:
            actual = nuevo
    return actual
