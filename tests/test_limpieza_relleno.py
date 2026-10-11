"""Reglas de limpieza del relleno de cada sitio (textos tomados de notas reales)."""
import unittest

import pandas as pd

from tests._util import RAIZ  # noqa: F401
from limpieza_relleno import MIN_RESTANTE, es_plantilla, limpiar_relleno
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

    def test_plantilla(self):
        self.assertTrue(es_plantilla("Mauris mattis auctor cursus. Phasellus tellus tellus"))
        self.assertTrue(es_plantilla("Lorem ipsum dolor sit amet"))
        self.assertFalse(es_plantilla(CUERPO))

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


if __name__ == "__main__":
    unittest.main()
