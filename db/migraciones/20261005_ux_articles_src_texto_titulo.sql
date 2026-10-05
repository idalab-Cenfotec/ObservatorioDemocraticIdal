-- Evita que la misma nota (mismo medio, mismo texto y mismo título) entre dos veces con URLs distintas
-- (slugs "-2", /index.php/, años mal formados). Parcial: solo textos de más de 300 caracteres, porque
-- las plantillas cortas (avisos OIJ, pronósticos) repiten texto legítimamente.
-- Los hashes evitan el límite de tamaño de fila del índice btree.
-- El webhook ingesta_desde_pipeline usa ON CONFLICT DO NOTHING sin objetivo, así que lo respeta sin cambios.
CREATE UNIQUE INDEX IF NOT EXISTS ux_articles_src_texto_titulo
    ON articles (source, md5(full_text), md5(lower(btrim(title))))
    WHERE length(full_text) > 300;
