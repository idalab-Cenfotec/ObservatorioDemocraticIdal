"""Interruptor de la escritura del corpus a CSV (SCRUM-29).

Mientras dure la transición, el corpus se guarda en CSV (en el caché y el
artifact `corpus-maestro` de GitHub Actions) y además se envía a PostgreSQL
por el webhook de N8N. Este módulo decide si la parte CSV sigue activa.

Variable de entorno ESCRIBIR_CSV:
  - sin definir, "true", "1", "si"  -> se escribe el CSV (comportamiento actual)
  - "false", "0", "no"              -> no se escribe una nueva versión del corpus CSV;
                                       PostgreSQL queda como única fuente

En GitHub Actions se toma de la variable del repositorio `ESCRIBIR_CSV`
(Settings -> Secrets and variables -> Actions -> Variables). Apagar el CSV es
crearla con el valor `false`; revertir es borrarla o ponerla en `true`.

Salvaguarda: si no hay webhook de N8N configurado, el CSV se escribe igual,
porque si no los artículos no quedarían guardados en ningún lado.
"""
import os

_APAGADO = {"false", "0", "no", "off"}


def escribir_csv_activo() -> bool:
    """True si hay que seguir escribiendo el corpus a CSV."""
    return os.environ.get("ESCRIBIR_CSV", "true").strip().lower() not in _APAGADO


def debe_escribir_csv(webhook_url: str | None) -> bool:
    """Aplica la salvaguarda: sin webhook, el CSV es el único destino y se escribe siempre."""
    return escribir_csv_activo() or webhook_url is None
