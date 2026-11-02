import json
import os
import re
import sqlite3
import time
from datetime import datetime
from urllib.request import urlopen
import xml.etree.ElementTree as ET
from typing import List, Dict, Tuple

FEED_URL = "https://ssl.smn.gob.ar/feeds/avisocorto_GeoRSS.xml"
DB_PATH = "weather_alerts.sqlite"

# Cache de los avisos vigentes que matchean el SW (despachados o no), para
# que la pestaña Alertas de la webapp los lea sin pegarle al feed de SMN en
# cada carga -- eso fue justamente lo que nos rate-limitió una vez.
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "short_term_alerts_cache.json")

NS = {
    "dc":     "http://purl.org/dc/elements/1.1/",
    "georss": "http://www.georss.org/georss",
}


def fetch_xml(url: str) -> bytes:
    with urlopen(url, timeout=30) as r:
        return r.read()


def parse_georss_polygon(polygon_str: str) -> List[Tuple[float, float]]:
    """
    GeoRSS polygon: 'lat1 lon1 lat2 lon2 ...' → [(lat, lon), ...]
    """
    parts = polygon_str.strip().split()
    coords = []
    for i in range(0, len(parts) - 1, 2):
        try:
            coords.append((float(parts[i]), float(parts[i + 1])))
        except ValueError:
            continue
    return coords


def strip_html(html: str) -> str:
    """Elimina tags HTML e imágenes, devuelve texto limpio."""
    # Eliminar tags <img ...>
    html = re.sub(r"<img[^>]*>", "", html, flags=re.IGNORECASE)
    # Eliminar todos los tags restantes
    html = re.sub(r"<[^>]+>", " ", html)
    # Normalizar espacios y saltos
    html = re.sub(r"\s+", " ", html).strip()
    return html


def extract_phenomenon(description_html: str) -> str:
    """
    Extrae el tipo de fenómeno del CDATA de description.
    Ej: 'TORMENTAS FUERTES CON LLUVIAS INTENSAS, RAFAGAS Y CAIDA DE GRANIZO'
    """
    match = re.search(
        r"por ocurrencia de\s*(.+?)</b>",
        description_html,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return "ALERTA METEOROLOGICA"


def parse_feed(feed_xml: bytes) -> List[Dict]:
    root = ET.fromstring(feed_xml)
    items = []

    for item in root.findall(".//item"):
        title_el = item.find("title")
        desc_el  = item.find("description")
        date_el  = item.find("dc:date", NS)
        poly_el  = item.find("georss:polygon", NS)

        title       = (title_el.text or "").strip() if title_el is not None else ""
        description = (desc_el.text  or "").strip() if desc_el  is not None else ""
        dc_date     = (date_el.text  or "").strip() if date_el  is not None else ""
        polygon_str = (poly_el.text  or "").strip() if poly_el  is not None else ""

        if not dc_date or not polygon_str:
            continue

        polygon           = parse_georss_polygon(polygon_str)
        phenomenon        = extract_phenomenon(description)
        description_clean = strip_html(description)

        items.append({
            "title":             title,
            "dc_date":           dc_date,
            "polygon":           polygon,
            "phenomenon":        phenomenon,
            "description_clean": description_clean,
        })

    return items


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS short_term_alert_dispatches (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            dc_date      TEXT NOT NULL UNIQUE,
            phenomenon   TEXT,
            dispatched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def is_dispatched(conn: sqlite3.Connection, dc_date: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM short_term_alert_dispatches WHERE dc_date = ?", (dc_date,)
    ).fetchone() is not None


def mark_dispatched(conn: sqlite3.Connection, dc_date: str, phenomenon: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO short_term_alert_dispatches (dc_date, phenomenon) VALUES (?, ?)",
        (dc_date, phenomenon),
    )
    conn.commit()


def fetch_new_items() -> List[Dict]:
    """
    Descarga el feed y devuelve todos los items parseados.
    No filtra por geo ni por dispatched — eso lo hace el llamador.
    """
    feed_xml = fetch_xml(FEED_URL)
    return parse_feed(feed_xml)


def save_current_items(items: List[Dict]) -> None:
    """Pisa el cache con los items vigentes (ya con affected_partidos
    calculado por el llamador). El polygon no se guarda, no hace falta
    para mostrarlo en la webapp."""
    data = {
        "updated_at": int(time.time()),
        "items": [
            {
                "dc_date":           it.get("dc_date", ""),
                "title":             it.get("title", ""),
                "phenomenon":        it.get("phenomenon", ""),
                "description_clean": it.get("description_clean", ""),
                "affected_partidos": it.get("affected_partidos", []),
            }
            for it in items
        ],
    }
    with open(CACHE_PATH, "w") as f:
        json.dump(data, f)


def get_current_items() -> Dict:
    """Devuelve {'updated_at': ts, 'items': [...]} del cache, o
    {'updated_at': None, 'items': []} si todavía no se guardó nada."""
    if not os.path.exists(CACHE_PATH):
        return {"updated_at": None, "items": []}
    try:
        with open(CACHE_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"updated_at": None, "items": []}
