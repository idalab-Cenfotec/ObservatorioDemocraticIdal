# -*- coding: utf-8 -*-
"""Extracción de enlaces y de notas de noticiaslagaritacr.com.

El sitio cambió de tema en noviembre de 2026: el listado pasó de <h4 class="title"> a
<article class="tarjeta"> con <h3><a>, y la nota pasó de <article class="small single"> a
<article class="articulo"> con el cuerpo en <div class="contenido">. Se conservan los
selectores del tema anterior como respaldo.
"""
import re

from bs4 import BeautifulSoup

_RUIDO = ['script', 'style', 'iframe', 'figure', 'nav', 'aside']


def clean_text(text):
    """Elimina espacios/saltos de línea extra."""
    return re.sub(r'\s+', ' ', text).strip() if text else ""


def extraer_enlaces(soup):
    """Enlaces a notas desde una página de categoría (tema nuevo y, como respaldo, el anterior)."""
    enlaces = []
    for tarjeta in soup.find_all('article', class_='tarjeta'):
        a = tarjeta.find('h3')
        a = a.find('a', href=True) if a else None
        if a:
            enlaces.append(a['href'])
    if not enlaces:
        for h4 in soup.find_all('h4', class_='title'):
            a = h4.find('a', href=True)
            if a:
                enlaces.append(a['href'])
    return enlaces


def extract_article_data(soup):
    """Devuelve (título, fecha YYYY-MM-DD, contenido) de la página de una nota."""
    title = None
    nuevo = soup.find('article', class_='articulo')
    if nuevo and nuevo.find('h1'):
        title = clean_text(nuevo.find('h1').get_text())
    else:
        mg_header = soup.find('div', class_='mg-header')
        h1 = mg_header.find('h1', class_='title') if mg_header else None
        if h1:
            title = clean_text(h1.get_text())

    date = None
    meta_date = soup.find('meta', property='article:published_time')
    if meta_date:
        date = meta_date.get('content', '')[:10]

    content = None
    cuerpo = nuevo.find('div', class_='contenido') if nuevo else None
    if cuerpo is None:
        cuerpo = soup.find('article', class_='small single')
    if cuerpo is not None:
        for tag in cuerpo.find_all(_RUIDO):
            tag.decompose()
        for div in cuerpo.find_all('div', class_=re.compile(r'post-share|gallery')):
            div.decompose()
        parrafos = cuerpo.find_all('p')
        # Con <p> sueltos se unen con espacio para no pegar la última palabra de uno con la primera del siguiente.
        content = clean_text(' '.join(p.get_text() for p in parrafos) if parrafos else cuerpo.get_text())

    return title, date, content
