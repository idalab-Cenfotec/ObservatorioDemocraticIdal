"""Cuerpo de puntarenasseoye.com: debe incluir los párrafos que vienen en <div> sueltos y no el contenido ajeno."""
import unittest

from tests._util import RAIZ  # noqa: F401
from scrapers.puntarenasseoye_cuerpo import extraer_cuerpo

# Estructura real de una publicación de sucesos: párrafos en <div> y solo el último en <p>.
SUCESOS = """
<div class="stream-item stream-item-above-post-content">Publicidad</div>
<div>Dos hombres, nacionalidad panameña, fueron capturados como sospechosos de dar muerte a un vecino.</div>
<div></div>
<div>El hecho se registró en el kilómetro 27 de Santa Lucía, en el cantón de Corredores.</div>
<div></div>
<div>De inmediato, la Fuerza Pública desplazó varias unidades y realizó una rápida búsqueda.</div>
<ul><li>LEA TAMBIÉN: Guardacostas y pescadores se unen para erradicar la pesca ilegal</li></ul>
<p>El caso queda ahora en manos del Organismo de Investigación Judicial (OIJ).</p>
"""

# Estructura habitual: todo en <p>, algunos dentro de un contenedor.
CON_PARRAFOS = """
<div class="stream-item stream-item-above-post-content"><p>Anuncio patrocinado</p></div>
<p>Primer párrafo de la nota.</p>
<div class="wp-block"><p>Segundo párrafo dentro de un contenedor.</p><p>Tercer párrafo.</p></div>
<p>   </p>
<p>Cuarto párrafo.</p>
<iframe src="x"></iframe><script>var x = 1;</script>
"""


class TestCuerpoPuntarenasSeOye(unittest.TestCase):
    def test_incluye_los_parrafos_en_div_sueltos(self):
        t = extraer_cuerpo(SUCESOS)
        self.assertTrue(t.startswith("Dos hombres, nacionalidad panameña"))
        self.assertIn("kilómetro 27 de Santa Lucía", t)
        self.assertIn("desplazó varias unidades", t)
        self.assertTrue(t.endswith("Organismo de Investigación Judicial (OIJ)."))

    def test_no_incluye_publicidad_ni_enlaces_relacionados(self):
        t = extraer_cuerpo(SUCESOS)
        self.assertNotIn("Publicidad", t)
        self.assertNotIn("LEA TAMBIÉN", t)

    def test_mantiene_el_comportamiento_con_parrafos(self):
        self.assertEqual(
            extraer_cuerpo(CON_PARRAFOS),
            "Primer párrafo de la nota. Segundo párrafo dentro de un contenedor. Tercer párrafo. Cuarto párrafo.")

    def test_p_sin_cerrar_no_arrastra_publicidad_whatsapp_ni_etiquetas(self):
        # En la página real el último <p> queda sin cerrar y lo que sigue queda anidado dentro de él.
        html = (SUCESOS.rsplit("<p>", 1)[0] + "<p>El caso queda ahora en manos del OIJ."
                '<div class="stream-item stream-item-below-post-content"><p>Canal oficial</p>'
                "<p>Puntarenas Se Oye en WhatsApp</p><p>Gratis · Sin costo · Podés salir cuando quieras</p></div>"
                '<div class="post-bottom-meta post-bottom-tags"><div class="post-bottom-meta-title">Etiquetas</div>'
                "<a>Asesinato</a><a>Sucesos</a></div>")
        t = extraer_cuerpo(html)
        self.assertTrue(t.endswith("El caso queda ahora en manos del OIJ."))
        for ajeno in ("WhatsApp", "Canal oficial", "Podés salir", "Etiquetas", "Asesinato"):
            self.assertNotIn(ajeno, t)

    def test_no_incluye_videos_ni_anuncios_en_linea(self):
        html = ('<div>Hoy se reportó un incendio en una vivienda de Cóbano.</div>'
                '<p><div class="wp-video"><video class="wp-video-shortcode"><a href="https://x.cr/wp-content/uploads/a.mp4">'
                'https://x.cr/wp-content/uploads/a.mp4</a></video></div></p>'
                '<div class="prb"><div class="prb-ad"><span class="prb-tag">Publicidad · JPS</span></div></div>'
                '<div>Los bomberos controlaron el fuego.</div>')
        self.assertEqual(extraer_cuerpo(html), "Hoy se reportó un incendio en una vivienda de Cóbano. Los bomberos controlaron el fuego.")

    def test_enlaces_y_negritas_en_linea_no_dejan_espacios_de_mas(self):
        html = '<div>Un hombre de apellido <a href="#">Mata</a>, de 61 años, falleció; <strong>la</strong> víctima<br>era vecino.</div>'
        self.assertEqual(extraer_cuerpo(html), "Un hombre de apellido Mata, de 61 años, falleció; la víctima era vecino.")

    def test_bloques_anidados_quedan_separados(self):
        html = "<div><div>Para el crédito:</div><div>Para el bono:</div><h3>Requisitos</h3><div>Estar al día.</div></div>"
        self.assertEqual(extraer_cuerpo(html), "Para el crédito: Para el bono: Requisitos Estar al día.")

    def test_orden_de_documento(self):
        t = extraer_cuerpo(SUCESOS)
        self.assertLess(t.index("Dos hombres"), t.index("kilómetro 27"))
        self.assertLess(t.index("kilómetro 27"), t.index("El caso queda"))

    def test_vacio(self):
        self.assertEqual(extraer_cuerpo(""), "")
        self.assertEqual(extraer_cuerpo(None), "")
        self.assertEqual(extraer_cuerpo("<div></div><p> </p>"), "")


if __name__ == "__main__":
    unittest.main()
