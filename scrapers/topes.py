"""
topes.py
Topes de tiempo (en minutos) por scraper.

Cada scraper deja de visitar artículos al llegar a su tope y devuelve lo reunido
hasta ese momento; el listado usa como máximo la mitad (ver BaseScraper.tiempo_agotado).
El valor sale de los tiempos reales de la corrida del 5-oct-2026 y de que la suma
de los topes de un grupo, repartida entre sus workers, quede bien por debajo del
timeout del job (grupo 1: 180 min, 2: 240, 3: 300, 4: 360).

Un scraper que no aparece aquí usa SCRAPER_MAX_MINUTOS (variable de entorno, que el
workflow fija por grupo); sin ninguno de los dos no tiene tope.
"""

TOPES_MINUTOS = {
    # Grupo 1 (4 workers, job de 180 min)
    "sinartdigital": 45,
    "monumental": 45,
    "telediario": 45,
    "ticosland": 45,
    "pulsocr": 45,
    "theglobalcr": 45,
    "puntarenasseoye": 60,   # primera carga: 5,855 notas pendientes, ~34/min => ~2,000 por corrida
    "caribeactual": 30,
    "costaricastar": 15,     # sitio inactivo desde oct-2022; en pausa (ver pipeline.yml)
    "elperiodicocr": 15,
    "presidencia": 10,
    # Grupo 2 (6 workers, job de 240 min)
    "ncrnoticias": 90,
    "trivisioncr": 90,
    "ticotimes": 90,
    "latejacr": 90,
    "rumboeconomico": 60,
    "vozdeguanacaste": 60,
    # Grupo 3 (job de 300 min)
    "lareaccioncr": 120,
    "noticiasenlineacr": 120,
    "puroperiodismo": 120,
    "larevistacr": 120,
    "lavozdegoicoechea": 60,
    # Grupo 4 (job de 360 min)
    "teletica": 150,
    "repretel": 150,
    "genteopa": 150,
}

# Protecciones contra páginas colgadas (segundos)
LIMITE_ARTICULO_SEG = 120    # una nota que no termina en 2 min se omite y se sigue con la siguiente
LIMITE_SECCION_SEG = 1200    # recolectar los enlaces de una sección: 20 min como máximo
LIMITE_CIERRE_SEG = 10       # page.close() puede colgarse si la página falló (puroperiodismo, 5-oct-2026)
GUARDADO_PARCIAL_CADA = 100  # artículos entre cada guardado parcial del CSV
