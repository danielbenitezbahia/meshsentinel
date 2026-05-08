import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from weather_alert_geo import parse_polygon, point_in_polygon_optimized
from typing import List, Tuple, Dict
import logging
logger = logging.getLogger(__name__)
from datetime import datetime, timezone
import json
import os
import re
import unicodedata
from typing import Dict, List, Tuple

DB_PATH = "weather_alerts.sqlite"
PARTIDOS_GEOJSON_PATH = "partidos.geojson"

TARGET_PARTIDOS = {
    "bahia blanca": "Bahia Blanca",
    "coronel de marina leonardo rosales": "Coronel Rosales",
    "coronel dorrego": "Coronel Dorrego",
    "monte hermoso": "Monte Hermoso",
    "tornquist": "Tornquist",    
    "puan": "Puan",
    "coronel pringles": "Coronel Pringles",
    "villarino": "Villarino",
    "adolfo alsina": "Adolfo Alsina",
    "guamini": "Guamini",
    "patagones": "Patagones",
}

_PARTIDOS_CACHE = None
DB_PATH = "weather_alerts.sqlite"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
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
            CREATE TABLE IF NOT EXISTS weather_alert_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL UNIQUE,
                is_subscribed INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS weather_alert_dispatches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id TEXT NOT NULL,
                source_xml_url TEXT NOT NULL,
                dispatched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'sent',
                UNIQUE(node_id, source_xml_url)
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

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_weather_alert_subscriptions_node_id
            ON weather_alert_subscriptions(node_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_weather_alert_dispatches_node_id
            ON weather_alert_dispatches(node_id)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_weather_alert_dispatches_source_xml_url
            ON weather_alert_dispatches(source_xml_url)
        """)

        conn.commit()


def subscribe_node(node_id: str) -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO weather_alert_subscriptions (
                node_id,
                is_subscribed,
                created_at,
                updated_at
            )
            VALUES (?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(node_id) DO UPDATE SET
                is_subscribed = 1,
                updated_at = CURRENT_TIMESTAMP
        """, (node_id,))
        conn.commit()


def unsubscribe_node(node_id: str) -> None:
    with get_connection() as conn:
        conn.execute("""
            UPDATE weather_alert_subscriptions
            SET is_subscribed = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE node_id = ?
        """, (node_id,))
        conn.commit()


def is_node_subscribed(node_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute("""
            SELECT is_subscribed
            FROM weather_alert_subscriptions
            WHERE node_id = ?
            LIMIT 1
        """, (node_id,)).fetchone()

        if row is None:
            return False

        return bool(row["is_subscribed"])


def get_subscribed_nodes() -> List[str]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT node_id
            FROM weather_alert_subscriptions
            WHERE is_subscribed = 1
            ORDER BY node_id
        """).fetchall()

        return [row["node_id"] for row in rows]


def register_alert_dispatch(node_id: str, source_xml_url: str, status: str = "sent") -> None:
    with get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO weather_alert_dispatches (
                node_id,
                source_xml_url,
                dispatched_at,
                status
            )
            VALUES (?, ?, CURRENT_TIMESTAMP, ?)
        """, (node_id, source_xml_url, status))
        conn.commit()


def was_alert_already_sent(node_id: str, source_xml_url: str) -> bool:
    with get_connection() as conn:
        row = conn.execute("""
            SELECT 1
            FROM weather_alert_dispatches
            WHERE node_id = ?
              AND source_xml_url = ?
            LIMIT 1
        """, (node_id, source_xml_url)).fetchone()

        return row is not None


def get_alert_dispatches_for_node(node_id: str) -> List[Dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                id,
                node_id,
                source_xml_url,
                dispatched_at,
                status
            FROM weather_alert_dispatches
            WHERE node_id = ?
            ORDER BY dispatched_at DESC
        """, (node_id,)).fetchall()

        return [dict(row) for row in rows]


def get_all_active_alerts() -> List[Dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                id,
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
            FROM weather_alerts
            WHERE is_active = 1
            ORDER BY expires ASC, title ASC
        """).fetchall()

        return [dict(row) for row in rows]


def parse_iso_dt(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def get_currently_valid_alerts() -> List[Dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                source_xml_url,
                title,
                alert_date,
                urgency,
                severity,
                certainty,
                onset,
                expires,
                polygon,
                is_active
            FROM weather_alerts
            WHERE is_active = 1
            ORDER BY title, expires
        """).fetchall()

    now = datetime.now().astimezone()
    result = []

    for row in rows:
        alert = dict(row)

        onset_dt = parse_iso_dt(alert.get("onset"))
        expires_dt = parse_iso_dt(alert.get("expires"))

        if not expires_dt:
            continue

        # vigente si todavía no expiró
        if expires_dt > now:
            result.append(alert)

    logger.info("SMN DEBUG: currently valid alerts after Python time filter: %d", len(result))
    return result


def get_unsent_alerts_for_node(node_id: str, now: Optional[datetime] = None) -> List[Dict]:
    """
    Devuelve alertas activas/no expiradas que todavía no fueron enviadas al nodo.
    Aún no filtra por polygon ni ubicación.
    """
    valid_alerts = get_currently_valid_alerts(now=now)
    result: List[Dict] = []

    for alert in valid_alerts:
        if not was_alert_already_sent(node_id, alert["source_xml_url"]):
            result.append(alert)

    return result


def mark_old_alerts_inactive(cutoff_hours: int = 48) -> int:
    """
    Marca como inactivas alertas muy viejas según expires.
    Es opcional, útil para limpieza.
    """
    cutoff = datetime.now() - timedelta(hours=cutoff_hours)
    cutoff_str = cutoff.isoformat(timespec="seconds")

    with get_connection() as conn:
        cur = conn.execute("""
            UPDATE weather_alerts
            SET is_active = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE is_active = 1
              AND expires < ?
        """, (cutoff_str,))
        conn.commit()
        return cur.rowcount


def delete_all_subscriptions() -> int:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM weather_alert_subscriptions")
        conn.commit()
        return cur.rowcount


def delete_all_dispatches() -> int:
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM weather_alert_dispatches")
        conn.commit()
        return cur.rowcount


def print_debug_summary() -> None:
    with get_connection() as conn:
        total_alerts = conn.execute("SELECT COUNT(*) AS c FROM weather_alerts").fetchone()["c"]
        active_alerts = conn.execute("""
            SELECT COUNT(*) AS c
            FROM weather_alerts
            WHERE is_active = 1
        """).fetchone()["c"]

        subscribed_nodes = conn.execute("""
            SELECT COUNT(*) AS c
            FROM weather_alert_subscriptions
            WHERE is_subscribed = 1
        """).fetchone()["c"]

        dispatches = conn.execute("""
            SELECT COUNT(*) AS c
            FROM weather_alert_dispatches
        """).fetchone()["c"]

    print(f"[INFO] Total alertas: {total_alerts}")
    print(f"[INFO] Alertas activas: {active_alerts}")
    print(f"[INFO] Nodos suscriptos: {subscribed_nodes}")
    print(f"[INFO] Dispatches registrados: {dispatches}")

def does_alert_match_node_location(alert: dict, node_lat: float, node_lon: float) -> bool:
    """
    Devuelve True si la ubicación del nodo cae dentro del polygon de la alerta.
    """
    polygon_str = alert.get("polygon", "")
    if not polygon_str:
        return False

    polygon = parse_polygon(polygon_str)
    if not polygon:
        return False

    return point_in_polygon_optimized(node_lat, node_lon, polygon)


def get_matching_alerts_for_node_location(node_lat: float, node_lon: float, now: Optional[datetime] = None) -> List[Dict]:
    """
    Devuelve alertas activas/no expiradas que aplican a una ubicación.
    """
    valid_alerts = get_currently_valid_alerts(now=now)
    matches: List[Dict] = []

    for alert in valid_alerts:
        if does_alert_match_node_location(alert, node_lat, node_lon):
            matches.append(alert)

    return matches


def get_unsent_matching_alerts_for_node(node_id: str, node_lat: float, node_lon: float, now: Optional[datetime] = None) -> List[Dict]:
    """
    Devuelve alertas que:
    - son activas y no expiradas
    - aplican a la ubicación del nodo
    - todavía no fueron enviadas a ese nodo
    """
    matching_alerts = get_matching_alerts_for_node_location(node_lat, node_lon, now=now)
    result: List[Dict] = []

    for alert in matching_alerts:
        if not was_alert_already_sent(node_id, alert["source_xml_url"]):
            result.append(alert)

    return result

# Polígono aproximado del sudoeste bonaerense
# Formato: (lat, lon)
SUDOESTE_BA_POLYGON = [
    (-37.0, -63.5),
    (-38.2, -63.8),
    (-39.5, -63.7),
    (-40.8, -63.2),
    (-41.3, -62.0),
    (-41.4, -60.2),
    (-40.8, -59.2),
    (-39.6, -58.9),
    (-38.4, -59.4),
    (-37.3, -60.3),
    (-37.0, -61.5),
    (-37.0, -63.5),
]


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def parse_polygon_string(polygon_str: str) -> List[Tuple[float, float]]:
    """
    Convierte string tipo:
    '-44.68,-70.98 -44.68,-70.96 ...'
    a lista [(lat, lon), ...]
    """
    points = []
    if not polygon_str:
        return points

    raw_points = polygon_str.strip().split()
    for raw in raw_points:
        try:
            lat_str, lon_str = raw.split(",", 1)
            lat = float(lat_str)
            lon = float(lon_str)
            points.append((lat, lon))
        except Exception:
            continue

    return points


def point_in_polygon(lat: float, lon: float, polygon: List[Tuple[float, float]]) -> bool:
    """
    Ray casting.
    polygon: lista de (lat, lon)
    """
    if len(polygon) < 3:
        return False

    inside = False
    j = len(polygon) - 1

    for i in range(len(polygon)):
        yi, xi = polygon[i]   # lat, lon
        yj, xj = polygon[j]   # lat, lon

        intersects = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) + 1e-12) + xi
        )
        if intersects:
            inside = not inside

        j = i

    return inside


def polygons_intersect_simple(poly_a: List[Tuple[float, float]], poly_b: List[Tuple[float, float]]) -> bool:
    """
    Aproximación práctica:
    - si algún punto de A está dentro de B
    - o algún punto de B está dentro de A
    """
    if not poly_a or not poly_b:
        return False

    for lat, lon in poly_a:
        if point_in_polygon(lat, lon, poly_b):
            return True

    for lat, lon in poly_b:
        if point_in_polygon(lat, lon, poly_a):
            return True

    return False


def get_currently_valid_alerts() -> List[Dict]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                source_xml_url,
                title,
                alert_date,
                urgency,
                severity,
                certainty,
                onset,
                expires,
                polygon,
                is_active
            FROM weather_alerts
            WHERE is_active = 1
            ORDER BY title, expires
        """).fetchall()

    now = datetime.now().astimezone()
    result = []

    for row in rows:
        alert = dict(row)

        onset_dt = parse_iso_dt(alert.get("onset"))
        expires_dt = parse_iso_dt(alert.get("expires"))

        if not expires_dt:
            continue

        # vigente si todavía no expiró
        if expires_dt > now:
            result.append(alert)

    logger.info("SMN DEBUG: currently valid alerts after Python time filter: %d", len(result))
    return result


def get_sudoeste_ba_alerts() -> List[Dict]:
    logger.info("SMN DEBUG: get_sudoeste_ba_alerts() called")

    alerts = get_currently_valid_alerts()
    partidos = load_target_partidos()
    result = []

    logger.info("SMN DEBUG: valid alerts retrieved: %d", len(alerts))

    for alert in alerts:
        polygon_str = alert.get("polygon") or ""
        alert_polygon = parse_polygon_string(polygon_str)

        if not alert_polygon:
            logger.info(
                "SMN DEBUG: skipping alert without polygon: %s",
                alert.get("source_xml_url"),
            )
            continue

        matched_partidos = []

        for partido_name, partido_polygons in partidos.items():
            for partido_poly in partido_polygons:
                if polygons_intersect(alert_polygon, partido_poly):
                    matched_partidos.append(partido_name)
                    break

        if matched_partidos:
            alert_copy = dict(alert)
            alert_copy["affected_partidos"] = sorted(set(matched_partidos))

            logger.info(
                "SMN DEBUG: MATCH -> %s | %s | onset=%s | expires=%s | partidos=%s",
                alert.get("title"),
                alert.get("severity"),
                alert.get("onset"),
                alert.get("expires"),
                ",".join(alert_copy["affected_partidos"]),
            )

            result.append(alert_copy)

    logger.info("SMN DEBUG: total alerts intersecting target partidos: %d", len(result))
    return result

def normalize_text(value: str) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"\s+", " ", value)
    return value


def parse_polygon_string(polygon_str: str) -> List[Tuple[float, float]]:
    points = []
    if not polygon_str:
        return points

    for raw in polygon_str.strip().split():
        try:
            lat_str, lon_str = raw.split(",", 1)
            points.append((float(lat_str), float(lon_str)))
        except Exception:
            continue

    return points


def point_in_polygon(lat: float, lon: float, polygon: List[Tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False

    inside = False
    j = len(polygon) - 1

    for i in range(len(polygon)):
        yi, xi = polygon[i]   # lat, lon
        yj, xj = polygon[j]   # lat, lon

        intersects = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) + 1e-12) + xi
        )
        if intersects:
            inside = not inside

        j = i

    return inside


def on_segment(a, b, c) -> bool:
    return (
        min(a[0], c[0]) - 1e-12 <= b[0] <= max(a[0], c[0]) + 1e-12
        and min(a[1], c[1]) - 1e-12 <= b[1] <= max(a[1], c[1]) + 1e-12
    )


def orientation(a, b, c) -> int:
    val = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
    if abs(val) < 1e-12:
        return 0
    return 1 if val > 0 else 2


def segments_intersect(p1, q1, p2, q2) -> bool:
    o1 = orientation(p1, q1, p2)
    o2 = orientation(p1, q1, q2)
    o3 = orientation(p2, q2, p1)
    o4 = orientation(p2, q2, q1)

    if o1 != o2 and o3 != o4:
        return True

    if o1 == 0 and on_segment(p1, p2, q1):
        return True
    if o2 == 0 and on_segment(p1, q2, q1):
        return True
    if o3 == 0 and on_segment(p2, p1, q2):
        return True
    if o4 == 0 and on_segment(p2, q1, q2):
        return True

    return False


def polygon_edges(poly: List[Tuple[float, float]]):
    if len(poly) < 2:
        return
    for i in range(len(poly)):
        yield poly[i], poly[(i + 1) % len(poly)]


def polygons_intersect(poly_a: List[Tuple[float, float]], poly_b: List[Tuple[float, float]]) -> bool:
    if not poly_a or not poly_b:
        return False

    for lat, lon in poly_a:
        if point_in_polygon(lat, lon, poly_b):
            return True

    for lat, lon in poly_b:
        if point_in_polygon(lat, lon, poly_a):
            return True

    for a1, a2 in polygon_edges(poly_a):
        for b1, b2 in polygon_edges(poly_b):
            if segments_intersect(a1, a2, b1, b2):
                return True

    return False


def geometry_to_polygons(geometry: dict) -> List[List[Tuple[float, float]]]:
    if not geometry:
        return []

    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])

    polygons = []

    if gtype == "Polygon":
        if coords:
            ring = coords[0]  # exterior
            polygons.append([(lat, lon) for lon, lat in ring])

    elif gtype == "MultiPolygon":
        for poly in coords:
            if poly:
                ring = poly[0]
                polygons.append([(lat, lon) for lon, lat in ring])

    return polygons

def load_target_partidos() -> Dict[str, List[List[Tuple[float, float]]]]:
    global _PARTIDOS_CACHE

    if _PARTIDOS_CACHE is not None:
        return _PARTIDOS_CACHE

    if not os.path.exists(PARTIDOS_GEOJSON_PATH):
        logger.error("SMN DEBUG: GeoJSON de partidos no encontrado: %s", PARTIDOS_GEOJSON_PATH)
        _PARTIDOS_CACHE = {}
        return _PARTIDOS_CACHE

    with open(PARTIDOS_GEOJSON_PATH, "r", encoding="utf-8") as f:
        geo = json.load(f)

    result = {}

    for feat in geo.get("features", []):
        props = feat.get("properties", {}) or {}
        geometry = feat.get("geometry", {}) or {}

        nombre = props.get("nombre") or ""
        nombre_completo = props.get("nombre_completo") or ""

        candidates = [
            normalize_text(nombre),
            normalize_text(nombre_completo),
        ]

        matched_key = None
        for target_key in TARGET_PARTIDOS.keys():
            if target_key in candidates or any(target_key in c for c in candidates):
                matched_key = target_key
                break

        if not matched_key:
            continue

        polygons = geometry_to_polygons(geometry)
        if not polygons:
            continue

        short_name = TARGET_PARTIDOS[matched_key]
        result[short_name] = polygons

    logger.info("SMN DEBUG: partidos cargados desde geojson: %s", sorted(result.keys()))
    _PARTIDOS_CACHE = result
    return _PARTIDOS_CACHE



if __name__ == "__main__":
    init_db()
    print("[OK] Weather alert service DB initialized")

    # pruebas mínimas de ejemplo
    subscribe_node("!abcd1234")
    subscribe_node("!efgh5678")

    print("[INFO] Nodos suscriptos actuales:")
    for node_id in get_subscribed_nodes():
        print(" -", node_id)

    print_debug_summary()
        # ejemplo de prueba geográfica
    # coordenadas aproximadas de Bahía Blanca
    test_lat = -38.7196
    test_lon = -62.2724

    matching = get_matching_alerts_for_node_location(test_lat, test_lon)

    print(f"[INFO] Alertas que coinciden con Bahía Blanca: {len(matching)}")
    for alert in matching[:5]:
        print(
            "[MATCH]",
            alert["title"],
            "| onset=", alert["onset"],
            "| expires=", alert["expires"],
        )
