import re
import sqlite3
from datetime import datetime, date
from urllib.request import urlopen
import xml.etree.ElementTree as ET


FEED_URL = "https://ssl.smn.gob.ar/CAP/AR.php"
DB_PATH = "weather_alerts.sqlite"


def fetch_xml(url: str) -> bytes:
    with urlopen(url, timeout=30) as response:
        return response.read()


def extract_timestamp_from_link(link: str) -> str | None:
    """
    Extrae timestamp del link en dos formatos posibles:
    - Antiguo: https://.../CAP_20260311205445_Tormenta_...
    - Nuevo: https://.../2026_03_26_1728_cap_es.xml
    """
    # Intenta formato antiguo primero
    match = re.search(r"CAP_(\d{14})_", link)
    if match:
        return match.group(1)
    
    # Intenta nuevo formato: YYYY_MM_DD_HHMM
    match = re.search(r"(\d{4})_(\d{2})_(\d{2})_(\d{4})_cap", link)
    if match:
        year, month, day, hhmm = match.groups()
        # Convertir 2026_03_26_1728 a 20260326172800
        return f"{year}{month}{day}{hhmm}00"
    
    return None


def extract_alert_date_from_link(link: str) -> str | None:
    timestamp = extract_timestamp_from_link(link)
    if not timestamp:
        return None
    return timestamp[:8]  # YYYYMMDD


def is_today_alert(link: str) -> bool:
    xml_date = extract_alert_date_from_link(link)
    if not xml_date:
        return False

    today_str = date.today().strftime("%Y%m%d")
    return xml_date == today_str


def parse_feed(feed_xml: bytes) -> list[dict]:
    """
    Devuelve una lista de dicts:
    [
        {"title": "...", "link": "..."},
        ...
    ]
    """
    root = ET.fromstring(feed_xml)
    items = []

    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")

        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        link = link_el.text.strip() if link_el is not None and link_el.text else ""

        if not title or not link:
            continue

        items.append({
            "title": title,
            "link": link,
        })

    return items


def parse_cap_xml(cap_xml: bytes) -> dict:
    """
    Devuelve:
    {
        "urgency": ...,
        "severity": ...,
        "certainty": ...,
        "onset": ...,
        "expires": ...,
        "polygon": ...
    }
    """
    root = ET.fromstring(cap_xml)

    # soporta XML con namespace o sin namespace
    def find_text_anywhere(tag_name: str) -> str:
        # intenta sin namespace
        el = root.find(f".//{tag_name}")
        if el is not None and el.text:
            return el.text.strip()

        # intenta con cualquier namespace
        for candidate in root.findall(".//*"):
            if candidate.tag.endswith(tag_name) and candidate.text:
                return candidate.text.strip()

        return ""

    urgency = find_text_anywhere("urgency")
    severity = find_text_anywhere("severity")
    certainty = find_text_anywhere("certainty")
    onset = find_text_anywhere("onset")
    expires = find_text_anywhere("expires")
    polygon = find_text_anywhere("polygon")

    return {
        "urgency": urgency,
        "severity": severity,
        "certainty": certainty,
        "onset": onset,
        "expires": expires,
        "polygon": polygon,
    }


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weather_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_xml_url TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            alert_date TEXT NOT NULL,
            urgency TEXT,
            severity TEXT,
            certainty TEXT,
            onset TEXT,
            expires TEXT,
            polygon TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_weather_alerts_alert_date
        ON weather_alerts(alert_date)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_weather_alerts_is_active
        ON weather_alerts(is_active)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_weather_alerts_expires
        ON weather_alerts(expires)
    """)

    conn.commit()


def upsert_alert(conn: sqlite3.Connection, alert: dict) -> None:
    conn.execute("""
        INSERT INTO weather_alerts (
            source_xml_url,
            title,
            alert_date,
            urgency,
            severity,
            certainty,
            onset,
            expires,
            polygon,
            is_active,
            last_seen_at,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(source_xml_url) DO UPDATE SET
            title = excluded.title,
            alert_date = excluded.alert_date,
            urgency = excluded.urgency,
            severity = excluded.severity,
            certainty = excluded.certainty,
            onset = excluded.onset,
            expires = excluded.expires,
            polygon = excluded.polygon,
            is_active = 1,
            last_seen_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
    """, (
        alert["source_xml_url"],
        alert["title"],
        alert["alert_date"],
        alert["urgency"],
        alert["severity"],
        alert["certainty"],
        alert["onset"],
        alert["expires"],
        alert["polygon"],
    ))


def deactivate_missing_alerts(conn: sqlite3.Connection, active_urls: set[str]) -> None:
    """
    Marca como inactivas todas las alertas que ya no están en el feed actual.
    """
    if not active_urls:
        return  # feed vacío: no desactivar masivamente (puede ser error de red)

    placeholders = ",".join("?" for _ in active_urls)
    sql = f"""
        UPDATE weather_alerts
        SET is_active = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE is_active = 1
          AND source_xml_url NOT IN ({placeholders})
    """
    conn.execute(sql, list(active_urls))


def run() -> None:
    feed_xml = fetch_xml(FEED_URL)
    items = parse_feed(feed_xml)

    active_urls = set()

    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)

        for item in items:
            title = item["title"]
            link = item["link"]

            alert_date = extract_alert_date_from_link(link) or date.today().strftime("%Y%m%d")

            try:
                cap_xml = fetch_xml(link)
                cap_data = parse_cap_xml(cap_xml)
            except Exception as exc:
                print(f"[WARN] Error procesando {link}: {exc}")
                continue

            alert_record = {
                "source_xml_url": link,
                "title": title,
                "alert_date": alert_date,
                "urgency": cap_data["urgency"],
                "severity": cap_data["severity"],
                "certainty": cap_data["certainty"],
                "onset": cap_data["onset"],
                "expires": cap_data["expires"],
                "polygon": cap_data["polygon"],
            }

            upsert_alert(conn, alert_record)
            active_urls.add(link)

        deactivate_missing_alerts(conn, active_urls)
        conn.commit()


if __name__ == "__main__":
    run()