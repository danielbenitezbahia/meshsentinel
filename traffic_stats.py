import math
import sqlite3
import time
from typing import Optional

DB_PATH = "traffic_stats.sqlite"


def _now() -> int:
    return int(time.time())


def _minute(ts: int) -> int:
    return ts - (ts % 60)


def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Mejor concurrencia y performance para escritura frecuente
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")

    # Stats acumuladas por nodo (incluye nombres)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS node_stats (
      node_id TEXT PRIMARY KEY,
      short_name TEXT,
      long_name TEXT,

      first_seen_ts INTEGER NOT NULL,
      last_seen_ts INTEGER NOT NULL,

      packets_total INTEGER NOT NULL DEFAULT 0,
      text_packets INTEGER NOT NULL DEFAULT 0,
      dm_packets INTEGER NOT NULL DEFAULT 0,
      broadcast_packets INTEGER NOT NULL DEFAULT 0,

      bytes_text_total INTEGER NOT NULL DEFAULT 0,
      errors_total INTEGER NOT NULL DEFAULT 0
    )
    """)

    # Serie temporal agregada por minuto (rate/peaks)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS node_stats_minute (
      node_id TEXT NOT NULL,
      minute_ts INTEGER NOT NULL,

      packets_total INTEGER NOT NULL DEFAULT 0,
      text_packets INTEGER NOT NULL DEFAULT 0,
      dm_packets INTEGER NOT NULL DEFAULT 0,
      broadcast_packets INTEGER NOT NULL DEFAULT 0,
      bytes_text_total INTEGER NOT NULL DEFAULT 0,

      PRIMARY KEY (node_id, minute_ts)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS node_neighbors (
      reporter        TEXT    NOT NULL,
      neighbor        TEXT    NOT NULL,
      snr             REAL,
      snr_reverse     REAL,
      times_seen      INTEGER NOT NULL DEFAULT 1,
      first_seen_ts   INTEGER NOT NULL,
      last_updated_ts INTEGER NOT NULL,
      PRIMARY KEY (reporter, neighbor)
    )
    """)

    # Columnas opcionales en node_stats (migración segura)
    for col, typedef in [
        ("hops_from_bbs", "INTEGER"), ("snr_from_bbs", "REAL"),
        ("lat", "REAL"), ("lon", "REAL"),
        ("altitude", "INTEGER"), ("position_ts", "INTEGER"),
    ]:
        try:
            cur.execute(f"ALTER TABLE node_stats ADD COLUMN {col} {typedef}")
        except Exception:
            pass  # ya existe

    cur.execute("""
    CREATE TABLE IF NOT EXISTS device_metrics (
      node_id           TEXT    NOT NULL,
      ts                INTEGER NOT NULL,
      channel_util      REAL,
      air_util_tx       REAL,
      battery_level     INTEGER,
      voltage           REAL,
      uptime_seconds    INTEGER,
      PRIMARY KEY (node_id, ts)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS channel_activity (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      ts          INTEGER NOT NULL,
      channel_idx INTEGER NOT NULL DEFAULT 0,
      node_id     TEXT    NOT NULL,
      portnum     TEXT,
      text_len    INTEGER NOT NULL DEFAULT 0,
      channel_name TEXT
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_chanact_idx_ts ON channel_activity(channel_idx, ts DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_chanact_ts ON channel_activity(ts DESC)"
    )
    for col, typedef in [("channel_name", "TEXT"), ("is_encrypted", "INTEGER"), ("rx_snr", "REAL")]:
        try:
            cur.execute(f"ALTER TABLE channel_activity ADD COLUMN {col} {typedef}")
        except Exception:
            pass
    cur.execute("""
    CREATE TABLE IF NOT EXISTS channel_names (
      channel_idx INTEGER PRIMARY KEY,
      name        TEXT    NOT NULL,
      updated_ts  INTEGER NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS node_track (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      node_id    TEXT    NOT NULL,
      short_name TEXT,
      long_name  TEXT,
      lat        REAL    NOT NULL,
      lon        REAL    NOT NULL,
      altitude   INTEGER,
      speed      REAL,
      heading    INTEGER,
      ts         INTEGER NOT NULL
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_node_track_node_ts ON node_track(node_id, ts DESC)"
    )

    cur.execute("""
    CREATE TABLE IF NOT EXISTS traceroute_paths (
      id      INTEGER PRIMARY KEY AUTOINCREMENT,
      target  TEXT    NOT NULL,
      path    TEXT    NOT NULL,
      ts      INTEGER NOT NULL
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_tr_paths_target_ts ON traceroute_paths(target, ts DESC)"
    )

    for col, typedef in [("relay_node", "TEXT"), ("rx_snr", "REAL"), ("hop_count", "INTEGER")]:
        try:
            cur.execute(f"ALTER TABLE node_track ADD COLUMN {col} {typedef}")
        except Exception:
            pass  # ya existe

    con.commit()
    con.close()


def record_neighbors(reporter_id: str, neighbors: list):
    """
    Guarda la lista de vecinos reportada por un nodo via NEIGHBORINFO_APP.
    neighbors: [{"node_id": "!abc", "snr": 8.5}, ...]
    """
    ts = _now()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    for n in neighbors:
        nid = n.get("node_id") or n.get("nodeId")
        snr = n.get("snr")
        if not nid:
            continue
        cur.execute("""
        INSERT INTO node_neighbors (reporter, neighbor, snr, first_seen_ts, last_updated_ts, times_seen)
        VALUES (?, ?, ?, ?, ?, 1)
        ON CONFLICT(reporter, neighbor) DO UPDATE SET
            snr             = excluded.snr,
            last_updated_ts = excluded.last_updated_ts,
            times_seen      = node_neighbors.times_seen + 1
        """, (reporter_id, nid, snr, ts, ts))
        # Actualizar snr_reverse en la dirección opuesta si existe
        cur.execute("""
        UPDATE node_neighbors SET snr_reverse = ?
        WHERE reporter = ? AND neighbor = ?
        """, (snr, nid, reporter_id))
    con.commit()
    con.close()


def update_node_hop_info(node_id: str, hops_from_bbs: Optional[int], snr_from_bbs: Optional[float]):
    """Actualiza hops y SNR desde el BBS hacia un nodo (perspectiva del BBS)."""
    ts = _now()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
    INSERT INTO node_stats (node_id, first_seen_ts, last_seen_ts, hops_from_bbs, snr_from_bbs)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(node_id) DO UPDATE SET
        hops_from_bbs = excluded.hops_from_bbs,
        snr_from_bbs  = excluded.snr_from_bbs,
        last_seen_ts  = excluded.last_seen_ts
    """, (node_id, ts, ts, hops_from_bbs, snr_from_bbs))
    con.commit()
    con.close()


def record_device_metrics(
    node_id: str,
    channel_util: Optional[float],
    air_util_tx: Optional[float],
    battery_level: Optional[int],
    voltage: Optional[float],
    uptime_seconds: Optional[int],
):
    ts = _now()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
    INSERT OR REPLACE INTO device_metrics
      (node_id, ts, channel_util, air_util_tx, battery_level, voltage, uptime_seconds)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (node_id, ts, channel_util, air_util_tx, battery_level, voltage, uptime_seconds))
    con.commit()
    con.close()


def record_packet(
    sender_id: str,
    short_name: Optional[str],
    long_name: Optional[str],
    is_text: bool,
    is_dm: bool,
    is_broadcast: bool,
    text_len: int
):
    ts = _now()
    m = _minute(ts)

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Upsert stats acumuladas
    cur.execute("""
    INSERT INTO node_stats(
      node_id, short_name, long_name,
      first_seen_ts, last_seen_ts,
      packets_total, text_packets, dm_packets, broadcast_packets,
      bytes_text_total, errors_total
    )
    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 0)
    ON CONFLICT(node_id) DO UPDATE SET
      last_seen_ts=excluded.last_seen_ts,
      packets_total=node_stats.packets_total+1,
      text_packets=node_stats.text_packets+excluded.text_packets,
      dm_packets=node_stats.dm_packets+excluded.dm_packets,
      broadcast_packets=node_stats.broadcast_packets+excluded.broadcast_packets,
      bytes_text_total=node_stats.bytes_text_total+excluded.bytes_text_total,
      short_name=COALESCE(excluded.short_name, node_stats.short_name),
      long_name=COALESCE(excluded.long_name, node_stats.long_name)
    """, (
        sender_id,
        short_name,
        long_name,
        ts,
        ts,
        1 if is_text else 0,
        1 if is_dm else 0,
        1 if is_broadcast else 0,
        text_len if is_text else 0
    ))

    # Upsert por minuto
    cur.execute("""
    INSERT INTO node_stats_minute(
      node_id, minute_ts,
      packets_total, text_packets, dm_packets, broadcast_packets, bytes_text_total
    )
    VALUES (?, ?, 1, ?, ?, ?, ?)
    ON CONFLICT(node_id, minute_ts) DO UPDATE SET
      packets_total=node_stats_minute.packets_total+1,
      text_packets=node_stats_minute.text_packets+excluded.text_packets,
      dm_packets=node_stats_minute.dm_packets+excluded.dm_packets,
      broadcast_packets=node_stats_minute.broadcast_packets+excluded.broadcast_packets,
      bytes_text_total=node_stats_minute.bytes_text_total+excluded.bytes_text_total
    """, (
        sender_id,
        m,
        1 if is_text else 0,
        1 if is_dm else 0,
        1 if is_broadcast else 0,
        text_len if is_text else 0
    ))

    con.commit()
    con.close()


def record_channel_packet(channel_idx: int, node_id: str, portnum: str,
                          text_len: int = 0, channel_name: str = "",
                          is_encrypted: int = 0, rx_snr: Optional[float] = None):
    ts = _now()
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO channel_activity (ts, channel_idx, node_id, portnum, text_len, channel_name, is_encrypted, rx_snr)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (ts, channel_idx, node_id, portnum, text_len, channel_name or None, is_encrypted, rx_snr),
    )
    if channel_name:
        con.execute(
            "INSERT INTO channel_names (channel_idx, name, updated_ts) VALUES (?,?,?)"
            " ON CONFLICT(channel_idx) DO UPDATE SET name=excluded.name, updated_ts=excluded.updated_ts",
            (channel_idx, channel_name, ts),
        )
    con.commit()
    con.close()


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def record_track_point(
    node_id: str,
    short_name: Optional[str],
    long_name: Optional[str],
    lat: float,
    lon: float,
    altitude: Optional[int],
    speed: Optional[float],
    heading: Optional[int],
    relay_node: Optional[str] = None,
    rx_snr: Optional[float] = None,
    hop_count: Optional[int] = None,
    min_distance_m: float = 100.0,
) -> bool:
    """Insert a track point if the node moved ≥ min_distance_m from its last recorded point."""
    ts = _now()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "SELECT lat, lon FROM node_track WHERE node_id = ? ORDER BY ts DESC LIMIT 1",
        (node_id,)
    )
    row = cur.fetchone()
    inserted = False
    if row is None or _haversine_m(row[0], row[1], lat, lon) >= min_distance_m:
        cur.execute(
            "INSERT INTO node_track (node_id, short_name, long_name, lat, lon, altitude, speed, heading, relay_node, rx_snr, hop_count, ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (node_id, short_name, long_name, lat, lon, altitude, speed, heading, relay_node, rx_snr, hop_count, ts)
        )
        inserted = True
    con.commit()
    con.close()
    return inserted


def get_node_side_relay(node_id: str) -> Optional[str]:
    """Retorna el relay directamente adyacente al nodo (path[-2] del traceroute más reciente).
    Retorna None si no hay traceroute o si el nodo está conectado directo al BBS."""
    import json
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT path FROM traceroute_paths WHERE target = ? ORDER BY ts DESC LIMIT 1",
        (node_id,)
    ).fetchone()
    con.close()
    if not row:
        return None
    path = json.loads(row[0])
    # path = [BBS, relay1, ..., relayN, node_id]
    # Si len < 3 el nodo está directo al BBS, no hay relay intermedio
    if len(path) < 3:
        return None
    return path[-2]


def resolve_relay_node(relay_short_num: int) -> Optional[str]:
    """Resuelve el último byte del ID de un relay al node_id completo conocido."""
    if not relay_short_num:
        return None
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT node_id FROM node_stats").fetchall()
    con.close()
    matches = []
    for (nid,) in rows:
        try:
            if int(nid.lstrip("!"), 16) & 0xFF == relay_short_num:
                matches.append(nid)
        except Exception:
            pass
    return matches[0] if len(matches) == 1 else None


def backfill_relay_node(node_id: str, relay_node: str, since_ts: int):
    """Actualiza puntos recientes sin relay del nodo con el relay descubierto por traceroute."""
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "UPDATE node_track SET relay_node = ? WHERE node_id = ? AND relay_node IS NULL AND ts >= ?",
        (relay_node, node_id, since_ts)
    )
    con.commit()
    con.close()


def record_traceroute_path(target: str, path: list):
    import json
    ts = _now()
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO traceroute_paths (target, path, ts) VALUES (?, ?, ?)",
        (target, json.dumps(path), ts)
    )
    con.commit()
    con.close()


def cleanup_old_data(days: int = 7, track_days: int = 30):
    """Elimina registros de series temporales más viejos que `days` días.
    node_track usa `track_days` (por defecto 30).
    node_stats NO se toca (es un registro permanente de nodos)."""
    cutoff = _now() - days * 86400
    track_cutoff = _now() - track_days * 86400
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("DELETE FROM node_stats_minute WHERE minute_ts < ?", (cutoff,))
    cur.execute("DELETE FROM device_metrics WHERE ts < ?", (cutoff,))
    cur.execute("DELETE FROM channel_activity WHERE ts < ?", (cutoff,))
    cur.execute("DELETE FROM node_track WHERE ts < ?", (track_cutoff,))
    cur.execute("DELETE FROM traceroute_paths WHERE ts < ?", (cutoff,))
    deleted = cur.rowcount
    con.commit()
    con.close()
    return deleted


def is_new_node(node_id: str, threshold_days: int = 30) -> bool:
    """Returns True if the node has never been seen or was last seen more than threshold_days ago."""
    cutoff = _now() - threshold_days * 86400
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT last_seen_ts FROM node_stats WHERE node_id = ?", (node_id,))
    row = cur.fetchone()
    con.close()
    return row is None or row[0] < cutoff


def record_error(sender_id: str):
    ts = _now()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
    INSERT INTO node_stats(node_id, short_name, long_name, first_seen_ts, last_seen_ts, packets_total, errors_total)
    VALUES (?, NULL, NULL, ?, ?, 0, 1)
    ON CONFLICT(node_id) DO UPDATE SET
      last_seen_ts=excluded.last_seen_ts,
      errors_total=node_stats.errors_total+1
    """, (sender_id, ts, ts))
    con.commit()
    con.close()


def update_node_position(node_id: str, lat: float, lon: float, altitude: Optional[int]):
    ts = _now()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
    INSERT INTO node_stats (node_id, first_seen_ts, last_seen_ts, lat, lon, altitude, position_ts)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(node_id) DO UPDATE SET
        lat          = excluded.lat,
        lon          = excluded.lon,
        altitude     = excluded.altitude,
        position_ts  = excluded.position_ts,
        last_seen_ts = excluded.last_seen_ts
    """, (node_id, ts, ts, lat, lon, altitude, ts))
    con.commit()
    con.close()
