"""Reglas de limpieza del relleno de cada sitio (textos tomados de notas reales)."""
import unittest

import pandas as pd

from tests._util import RAIZ  # noqa: F401
from limpieza_relleno import MIN_RESTANTE, es_plantilla, es_publicidad_apuestas, es_sin_contenido, idioma_de_fuente, idioma_de_nota, limpiar_relleno
from output_cleaner import clean_dataframe

CUERPO = "La Municipalidad informó que las obras avanzan según lo previsto y que se mantendrá el cierre parcial. " * 5


class TestLimpiezaRelleno(unittest.TestCase):
    def casos(self):
        return {
            "puntarenasseoye": (CUERPO + " CANAL OFICIAL Puntarenas Se Oye en WhatsAppLeer periódicos Recibí las noticias más importantes de Puntarenas directo en tu teléfono. Sin spam, solo lo que importa. Gratis · Sin costo · Podés salir cuando quierasInversiones Rurales", CUERPO),
            "miprensacr": ("Franklin Castro R. franklindecostaricagmail.com Whatsapp (506) 89384176 " + CUERPO + " Vea aquí más información de Mi Prensa , Juntos Somos más! Horarios y tarifas de servicio de ferry a Paquera aquí Visite nuestro canal de Youtube", CUERPO),
            "repretel": (CUERPO + " Si quiere estar atento a otras informaciones, puede seguir las ediciones de Noticias Repretel en Canal 6, Repretel.com, Facebook Live y YouTube de Grupo Repretel Noticias.", CUERPO),
            "lavozdegoicoechea": (CUERPO + " Teléfono: +506 6226-2075 Correo: info@lavozdegoicoechea.com Dirección: San José, Goicoechea, Guadalupe", CUERPO),
            "eldelfino": ("Abogada litigante. Máster en Derecho Penal. De martes a viernes le contamos las noticias más relevantes del acontecer nacional como solo Delfino.cr puede hacerlo. En cualquier momento puede salirse de la lista de correos. " + CUERPO, CUERPO),
            "digital506": ("Por Redacción. Esta nota fue elaborada con asistencia de IAImagen tomada de internet " + CUERPO, CUERPO),
            "caribeactual": (CUERPO + " Something went wrong. Please refresh the page and/or try again. Noticias de Pococí en Whatsapp Noticias de Pococí en Facebook Otro titular de una nota distinta", CUERPO),
            "teletica": (CUERPO + " Haz clic aquí para leer más historias de BBC News Mundo. También puedes seguirnos en YouTube. Descarga la última versión y actívalas.", CUERPO),
            "adiariocr": (CUERPO + " Proponga su solución Queremos que usted sea parte de la solución. Coméntenos de qué forma se podría mejorar la situación descrita en la nota. Cada semana destacaremos y le daremos seguimiento a las principales soluciones propuestas por nuestros lectores. Nombre Su solución", CUERPO),
            "guanacastealaaltura": (CUERPO + " loadmoduleid 212", CUERPO),
            "acontecer_cr": (CUERPO + " Apoye al periodismo independiente y objetivo que hace acontecer.co.cr/ con nuestras suscripciones solidarias", CUERPO),
            "vozdeguanacaste": ("(Unite a nuestro canal de WhatsApp y recibí las noticias directo en tu cel). " + CUERPO, CUERPO),
            "ticotimes": (CUERPO + " Live prediction market odds via Kalshi. Updates every 60 seconds. Kalshi is available to US residents 18+. The Tico Times may earn a commission from new signups.", CUERPO),
        }

    def test_cada_regla_quita_el_relleno_y_deja_el_cuerpo(self):
        for fuente, (sucio, limpio) in self.casos().items():
            with self.subTest(fuente=fuente):
                self.assertEqual(limpiar_relleno(fuente, sucio), limpio.strip())

    def test_lavozdegoicoechea_quita_la_linea_published_by(self):
        # Texto real: la fecha sale repetida y, tras normalizar espacios, pegada ("202611 agosto, 2026").
        for prefijo in ("Published by Redacción on 11 agosto, 202611 agosto, 2026 ",
                        "Published by Redacción on 5 junio, 2026 5 junio, 2026 "):
            with self.subTest(prefijo=prefijo):
                self.assertEqual(limpiar_relleno("lavozdegoicoechea", prefijo + CUERPO), CUERPO.strip())
        # No toca un "Published by" que esté a mitad de texto
        medio = CUERPO + " Published by Redacción on 5 junio, 2026 5 junio, 2026 " + CUERPO
        self.assertEqual(limpiar_relleno("lavozdegoicoechea", medio), medio)

    def finales_reales(self):
        """Finales de notas reales (biografías de columnistas, avisos de contenido pagado, invitaciones)."""
        return [
            ("costaricastar", " About the Author Britt Nelson relocated to Costa Rica in 2011 from Florida, where he served as news editor for the Palatka Daily News, a newspaper owned by the New York Times Regional Newspaper Group (NYT-RNG) for 20 years. Nelson served the NYT-RNG for 18 years in Tennessee, Kentucky, Virginia, North Carolina and Florida as a correspondent, reporter, staff writer, senior staff writer training new reporters, copy editor, page designer, editorial writer, editorial page editor and editorial board member"),
            ("costaricastar", " Article by Shannon Farley A Southern California native, Shannon Farley writes English-language blogs and handles social media marketing for Enchanting Costa Rica and Profimercadeo, both based in San Jose, Costa Rica. She is a professional journalist who spent an 18-year hiatus in tourism and business management, and has since combined all three talents in her current work. See also for more real estate information – Guide to Real Estate in Costa Rica"),
            ("costaricastar", " Article by Shannon Farley Shannon Farley writes English-language blogs and handles social media marketing for Enchanting Costa Rica and Profimercadeo in San Jose, Costa Rica."),
            ("costaricastar", " About the Author: Allan Garro was incorporated as a lawyer and public notary in 1996. He specializes in Litigation, Corporate, and Real Estate Law. He has also acted as an external legal consultant to Congress. He has been the author of more than 100 published English Language articles and can be reached at allan@garrolaw.com"),
            ("costaricastar", " About the Author Carol Blair Vaughn holds a Masters Degree in Performing Arts and is a retired college professor from Washington, D.C. She inherited her love for Latin America from her father, the former U.S. Ambassador to Colombia, Panama, and Director of the Peace Corps, Jack Vaughn. Carol writes for the Association of Residents of Costa Rica ( ARCR ) and for an English language club in the southern region of Costa Rica. In the past, Carol served as Editor in Chief of Whats on Tap magazine."),
            ("costaricastar", " About the Author : Carol Blair Vaughn has written for Inside Costa Rica and The Costa Rica Star, as well as El Residente magazine. She grew up in Latin America, traveling with her father Jack Vaughn, former Assistant Secretary of State for Latin American Affairs, and US Ambassador to Panama and Colombia. The Star published her book Crazy Jungle Love : Murder, Madness, Money Monkeys in 2017, and it is now available for purchase on Amazon as both a paperback and an ebook."),
            ("costaricastar", " About the Author Jack Donnelly Jack Donnelly has been a life-long enthusiast of Latin American folk culture. As a young man, during the 1960s, he studied cultural anthropology at the Universidad de las Américas in Mexico City. He went on to volunteer with the U. S. Peace Corps in Guatemala. Now, Donnelly lives in Heredia, Costa Rica. He travels around the country investigating and documenting folkloric events. Donnelly is the author of COSTA RICA: Folk Culture, Traditions, and Cuisine which is available on Amazon in paperback and Kindle."),
            ("costaricastar", " Mike Poynton is Owner/Principal of Paradise Social , a digital marketing, SEO and website design company in Playa Hermosa, Guanacaste. He is also active in his community and serves on the Board of Directors of the Asociación de Vecinos y Empresarios de Playa Hermosa . He periodically contributes articles to the Costa Rica Star."),
            ("costaricastar", " If you are in need of immigration assistance Laura can be reached by clicking here, Toll-free at 1-833-733-6337, Locally at or by sending an email to info@immigrationhelpcr.com"),
            ("costaricastar", " If you are in need of immigration assistance Laura can be reached by clicking here, Toll free at 1-833-733-6337, Locally at or by sending an email to info@immigrationhelpcr.com."),
            ("costaricastar", " For more information, please contact: RE/MAX Ocean Surf and Sun 1 Sunrise Center Beach Road, Tamarindo Toll Free: 1-866-976-8898 Local: 2653-0073 / 2653-0733 inforemax-oceansurf-cr.com"),
            ("costaricastar", " For more information about real estate listings that include fruit trees , please contact: RE/MAX Ocean Surf and Sun 1 Sunrise Center Beach Road, Tamarindo Toll Free: 1-866-976-8898 Local: 2653-0073 / 2653-0733 inforemax-oceansurf-cr.com"),
            ("costaricastar", " To learn more, please contact: RE/MAX Ocean Surf and Sun #1 Sunrise Center Beach Road, Tamarindo"),
            ("costaricastar", " Laura can be reached by clicking here, Toll-free at 1-833-733-6337 locally or by sending an email to info@immigrationhelpcr.com."),
            ("costaricastar", " If you are in need of immigration assistance Laura can be reached by clicking here, Toll free at 1-833-733-6337, Locally at or by sending an email to laura@immigrationhelpcr.com."),
            ("costaricastar", " About the Author: Allan Garro was incorporated as a lawyer and public notary in 1996. He specializes in Litigation, Corporate, and Real Estate Law. He has also acted as an external legal consultant to Congress. He has been the author of more than 100 published English Language articles and can be reached at allan@garrolaw.com or by visiting his website here."),
            ("costaricastar", " Britt Nelson relocated to Costa Rica in 2011 from Florida, where he served as news editor for the Palatka Daily News. Nelson served the NYT-RNG for 18 years as a correspondent, reporter, staff writer, editorial writer, editorial page editor and editorial board member."),
            ("ticotimes", " Vincent Losasso, founder of Guanacaste Wildlife Monitoring, is a biologist who works with camera traps throughout Costa Rica."),
            ("ticotimes", " Vincent Losasso, founder of Guanacaste Wildlife Monitoring, is a biologist who works with camera traps throughout Costa Rica. Learn more about his projects on facebook or instagram. You can also email him at: vincent@guanacastewildlifemonitoring.com"),
            ("elmundocr", " __________________ El autor es Licenciado en Relaciones Internacionales. Analista político internacional. Además elabora procesos de capacitación política, desarrollo comunal y administración. maumazu208gmail.com ."),
            ("elmundocr", " ____________________ El autor es Dr. Derecho Administrativo, Abogado/ Doctor en Derecho Administrativo Máster en Derecho Administrativo y Máster en Derecho Tributario."),
            ("el_financiero", " Para BRAND VOICE es la unidad de generación de ideas y contenidos de GRUPO NACIÓN para las marcas con distribución en sus plataformas digitales y redes sociales. Este contenido fue producido para un anunciante y publicado por Brand Voice."),
            ("el_financiero", " ‘Lab de Ideas’ es una sección que destaca iniciativas de marcas y empresas en Costa Rica. El contenido editorial está diseñado para ser útil, y también hay espacio para mensajes comerciales curados, acompañados; diferentes."),
            ("el_financiero", " ‘Lab de Ideas’es una sección que destaca iniciativas de marcas y empresas en Costa Rica. El contenido editorial está diseñado para ser útil, y también hay espacio para mensajes comerciales curados, acompañados; diferentes."),
            ("el_observador", " Este artículo fue escrito originalmente en inglés y usamos una herramienta de inteligencia artificial para traducirlo. Un periodista de la BBC revisó el texto antes de su publicación. Más información sobre cómo usamos IA"),
            ("el_observador", " Este artículo fue escrito originalmente en inglés y usamos una herramienta de inteligencia artificial para traducirlo. Periodistas de la BBC revisaron el texto antes de su publicación. Más información sobre cómo usamos IA."),
            ("vozdeguanacaste", " Nuestro periodismo existe por y para personas como vos. Sumate a Semillas, el programa de membresías con el que vos podés hacer posible el periodismo local e independiente en Guanacaste: investigaciones rigurosas y reveladoras, coberturas desde los territorios y escucha activa de las comunidades. Además, las personas miembras acceden a beneficios que construyen comunidad. Sumate hoy a Semillas 🌱"),
            ("vozdeguanacaste", " Sumate a Semillas y ayudá a sostener el periodismo local e independiente en Guanacaste. Tu aporte hace posible investigaciones rigurosas y reveladoras, cobertura desde los territorios y escucha activa de las comunidades. Además, recibís beneficios que construyen comunidad. QUIERO SER PARTE"),
            ("lanacion", " ¿Sabía que puede recibir El Boletín Manudo en su correo electrónico de forma gratuita? Para eso, regístrese aquí . ¿Ya se unió al canal de WhatsApp La Nación Alajuelense ? Aquí puede hacerlo . Si desea unirse al canal AlajuelenseLN en Telegram , aquí encontrará el enlace directo . El Blog de Alajuelense , Actualidad Rojinegra , puede accesarlo aquí ."),
            ("lanacion", " ¿Sabía que puede recibir El Boletín Manudo en su correo electrónico de forma gratuita? Para eso, regístrese aquí . Si desea seguir el Blog de Saprissa, Actualidad Morada, aquí encontrará el enlace directo . Además, regístrese en El Boletín Morado para que lo reciba en su correo electrónico."),
            ("adiariocr", " También le puede interesar: Nuevo documental Groundswell se lanza en el Día Mundial del Ambiente Síganos en: Instagram Facebook X Youtube Tik Tok"),
            ("adiariocr", " También le puede interesar : Índice Global de Retiro 2026 coloca a Costa Rica en tercer lugar del mundo Síganos en: Instagram Facebook X Youtube Tik Tok"),
            ("adiariocr", " También le puede interesar nota del 2020: Estudio muestra que productores utilizan plaguicidas prohibidos en Costa Rica Síganos en: Instagram Facebook X Youtube Tik Tok"),
            ("adiariocr", " Síganos en : Instagram Facebook X Youtube También le puede interesar: La confesión: el abrazo de la misericordia"),
            ("eldelfino", " Este artículo representa el criterio de quien lo firma. Los artículos de opinión publicados no reflejan necesariamente la posición editorial de este medio."),
        ]

    def test_finales_reales_se_quitan_y_queda_el_cuerpo(self):
        for fuente, final in self.finales_reales():
            with self.subTest(fuente=fuente, final=final[:50]):
                self.assertEqual(limpiar_relleno(fuente, CUERPO + final), CUERPO.strip())

    def test_costaricastar_quita_los_restos_de_html_del_editor(self):
        # Marcador del editor que quedó como texto: se quita donde esté (al final o a mitad de nota)
        junk = " span data-mce-type'bookmark' style'display: inline-block; width: 0px; overflow: hidden; line-height: 0;' class'mce_SELRES_start'/span"
        self.assertEqual(limpiar_relleno("costaricastar", CUERPO + junk), CUERPO.strip())
        self.assertEqual(limpiar_relleno("costaricastar", CUERPO + junk + " " + CUERPO), (CUERPO.rstrip() + " " + CUERPO).strip())

    def test_la_frase_a_mitad_de_texto_no_se_recorta(self):
        # Las reglas de biografía/invitación solo actúan al final: si la frase sale a mitad de nota se deja
        for fuente, final in self.finales_reales():
            with self.subTest(fuente=fuente, final=final[:50]):
                texto = CUERPO + final + " " + CUERPO * 4   # mucho texto después: no es un pie
                self.assertEqual(limpiar_relleno(fuente, texto), texto)

    def test_texto_sin_relleno_no_cambia(self):
        for fuente in self.casos():
            with self.subTest(fuente=fuente):
                self.assertEqual(limpiar_relleno(fuente, CUERPO), CUERPO)

    def test_otra_fuente_no_se_toca(self):
        sucio, _ = self.casos()["puntarenasseoye"]
        self.assertEqual(limpiar_relleno("teletica", sucio), sucio)

    def test_no_recorta_si_queda_casi_nada(self):
        casi_solo_relleno = "Nota breve. Teléfono: +506 6226-2075 Correo: info@lavozdegoicoechea.com Dirección: San José, Goicoechea, Guadalupe"
        self.assertLess(len("Nota breve."), MIN_RESTANTE)
        self.assertEqual(limpiar_relleno("lavozdegoicoechea", casi_solo_relleno), casi_solo_relleno)

    def test_costaricastar_quita_el_aviso_de_columnistas_invitados(self):
        aviso = ("Editors note: The views expressed by guest columnists are not necessarily those of The Costa Rica Star, "
                 "its advertisers, or its staff")
        cuerpo = "Love him or hate him President Trump's popularity is on the rise with approval ratings topping fifty percent. " * 3
        # al inicio, con y sin punto final, y con la tipografía curva de "Editor's"
        self.assertEqual(limpiar_relleno("costaricastar", aviso + " " + cuerpo), cuerpo.strip())
        self.assertEqual(limpiar_relleno("costaricastar", aviso + ". " + cuerpo), cuerpo.strip())
        self.assertEqual(limpiar_relleno("costaricastar", aviso.replace("Editors", "Editor’s") + " " + cuerpo), cuerpo.strip())
        # variantes: "Editors Note" con mayúscula y espacio antes de la coma
        variante = "By Britt Nelson Editors Note: The views expressed by guest columnists are not necessarily those of The Costa Rica Star , its advertisers, or its staff. "
        self.assertEqual(limpiar_relleno("costaricastar", variante + cuerpo), "By Britt Nelson " + cuerpo.strip())
        # tras el crédito de la caricatura y del autor: el crédito se conserva, el aviso no
        con_credito = "Cartoon by Clay Jones By Britt Nelson " + aviso + ". " + cuerpo
        esperado = "Cartoon by Clay Jones By Britt Nelson" + " " + cuerpo.strip()
        self.assertEqual(" ".join(limpiar_relleno("costaricastar", con_credito).split()), esperado)
        # si solo hay aviso no queda nada que valga la pena: no se toca (lo descarta es_sin_contenido)
        solo_aviso = aviso + ". Latinos& Latin-Americans"
        self.assertEqual(limpiar_relleno("costaricastar", solo_aviso), solo_aviso)
        self.assertEqual(limpiar_relleno("ticotimes", aviso + " " + cuerpo), aviso + " " + cuerpo)  # solo en su fuente

    def test_larevistacr_quita_la_presentacion_del_audio_y_conserva_los_titulares(self):
        intro = ("Asamblea Legislativa de Costa. Desde la Asamblea Legislativa, compartimos con ustedes «Acontecer Legislativo» "
                 "en archivo de audio. Esta es la edición que resume los principales acontecimientos en el Congreso de Costa Rica. "
                 "Producido por el Departamento de Relaciones Públicas, Prensa y Protocolo de la Asamblea Legislativa.")
        titulares = ("Aprueban proyecto que sanciona a jerarcas que incumplan con reglamentación de Leyes. "
                     "Ministerio de Hacienda deberá publicar lista de contribuyentes que reportan cero ganancias.")
        self.assertEqual(limpiar_relleno("larevistacr", intro + " " + titulares), titulares)
        # sin titulares no queda texto suficiente: se deja igual y es_sin_contenido la descarta
        self.assertEqual(limpiar_relleno("larevistacr", intro), intro)
        self.assertTrue(es_sin_contenido("larevistacr", limpiar_relleno("larevistacr", intro)))
        # solo al inicio de la nota
        self.assertEqual(limpiar_relleno("larevistacr", titulares + " " + intro), titulares + " " + intro)

    def test_larevistacr_quita_la_declaracion_estandar_de_the_conversation(self):
        marca = "Publicado originalmente en The Conversation"
        estandar = ("no recibe salario, ni ejerce labores de consultoría, ni posee acciones, ni recibe financiación de ninguna "
                    "compañía u organización que pueda obtener beneficio de este artículo, y ha declarado carecer de vínculos "
                    "relevantes más allá del cargo académico citado.")
        cuerpo = CUERPO.strip()
        # con nombre propio
        self.assertEqual(limpiar_relleno("larevistacr", f"{cuerpo} Gaizka Fernández Soldevilla {estandar} {marca}"), cuerpo)
        # con "Las personas firmantes" y plural
        plural = ("Las personas firmantes no son asalariadas, ni consultoras, ni poseen acciones, ni reciben financiación de "
                  "ninguna compañía u organización que pueda obtener beneficio de este artículo, y han declarado carecer de "
                  "vínculos relevantes más allá del cargo académico citado anteriormente.")
        self.assertEqual(limpiar_relleno("larevistacr", f"{cuerpo} {plural} {marca}"), cuerpo)
        # varios autores con iniciales y conectores
        autores = "Adrián Cervera Muñoz, Ana M. López Sobaler y Viviana Loria Kohen"
        self.assertEqual(limpiar_relleno("larevistacr", f"{cuerpo} {autores} {estandar.replace('no recibe', 'no reciben')} {marca}"), cuerpo)

    def test_larevistacr_no_se_lleva_la_ultima_oracion_si_no_termina_con_punto(self):
        marca = "Publicado originalmente en The Conversation"
        estandar = ("no recibe salario, ni ejerce labores de consultoría, ni posee acciones, ni recibe financiación de ninguna "
                    "compañía u organización que pueda obtener beneficio de este artículo, y ha declarado carecer de vínculos "
                    "relevantes más allá del cargo académico citado.")
        cuerpo = CUERPO.strip() + " Se asegure a las generaciones futuras que los bosques seguirán siendo una realidad posible"
        out = limpiar_relleno("larevistacr", f"{cuerpo} Manuel Esteban Lucas-Borja {estandar} {marca}")
        self.assertEqual(out, cuerpo)  # conserva "…una realidad posible"; quita solo desde el nombre del autor
        # el sujeto no absorbe palabras con mayúscula de la oración anterior que sí terminó con punto
        previo = CUERPO.strip() + " El proyecto fue financiado por el Plan Propio de la Universidad de Almería."
        self.assertEqual(limpiar_relleno("larevistacr", f"{previo} Elizabeth Gualda Alonso {estandar} {marca}"), previo)

    def test_larevistacr_en_declaraciones_propias_solo_quita_la_marca(self):
        marca = "Publicado originalmente en The Conversation"
        propia = "Josep-Maria Arauzo-Carod recibe fondos de Generalitat de Catalunya y el Ministerio de Ciencia e Innovación."
        cuerpo = CUERPO.strip()
        self.assertEqual(limpiar_relleno("larevistacr", f"{cuerpo} {propia} {marca}"), f"{cuerpo} {propia}")
        # la marca solo se quita al final de la nota
        self.assertEqual(limpiar_relleno("larevistacr", f"{marca} {cuerpo}"), f"{marca} {cuerpo}")
        self.assertEqual(limpiar_relleno("teletica", f"{cuerpo} {marca}"), f"{cuerpo} {marca}")

    def test_elcolectivo506_quita_el_aviso_de_republicacion(self):
        cuerpo = CUERPO.strip() + " Mónica Quesada Cordero / El Colectivo 506"
        for aviso in ("A previous version of this piece was published in Nature Landings, the in-flight magazine of Nature Air.",
                      "___ A previous version of this piece was published in Nature Landings, the in-flight magazine of Nature Air.",
                      "An earlier version of this article was published in Nature Landings, Nature Airs onboard magazine."):
            with self.subTest(aviso=aviso):
                self.assertEqual(limpiar_relleno("elcolectivo506", f"{cuerpo} {aviso}"), cuerpo)
        # a mitad de la nota no se toca
        mitad = "A previous version of this piece was published in Nature Landings, the in-flight magazine of Nature Air. " + CUERPO
        self.assertEqual(limpiar_relleno("elcolectivo506", mitad), mitad)

    def test_plantilla(self):
        self.assertTrue(es_plantilla("Mauris mattis auctor cursus. Phasellus tellus tellus"))
        self.assertTrue(es_plantilla("Lorem ipsum dolor sit amet"))
        self.assertFalse(es_plantilla(CUERPO))

    def test_sin_contenido_solo_el_aviso_fijo(self):
        audio = ("Asamblea Legislativa de Costa. Desde la Asamblea Legislativa, compartimos con ustedes «Acontecer Legislativo» "
                 "en archivo de audio. Esta es la edición que resume los principales acontecimientos en el Congreso de Costa Rica. "
                 "Producido por el Departamento de Relaciones Públicas, Prensa y Protocolo de la Asamblea Legislativa.")
        self.assertTrue(es_sin_contenido("larevistacr", audio))
        self.assertTrue(es_sin_contenido("larevistacr", audio.replace(". ", ".   ")))  # espacios distintos
        # con titulares la nota sí tiene información: se conserva
        self.assertFalse(es_sin_contenido("larevistacr", audio + " Aprueban proyecto que sanciona a jerarcas que incumplan."))
        self.assertFalse(es_sin_contenido("adiariocr", audio))  # solo en su fuente
        nota = "Editor’s note: The views expressed by guest columnists are not necessarily those of The Costa Rica Star, its advertisers, or its staff."
        self.assertTrue(es_sin_contenido("costaricastar", nota + " Latinos& Latin-Americans"))
        self.assertFalse(es_sin_contenido("costaricastar", nota + " " + CUERPO))
        loteria = ("Durante la noche de este 1 de noviembre se celebró el sorteo #4925 de la Lotería Nacional. Detallamos cuáles fueron "
                   "los números ganadores. En caso de querer revivir el sorteo, puede verlo en el video que aparece en el link.")
        self.assertTrue(es_sin_contenido("repretel", loteria))
        self.assertFalse(es_sin_contenido("repretel", loteria + " Primer premio: 1234 serie 056. Segundo premio: 5678."))
        self.assertFalse(es_sin_contenido("repretel", None))
        self.assertFalse(es_sin_contenido("repretel", ""))

    def test_clean_dataframe_descarta_notas_sin_contenido(self):
        audio = ("Asamblea Legislativa de Costa. Desde la Asamblea Legislativa, compartimos con ustedes «Acontecer Legislativo» "
                 "en archivo de audio. Esta es la edición que resume los principales acontecimientos en el Congreso de Costa Rica. "
                 "Producido por el Departamento de Relaciones Públicas, Prensa y Protocolo de la Asamblea Legislativa.")
        fila = lambda url, fuente, texto, titulo: {
            "source": fuente, "url": url, "title": titulo, "publication_date": "2026-10-05 10:00:00",
            "scraping_date": "2026-10-05 11:00:00", "section": "Noticias", "full_text": texto, "language": "es"}
        df = pd.DataFrame([
            fila("https://a.cr/1", "larevistacr", audio, "Resumen del acontecer legislativo: 27-02-2026"),
            fila("https://a.cr/2", "larevistacr", audio + " Aprueban proyecto de ley sobre el marchamo.", "Resumen: 13-02-2026"),
        ])
        out, stats, _ = clean_dataframe(df, verbose=False)
        self.assertEqual(stats["dropped_sin_contenido"], 1)
        self.assertEqual(list(out["url"]), ["https://a.cr/2"])

    def test_idioma_se_decide_por_la_fuente(self):
        self.assertEqual(idioma_de_fuente("ticosland"), "en")
        self.assertEqual(idioma_de_fuente("ticotimes"), "en")
        self.assertEqual(idioma_de_fuente("costaricastar"), "en")
        self.assertEqual(idioma_de_fuente("larevistacr"), "es")
        self.assertEqual(idioma_de_fuente("cualquier_otra"), "es")

    def test_nota_en_ingles_en_medio_en_espanol_se_respeta(self):
        ingles = ("Welcome to Costa Rica, President Barack Obama. There is a pleasant, symbolic and historical coincidence "
                  "that you are visiting our country at a time when the two nations celebrate their long friendship. ") * 4
        self.assertEqual(idioma_de_nota("puroperiodismo", ingles), "en")
        self.assertEqual(idioma_de_nota("puroperiodismo", CUERPO), "es")
        self.assertEqual(idioma_de_nota("ticosland", CUERPO), "en")      # fuentes en inglés: siempre inglés

    def test_fecha_imposible_cae_a_scraping_date_como_inferida(self):
        fila = {"source": "periodicomensaje", "url": "https://a.cr/x", "title": "Titulo", "section": "Noticias",
                "full_text": CUERPO, "language": "es", "scraping_date": "2026-10-05 11:00:00",
                "publication_date": "1979-12-31 18:00:00"}
        out, stats, inferidas = clean_dataframe(pd.DataFrame([fila]), verbose=False)
        self.assertEqual(out.iloc[0]["publication_date"], "2026-10-05 11:00:00")
        self.assertEqual(list(inferidas["url"]), ["https://a.cr/x"])

    def test_correo_protegido_de_joomla_se_reemplaza_por_una_marca(self):
        aviso = ("Esta dirección de correo electrónico está siendo protegida contra los robots de spam. "
                 "Necesita tener JavaScript habilitado para poder verlo.")
        sucio = CUERPO + " Para más información escriba al correo: " + aviso + " o llame al 2222-3333. Segundo contacto: " + aviso
        limpio = limpiar_relleno("periodicomensaje", sucio)
        self.assertNotIn("robots de spam", limpio)
        self.assertEqual(limpio.count("[correo protegido]"), 2)
        self.assertIn("escriba al correo: [correo protegido] o llame al 2222-3333", limpio)
        self.assertEqual(limpiar_relleno("crhoy", sucio), sucio)     # solo en la fuente que lo produce

    def test_publicidad_de_apuestas_se_descarta_solo_en_su_fuente(self):
        anuncio = ("Los casinos online y las casas de apuestas ofrecen bonos. Las apuestas deportivas en vivo permiten apostar "
                   "durante el partido; la ruleta y el blackjack siguen siendo los juegos favoritos de los jugadores. ") * 3
        self.assertTrue(es_publicidad_apuestas("adiariocr", "Casas de apuestas y casino online en Costa Rica", anuncio))
        self.assertTrue(es_publicidad_apuestas("adiariocr", "Registro y verificación de cuenta en DoradoBet Costa Rica", anuncio))
        self.assertFalse(es_publicidad_apuestas("crhoy", "Casas de apuestas y casino online en Costa Rica", anuncio))

    def test_noticia_con_un_anuncio_no_se_pierde(self):
        anuncio = "Casinos online, casas de apuestas, apuestas deportivas y ruleta con bonos. " * 4
        # noticia real (guarda por título) aunque el texto traiga un bloque de anuncio
        self.assertFalse(es_publicidad_apuestas("adiariocr", "Hacienda sanciona por más de 361 millones a 10 casinos", anuncio))
        self.assertFalse(es_publicidad_apuestas("adiariocr", "Juegue bingo en el Blue Valley School y ayude a Proyecto Daniel", anuncio))
        # noticia cualquiera con un par de menciones: no llega al mínimo de términos
        self.assertFalse(es_publicidad_apuestas("adiariocr", "Ministro presenta presupuesto 2027", CUERPO + " Casa de apuestas patrocina el evento."))
        # opinión sobre apuestas sin lenguaje comercial del sector
        opinion = "La ética de apostar sobre la realidad. Las apuestas sobre eventos futuros, las apuestas y las cuotas. " * 8
        self.assertFalse(es_publicidad_apuestas("adiariocr", "Polymarket y la ética de apostar sobre la realidad", opinion))

    def test_clean_dataframe_descarta_publicidad_de_apuestas(self):
        anuncio = "Los casinos online y las casas de apuestas ofrecen bonos. Las apuestas deportivas en vivo. La ruleta y el blackjack. " * 4
        base = {"publication_date": "2026-10-05 10:00:00", "scraping_date": "2026-10-05 11:00:00", "section": "Economía", "language": "es", "source": "adiariocr"}
        df = pd.DataFrame([
            {**base, "url": "https://a.cr/1", "title": "Casinos online en Costa Rica", "full_text": anuncio},
            {**base, "url": "https://a.cr/2", "title": "Ministro presenta presupuesto 2027", "full_text": CUERPO},
        ])
        out, stats, _ = clean_dataframe(df, verbose=False)
        self.assertEqual(stats["dropped_publicidad_apuestas"], 1)
        self.assertEqual(list(out["url"]), ["https://a.cr/2"])

    def test_clean_dataframe_corrige_el_idioma(self):
        base = {"url": "", "title": "Titulo", "publication_date": "2026-10-05 10:00:00", "scraping_date": "2026-10-05 11:00:00",
                "section": "Noticias", "full_text": CUERPO}
        df = pd.DataFrame([
            {**base, "source": "ticosland", "url": "https://a.cr/1", "language": "es"},
            {**base, "source": "larevistacr", "url": "https://a.cr/2", "language": "en"},
        ])
        out, stats, _ = clean_dataframe(df, verbose=False)
        self.assertEqual(dict(zip(out["source"], out["language"])), {"ticosland": "en", "larevistacr": "es"})
        self.assertEqual(stats["idioma_corregido"], 2)

    def test_clean_dataframe_aplica_las_reglas_y_descarta_plantillas(self):
        sucio, limpio = self.casos()["repretel"]
        fila = lambda url, fuente, texto, titulo: {
            "source": fuente, "url": url, "title": titulo, "publication_date": "2026-10-05 10:00:00",
            "scraping_date": "2026-10-05 11:00:00", "section": "Noticias", "full_text": texto, "language": "es"}
        df = pd.DataFrame([
            fila("https://a.cr/1", "repretel", sucio, "Con relleno"),
            fila("https://a.cr/2", "enlamira", "Mauris mattis auctor cursus. " * 20, "Plantilla del tema"),
        ])
        out, stats, _ = clean_dataframe(df, verbose=False)
        self.assertEqual(stats["relleno_recortado"], 1)
        self.assertEqual(stats["dropped_plantilla"], 1)
        self.assertEqual(list(out["url"]), ["https://a.cr/1"])
        self.assertEqual(out.iloc[0]["full_text"], limpio.strip())

class TestRellenoGrupoA(unittest.TestCase):
    """Cierres/encabezados fijos de varias fuentes (textos tomados de notas reales)."""

    def verifica(self, fuente, relleno, al_final=True):
        sucio = CUERPO.strip() + " " + relleno if al_final else relleno + " " + CUERPO.strip()
        self.assertEqual(limpiar_relleno(fuente, sucio).strip(), CUERPO.strip())

    def test_repretel_dos_redacciones_y_le_puede_interesar(self):
        base = "Si quiere estar atento a otras informaciones, puede seguir las ediciones de Noticias Repretel en Canal 6, Repretel.com, Facebook Live y YouTube de "
        self.verifica("repretel", base + "Noticias Grupo Repretel.")
        self.verifica("repretel", base + "Grupo Repretel Noticias.")
        self.verifica("repretel", base + "Grupo Repretel Noticias. Le puede interesar: Otro titular de una nota distinta")

    def test_eldelfino_cierre(self):
        self.verifica("eldelfino", "¡Gracias por acompañarnos en una entrega más del acontecer internacional!")

    def test_pulsocr_aviso_de_opinion(self):
        self.verifica("pulsocr", "*La opinión expresada en este espacio no necesariamente representa la línea editorial de PULSOCR, sino exclusivamente responsabilidad de su autor.")

    def test_el_jornal_bloque_de_pie(self):
        self.verifica("el_jornal", "Etiquetas tema uno tema dos Facebook Twitter Pinterest WhatsApp Artículo anterior Otro titular Artículo siguiente Más titulares "
                      "DEJA UNA RESPUESTA Cancelar respuesta Tu dirección de correo electrónico no será publicada. "
                      "Guardar mi nombre, correo electrónico y sitio web en este navegador la próxima vez que comente.")

    def test_alajuela_digital_navegacion(self):
        self.verifica("alajuela_digital", "Navegación de entradas Agenda: otra nota anterior del sitio ¡Asista!")

    def test_teletica_invitacion_a_youtube(self):
        self.verifica("teletica", "No olvide mandarnos sus fotos a las redes sociales de 'Buen Día'. Recuerde que también puede repasar todas las recetas en nuestro canal de Youtube.")

    def test_el_financiero_podcast(self):
        self.verifica("el_financiero", "Acceda a todos los capítulos de este podcast en nuestros canales de Youtube y Spotify.")
        self.verifica("el_financiero", "Acceda a todos los capítulos de este podcast en nuestros canales de Youtube y Spotify. LEA MÁS: Otra nota relacionada")

    def test_costaricastar_invitacion_solo_al_inicio(self):
        frase = "If you would like The Costa Rica Star to publish your business press release, please contact us ."
        self.verifica("costaricastar", frase, al_final=False)
        en_medio = CUERPO.strip() + " " + frase + " " + CUERPO.strip()
        self.assertIn("press release", limpiar_relleno("costaricastar", en_medio))

    def test_caribeactual_invitacion_a_whatsapp_y_teasers(self):
        self.verifica("caribeactual", "¿Le interesan las noticias de Pococí? Únase a nuestro grupo de noticias en Whatsapp La Delegación Regional requiere la colaboración de… La nota completa")

    def test_no_toca_el_texto_sin_relleno(self):
        for f in ("repretel", "eldelfino", "pulsocr", "el_jornal", "alajuela_digital", "teletica", "el_financiero", "costaricastar", "caribeactual"):
            self.assertEqual(limpiar_relleno(f, CUERPO), CUERPO)


class TestRellenoBoletinesYEnlaces(unittest.TestCase):
    """Boletines del OIJ (se conserva el cuerpo) y cierres fijos de guanacastealaaltura (textos de notas reales)."""

    CUERPO_OIJ = ("La Delegación Regional de Cartago del Organismo de Investigación Judicial (OIJ) requiere la colaboración de la "
                  "ciudadanía para ubicar a Juan Pérez, de 34 años, alias Chino, vecino de Tejar, visto por última vez el 20 de enero de 2024.")
    TELEFONO = ("Cualquier información que pueda brindar es indispensable que se comunique al teléfono 800-8000645 "
                "o al WhatsApp 8800-0645 del Centro de Información Confidencial.")
    RECUERDE = ("Recuerde que una persona es requerida por el OIJ por diversas situaciones, entre ellas para informaciones "
                "sobre investigaciones o como testigos de un caso y no necesariamente como imputados.")

    def test_quita_las_frases_fijas_y_conserva_los_datos_propios(self):
        for fuente in ("radiopuertotv", "caribeactual", "tvsur"):
            texto = f"{self.CUERPO_OIJ} {self.TELEFONO} Denuncia interpuesta el 21/01/2024. {self.RECUERDE}"
            self.assertEqual(limpiar_relleno(fuente, texto), f"{self.CUERPO_OIJ} Denuncia interpuesta el 21/01/2024.")

    def test_variantes_del_telefono(self):
        for tel in ("teléfono 800-8000645 del Centro de Información Confidencial.",
                    "teléfono 800-8000645 o al Whatsapp 8800-0645 del Centro de información confidencial.",
                    "teléfono 800 8000 645 o al WhatsApp 8800-0645 del Centro de Información Confidencial"):
            texto = f"{self.CUERPO_OIJ} Cualquier información que pueda brindar es indispensable que se comunique al {tel}"
            self.assertEqual(limpiar_relleno("tvsur", texto), self.CUERPO_OIJ)

    def test_frases_con_espacios_duros(self):
        nb = chr(0xa0)
        tel = nb.join(self.TELEFONO.split(" "))
        self.assertEqual(limpiar_relleno("caribeactual", f"{self.CUERPO_OIJ} {tel}"), self.CUERPO_OIJ)
        self.assertEqual(limpiar_relleno("caribeactual", f"{self.CUERPO_OIJ} {nb.join(self.RECUERDE.split(chr(32)))}"), self.CUERPO_OIJ)

    def test_variante_con_correo(self):
        texto = (f"{self.CUERPO_OIJ} Cualquier información que pueda brindar es indispensable que se comunique al teléfono 800-8000645 "
                 "o al correo cicooij@poder-judicial.go.cr del Centro de Información Confidencial del OIJ.")
        self.assertEqual(limpiar_relleno("radiopuertotv", texto), self.CUERPO_OIJ)

    def test_no_pega_la_frase_siguiente(self):
        texto = f"{self.CUERPO_OIJ} {self.TELEFONO}La Delegación Regional de Limón informa otro caso distinto de este boletín."
        self.assertEqual(limpiar_relleno("radiopuertotv", texto),
                         f"{self.CUERPO_OIJ} La Delegación Regional de Limón informa otro caso distinto de este boletín.")

    def test_no_toca_la_cita_textual_dentro_de_una_nota(self):
        cita = ("«Cualquier información que pueda brindar es indispensable que se comunique al teléfono 800-8000645 "
                "del Centro de Información Confidencial», indicó la oficina de prensa del OIJ.")
        texto = f"{self.CUERPO_OIJ} {cita}"
        self.assertEqual(limpiar_relleno("caribeactual", texto), texto)

    def test_no_deja_la_nota_casi_vacia(self):
        texto = f"Se busca a una persona. {self.TELEFONO}"
        self.assertEqual(limpiar_relleno("radiopuertotv", texto), texto)

    def test_otras_fuentes_no_se_tocan(self):
        texto = f"{self.CUERPO_OIJ} {self.TELEFONO}"
        self.assertEqual(limpiar_relleno("crhoy", texto), texto)

    def test_cierre_del_imn(self):
        base = CUERPO.strip() + " Estas ráfagas pueden alcanzar 80 km/h en algunos casos aislados."
        for cierre in ("Manténganse informados a través de las redes sociales, Twitter: IMNCR, Facebook: Instituto Meteorológico Nacional CR y la Página WEB: http://www.imn.ac.cr",
                       "Mantenerse informados a través de las redes sociales, Twitter: IMNCR, Facebook: Instituto Meteorológico Nacional CR y la Página WEB: www.imn.ac.cr"):
            self.assertEqual(limpiar_relleno("guanacastealaaltura", base + " " + cierre), base)

    def test_enlaces_del_reporte_covid(self):
        base = CUERPO.strip() + " Por edad son 847 adultos, 1.886 adultos mayores y cuatro menores de edad."
        for cierre in ("Datos diarios (nacional, cantonal y hospitalizaciones): https://bit.ly/3hIMPk4 Disponibles para descarga: https://bit.ly/2UYCtCM",
                       "Actualización diaria (nacional, cantonal y hospitalizaciones): https://bit.ly/3hIMPk4 Datos disponibles para descarga: https://bit.ly/2UYCtCM"):
            self.assertEqual(limpiar_relleno("guanacastealaaltura", base + " " + cierre), base)

    def test_deja_los_enlaces_de_los_comunicados(self):
        texto = CUERPO.strip() + " Puede descargar su solicitud en el sitio https://pensiones.ccss.sa.cr y remitirlo por correo."
        self.assertEqual(limpiar_relleno("guanacastealaaltura", texto), texto)


if __name__ == "__main__":
    unittest.main()
