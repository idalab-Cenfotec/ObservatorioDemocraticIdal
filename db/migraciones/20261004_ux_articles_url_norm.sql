-- Índice único sobre la URL normalizada de articles.
--
-- Por qué: la misma nota puede llegar con la URL escrita distinto (con o sin
-- "www.", http/https, barra final, #fragmento). El UNIQUE sobre url solo detecta
-- coincidencias exactas; el 4-oct-2026 dejó pasar 7,135 duplicados de
-- miprensacr (histórico con "www.", scraper actual sin él). Ya se borraron.
--
-- La expresión debe mantenerse idéntica a output_cleaner.url_key() en Python
-- (clave sin http(s)://, www., #fragmento, barras finales y en minúsculas).
-- Con este índice el workflow de N8N usa "ON CONFLICT DO NOTHING" (sin objetivo)
-- para ignorar conflictos tanto de url como de url normalizada.
--
-- Aplicar como el dueño de la tabla (devidalab). Es idempotente. Falla si la
-- tabla ya tiene duplicados normalizados: en ese caso listarlos y resolverlos
-- antes (ver el ejemplo de consulta al final).

CREATE UNIQUE INDEX IF NOT EXISTS ux_articles_url_norm ON articles ((
    lower(regexp_replace(regexp_replace(regexp_replace(url, '^https?://(www\.)?', ''), '#.*$', ''), '/+$', ''))
));

-- Consulta de verificación (debe devolver 0 filas):
-- SELECT lower(regexp_replace(regexp_replace(regexp_replace(url, '^https?://(www\.)?', ''), '#.*$', ''), '/+$', '')) AS k, count(*)
-- FROM articles GROUP BY k HAVING count(*) > 1;
