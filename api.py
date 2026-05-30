"""
SOMesh BBS REST API
Expone topología de la mesh, árbol de nodos y estadísticas.

Endpoints:
  GET /api/mesh/nodes                         — lista todos los nodos conocidos
  GET /api/mesh/neighbors/<node_id_or_name>   — vecinos directos de un nodo
  GET /api/mesh/tree/<node_id_or_name>        — árbol recursivo desde un nodo raíz
"""

import datetime
import json
import math
import time
import sqlite3
from flask import Flask, jsonify, abort, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


DB_PATH = "/home/daniel/bbs/meshsentinel/traffic_stats.sqlite"
MAX_DEPTH = 8
BBS_LAT  = -38.719946
BBS_LON  = -62.255695


def _migrate():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    for col, typedef in [
        ("hops_from_bbs", "INTEGER"), ("snr_from_bbs", "REAL"),
        ("lat", "REAL"), ("lon", "REAL"),
        ("altitude", "INTEGER"), ("position_ts", "INTEGER"),
    ]:
        try:
            cur.execute(f"ALTER TABLE node_stats ADD COLUMN {col} {typedef}")
        except Exception:
            pass
    # Tabla de actividad por canal (creada si no existe, compatible con DBs viejas)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS channel_activity (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      ts          INTEGER NOT NULL,
      channel_idx INTEGER NOT NULL DEFAULT 0,
      node_id     TEXT    NOT NULL,
      portnum     TEXT,
      text_len    INTEGER NOT NULL DEFAULT 0
    )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_chanact_idx_ts ON channel_activity(channel_idx, ts DESC)"
    )
    con.commit()
    con.close()

_migrate()


# ─── helpers ────────────────────────────────────────────────────────────────

def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _con():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _q(sql, params=()):
    con = _con()
    cur = con.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def _resolve(node_id_or_name: str) -> str | None:
    """Resuelve nombre corto, nombre largo o ID a node_id."""
    for col in ("node_id", "short_name", "long_name"):
        rows = _q(f"SELECT node_id FROM node_stats WHERE LOWER({col}) = LOWER(?)",
                  (node_id_or_name,))
        if rows:
            return rows[0]["node_id"]
    return None


def _node_info(node_id: str) -> dict:
    now = int(time.time())
    since_24h = now - 86400

    base = _q("""
        SELECT node_id, short_name, long_name,
               last_seen_ts, hops_from_bbs, snr_from_bbs,
               packets_total, errors_total
        FROM node_stats WHERE node_id = ?
    """, (node_id,))

    if not base:
        return {"node_id": node_id}

    b = dict(base[0])
    b["last_seen_mins_ago"] = (
        round((now - b["last_seen_ts"]) / 60) if b.get("last_seen_ts") else None
    )

    dm = _q("""
        SELECT ROUND(AVG(channel_util), 1) AS avg_chutil,
               battery_level, voltage
        FROM device_metrics
        WHERE node_id = ? AND ts >= ?
        ORDER BY ts DESC LIMIT 1
    """, (node_id, since_24h))

    if dm and dm[0].get("avg_chutil") is not None:
        b["channel_util_avg_24h"] = dm[0]["avg_chutil"]
    if dm and dm[0].get("battery_level") is not None:
        b["battery_level"]  = dm[0]["battery_level"]
        b["voltage"]        = dm[0]["voltage"]

    return b


def _direct_neighbors(node_id: str) -> list:
    return _q("""
        SELECT neighbor, snr, snr_reverse, times_seen, last_updated_ts
        FROM node_neighbors
        WHERE reporter = ?
        ORDER BY snr DESC NULLS LAST
    """, (node_id,))


def _build_tree(node_id: str, visited: set, depth: int = 0,
                link: dict = None) -> dict:
    now = int(time.time())
    node = _node_info(node_id)
    visited.add(node_id)

    if link:
        node["snr_to_parent"]   = link.get("snr")
        node["snr_from_parent"] = link.get("snr_reverse")
        node["link_stability"]  = link.get("times_seen")
        node["link_age_mins"]   = (
            round((now - link["last_updated_ts"]) / 60)
            if link.get("last_updated_ts") else None
        )

    if depth >= MAX_DEPTH:
        node["children"]   = []
        node["_truncated"] = True
        return node

    neighbors = _direct_neighbors(node_id)
    children  = []
    for n in neighbors:
        nid = n["neighbor"]
        if nid in visited:
            continue
        children.append(_build_tree(nid, visited, depth + 1, link=n))

    node["children"] = children
    return node


def _freshness() -> dict:
    now  = int(time.time())
    rows = _q("""
        SELECT MIN(last_updated_ts) AS oldest,
               MAX(last_updated_ts) AS newest,
               COUNT(*)             AS total
        FROM node_neighbors
    """)

    if not rows or not rows[0]["total"]:
        return {
            "total_links": 0,
            "warning": "Sin datos de neighborinfo aún. Esperá ~15 min tras reiniciar.",
        }

    r = rows[0]
    oldest_mins = round((now - r["oldest"]) / 60) if r["oldest"] else None
    newest_mins = round((now - r["newest"]) / 60) if r["newest"] else None

    result = {
        "total_links":      r["total"],
        "oldest_link_mins": oldest_mins,
        "newest_link_mins": newest_mins,
    }
    if oldest_mins and oldest_mins > 30:
        result["warning"] = (
            f"Algunos links tienen {oldest_mins} min — pueden estar desactualizados"
        )
    return result


# ─── endpoints ──────────────────────────────────────────────────────────────

@app.get("/api/mesh/graph")
def get_graph():
    now = int(time.time())
    since = now - 7200  # solo nodos vistos en las últimas 2 horas
    nodes = _q("""
        SELECT ns.node_id, ns.short_name, ns.long_name,
               ns.last_seen_ts, ns.hops_from_bbs, ns.snr_from_bbs,
               ns.packets_total, ns.errors_total,
               ns.lat, ns.lon, ns.altitude, ns.position_ts,
               (SELECT COUNT(*) FROM node_neighbors WHERE reporter = ns.node_id) AS neighbor_count
        FROM node_stats ns
        WHERE ns.last_seen_ts >= ?
        ORDER BY ns.last_seen_ts DESC
    """, (since,))
    for n in nodes:
        n["last_seen_mins_ago"] = (
            round((now - n["last_seen_ts"]) / 60) if n.get("last_seen_ts") else None
        )

    edges = _q("""
        SELECT reporter, neighbor, snr, snr_reverse, times_seen, last_updated_ts
        FROM node_neighbors
        ORDER BY times_seen DESC
    """)
    for e in edges:
        e["age_mins"] = (
            round((now - e["last_updated_ts"]) / 60) if e.get("last_updated_ts") else None
        )

    return jsonify({
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "graph_freshness": _freshness(),
    })


@app.get("/api/mesh/nodes")
def get_nodes():
    now   = int(time.time())
    rows  = _q("""
        SELECT ns.node_id, ns.short_name, ns.long_name,
               ns.last_seen_ts, ns.hops_from_bbs, ns.snr_from_bbs,
               ns.packets_total, ns.errors_total,
               (SELECT COUNT(*) FROM node_neighbors WHERE reporter = ns.node_id) AS neighbor_count
        FROM node_stats ns
        ORDER BY ns.last_seen_ts DESC
    """)
    for r in rows:
        r["last_seen_mins_ago"] = (
            round((now - r["last_seen_ts"]) / 60) if r.get("last_seen_ts") else None
        )
    return jsonify({"nodes": rows, "count": len(rows)})


@app.get("/api/mesh/neighbors/<path:node_id_or_name>")
def get_neighbors(node_id_or_name):
    node_id = _resolve(node_id_or_name)
    if not node_id:
        abort(404, description=f"Nodo '{node_id_or_name}' no encontrado")

    now       = int(time.time())
    neighbors = _direct_neighbors(node_id)
    result    = []

    for n in neighbors:
        info = _node_info(n["neighbor"])
        info["snr"]            = n["snr"]
        info["snr_reverse"]    = n["snr_reverse"]
        info["link_stability"] = n["times_seen"]
        info["link_age_mins"]  = (
            round((now - n["last_updated_ts"]) / 60)
            if n.get("last_updated_ts") else None
        )
        result.append(info)

    return jsonify({
        "node":      node_id,
        "neighbors": result,
        "count":     len(result),
    })


@app.get("/api/mesh/tree/<path:node_id_or_name>")
def get_tree(node_id_or_name):
    node_id = _resolve(node_id_or_name)
    if not node_id:
        abort(404, description=f"Nodo '{node_id_or_name}' no encontrado")

    visited   = set()
    tree_data = _build_tree(node_id, visited)

    return jsonify({
        "root":             node_id,
        "graph_freshness":  _freshness(),
        "tree":             tree_data,
    })


@app.get("/api/channels")
def get_channels():
    now = int(time.time())
    since_24h = now - 86400
    channels = _q("""
        SELECT
            ca.channel_idx,
            COUNT(*)                                                        AS total_packets,
            COUNT(DISTINCT ca.node_id)                                      AS unique_nodes,
            MAX(ca.ts)                                                      AS last_ts,
            SUM(CASE WHEN ca.ts >= ? THEN 1 ELSE 0 END)                    AS packets_24h,
            SUM(CASE WHEN ca.portnum = 'TEXT_MESSAGE_APP' THEN 1 ELSE 0 END) AS text_packets,
            SUM(CASE WHEN ca.portnum = 'TEXT_MESSAGE_APP'
                      AND ca.ts >= ? THEN 1 ELSE 0 END)                    AS texts_24h
        FROM channel_activity ca
        GROUP BY ca.channel_idx
        ORDER BY ca.channel_idx
    """, (since_24h, since_24h))
    for c in channels:
        c["last_mins_ago"] = (
            round((now - c["last_ts"]) / 60) if c.get("last_ts") else None
        )
    return jsonify({"channels": channels, "count": len(channels)})


@app.get("/api/channels/<int:channel_idx>")
def get_channel_detail(channel_idx):
    now = int(time.time())
    activity = _q("""
        SELECT ca.ts, ca.node_id, ca.portnum, ca.text_len,
               ns.short_name, ns.long_name
        FROM channel_activity ca
        LEFT JOIN node_stats ns ON ns.node_id = ca.node_id
        WHERE ca.channel_idx = ?
        ORDER BY ca.ts DESC
        LIMIT 200
    """, (channel_idx,))
    for a in activity:
        a["mins_ago"] = round((now - a["ts"]) / 60) if a.get("ts") else None

    top_senders = _q("""
        SELECT ca.node_id, ns.short_name, ns.long_name,
               COUNT(*) AS packets, MAX(ca.ts) AS last_ts,
               SUM(CASE WHEN ca.portnum = 'TEXT_MESSAGE_APP' THEN 1 ELSE 0 END) AS text_packets
        FROM channel_activity ca
        LEFT JOIN node_stats ns ON ns.node_id = ca.node_id
        WHERE ca.channel_idx = ?
        GROUP BY ca.node_id
        ORDER BY packets DESC
        LIMIT 20
    """, (channel_idx,))
    for s in top_senders:
        s["last_mins_ago"] = (
            round((now - s["last_ts"]) / 60) if s.get("last_ts") else None
        )

    return jsonify({
        "channel_idx": channel_idx,
        "activity":    activity,
        "top_senders": top_senders,
    })


@app.get("/api/tracks/dates")
def get_track_dates():
    rows = _q("""
        SELECT DISTINCT date(ts, 'unixepoch', 'localtime') AS d
        FROM node_track
        ORDER BY d DESC
    """)
    return jsonify({"dates": [r["d"] for r in rows]})


@app.get("/api/tracks/<date>")
def get_track_nodes(date):
    rows = _q("""
        SELECT node_id, long_name, short_name,
               COUNT(*) AS pts, MIN(ts) AS first_ts, MAX(ts) AS last_ts
        FROM node_track
        WHERE date(ts, 'unixepoch', 'localtime') = ?
        GROUP BY node_id
        HAVING pts >= 2
        ORDER BY last_ts DESC
    """, (date,))
    return jsonify({"nodes": rows})


@app.get("/api/tracks/<date>/<path:node_id>")
def get_track_points(date, node_id):
    rows = _q("""
        SELECT nt.lat, nt.lon, nt.altitude, nt.speed, nt.heading, nt.ts,
               nt.relay_node, nt.rx_snr, nt.hop_count,
               ns.short_name AS relay_short_name,
               ns.long_name  AS relay_long_name,
               ns.lat AS relay_lat,
               ns.lon AS relay_lon
        FROM node_track nt
        LEFT JOIN node_stats ns ON ns.node_id = nt.relay_node
        WHERE date(nt.ts, 'unixepoch', 'localtime') = ?
          AND nt.node_id = ?
        ORDER BY nt.ts ASC
    """, (date, node_id))
    for row in rows:
        rlat, rlon = row.pop("relay_lat", None), row.pop("relay_lon", None)
        if rlat is not None and rlon is not None:
            row["relay_distance_m"] = round(_haversine_m(row["lat"], row["lon"], rlat, rlon))
        else:
            row["relay_distance_m"] = None
    return jsonify({"points": rows})


@app.get("/api/tracks/path/<path:node_id>")
def get_track_path(node_id):
    rows = _q(
        "SELECT path, ts FROM traceroute_paths WHERE target = ? ORDER BY ts DESC LIMIT 1",
        (node_id,)
    )
    if not rows:
        return jsonify({"path": None, "ts": None})
    raw_path = json.loads(rows[0]["path"])
    enriched = []
    for nid in raw_path:
        info = _q("SELECT short_name, long_name FROM node_stats WHERE node_id = ?", (nid,))
        enriched.append({
            "node_id": nid,
            "short_name": info[0]["short_name"] if info else None,
            "long_name": info[0]["long_name"] if info else None,
        })
    return jsonify({"path": enriched, "ts": rows[0]["ts"]})


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _fit_snr_distance_model():
    """Ajusta SNR = a + b*log10(dist_km) con nodos conocidos. Retorna (a, b, n, r2)."""
    rows = _q("""
        SELECT lat, lon, snr_from_bbs
        FROM node_stats
        WHERE lat IS NOT NULL AND lon IS NOT NULL AND snr_from_bbs IS NOT NULL
    """)
    points = []
    for r in rows:
        d = _haversine_km(BBS_LAT, BBS_LON, r["lat"], r["lon"])
        if d >= 0.05:  # ignorar nodos a menos de 50 m (mismo lugar que el BBS)
            points.append((math.log10(d), r["snr_from_bbs"]))

    n = len(points)
    if n < 3:
        return None, None, n, None

    x_vals = [p[0] for p in points]
    y_vals = [p[1] for p in points]
    x_mean = sum(x_vals) / n
    y_mean = sum(y_vals) / n

    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals))
    den = sum((x - x_mean) ** 2 for x in x_vals)
    if den == 0:
        return None, None, n, None

    b = num / den
    a = y_mean - b * x_mean

    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(x_vals, y_vals))
    ss_tot = sum((y - y_mean) ** 2 for y in y_vals)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return a, b, n, r2


@app.get("/api/stats/distance/<path:node_id>")
def estimate_node_distance(node_id):
    snr_row = _q("""
        SELECT AVG(rx_snr) AS avg_snr, COUNT(*) AS samples
        FROM channel_activity
        WHERE node_id = ? AND rx_snr IS NOT NULL
    """, (node_id,))

    if not snr_row or snr_row[0]["avg_snr"] is None:
        return jsonify({"error": "Sin datos de SNR para este nodo. Esperá que mande paquetes nuevos."}), 404

    avg_snr = snr_row[0]["avg_snr"]
    samples  = snr_row[0]["samples"]

    a, b, ref_nodes, r2 = _fit_snr_distance_model()
    if a is None:
        return jsonify({"error": "No hay suficientes nodos de referencia para calibrar el modelo."}), 500
    if abs(b) < 0.01 or r2 < 0.2:
        return jsonify({
            "error": f"El modelo no tiene correlación suficiente para esta red (r²={round(r2, 3)}). "
                     "El SNR en LoRa depende más de obstáculos y condiciones que de la distancia."
        }), 422

    log_d = (avg_snr - a) / b
    dist_km = 10 ** log_d

    return jsonify({
        "node_id":                node_id,
        "estimated_distance_km":  round(dist_km, 2),
        "estimated_distance_m":   round(dist_km * 1000),
        "avg_snr":                round(avg_snr, 2),
        "snr_samples":            samples,
        "model": {
            "reference_nodes": ref_nodes,
            "r2":              round(r2, 3),
            "a":               round(a, 3),
            "b":               round(b, 3),
            "note":            "r2 > 0.5 indica modelo confiable; r2 < 0.3 indica alta variabilidad"
        }
    })


KNOWN_PORTNUMS = {
    "TEXT_MESSAGE_APP", "POSITION_APP", "NODEINFO_APP",
    "TELEMETRY_APP", "TRACEROUTE_APP", "NEIGHBORINFO_APP",
}

def _stats_since(period: str) -> int:
    if period == "day":
        today = datetime.date.today()
        return int(datetime.datetime.combine(today, datetime.time.min).timestamp())
    elif period == "week":
        return int(time.time()) - 7 * 86400
    elif period == "month":
        return int(time.time()) - 30 * 86400
    return abort(400)

@app.get("/api/stats/traffic")
def get_traffic_stats():
    period = request.args.get("period", "day")
    since  = _stats_since(period)

    rows = _q("""
        SELECT channel_idx,
               COALESCE(is_encrypted, 0) AS is_encrypted,
               COALESCE(portnum, 'UNKNOWN') AS portnum,
               COUNT(*) AS cnt
        FROM channel_activity
        WHERE ts >= ?
        GROUP BY channel_idx, is_encrypted, portnum
    """, (since,))

    public     = {}   # portnum → count
    priv_known = {}   # portnum → count
    priv_enc   = 0

    for row in rows:
        cnt = row["cnt"]
        if row["channel_idx"] == 0:
            key = row["portnum"] if row["portnum"] in KNOWN_PORTNUMS else "OTHER"
            public[key] = public.get(key, 0) + cnt
        elif row["is_encrypted"]:
            priv_enc += cnt
        else:
            key = row["portnum"] if row["portnum"] in KNOWN_PORTNUMS else "OTHER"
            priv_known[key] = priv_known.get(key, 0) + cnt

    public_total      = sum(public.values())
    priv_known_total  = sum(priv_known.values())
    total             = public_total + priv_known_total + priv_enc

    def pct(n, base=None):
        base = base if base is not None else total
        return round(n / base * 100, 1) if base else 0.0

    return jsonify({
        "period":   period,
        "since_ts": since,
        "total":    total,
        "public": {
            "total":        public_total,
            "pct_of_total": pct(public_total),
            "by_type": {
                k: {"count": v, "pct": pct(v, public_total)}
                for k, v in sorted(public.items(), key=lambda x: -x[1])
            },
        },
        "other_mesh": {
            "total":        priv_known_total,
            "pct_of_total": pct(priv_known_total),
            "by_type": {
                k: {"count": v, "pct": pct(v, priv_known_total)}
                for k, v in sorted(priv_known.items(), key=lambda x: -x[1])
            },
            "by_channel": [{"name": r["name"], "count": r["cnt"]} for r in _q("""
                SELECT COALESCE(channel_name, 'Canal ' || channel_idx) AS name, COUNT(*) AS cnt
                FROM channel_activity
                WHERE ts >= ? AND channel_idx > 0 AND COALESCE(is_encrypted, 0) = 0
                GROUP BY name ORDER BY cnt DESC
            """, (since,))],
        },
        "private_encrypted": {
            "total":        priv_enc,
            "pct_of_total": pct(priv_enc),
        },
    })


@app.get("/api/stats/traffic/evolution")
def get_traffic_evolution():
    period = request.args.get("period", "week")
    since  = _stats_since(period)

    label_expr = (
        "strftime('%H:00', ts, 'unixepoch', 'localtime')"
        if period == "day"
        else "date(ts, 'unixepoch', 'localtime')"
    )

    rows = _q(f"""
        SELECT {label_expr} AS label,
               channel_idx,
               COALESCE(is_encrypted, 0) AS is_encrypted,
               COUNT(*) AS cnt
        FROM channel_activity
        WHERE ts >= ?
        GROUP BY label, channel_idx, is_encrypted
        ORDER BY label ASC
    """, (since,))

    by_label: dict = {}
    for row in rows:
        lbl = row["label"]
        if lbl not in by_label:
            by_label[lbl] = {"label": lbl, "public": 0, "other_mesh": 0, "private_encrypted": 0}
        if row["channel_idx"] == 0:
            by_label[lbl]["public"] += row["cnt"]
        elif row["is_encrypted"]:
            by_label[lbl]["private_encrypted"] += row["cnt"]
        else:
            by_label[lbl]["other_mesh"] += row["cnt"]

    return jsonify({"points": list(by_label.values())})


@app.get("/api/stats/nodes")
def get_stats_nodes():
    period = request.args.get("period", "day")
    limit  = min(int(request.args.get("limit", 10)), 50)
    since  = _stats_since(period)

    def top_nodes(extra_where: str):
        return _q(f"""
            SELECT ca.node_id,
                   COALESCE(ns.long_name, ns.short_name, ca.node_id) AS name,
                   COUNT(*) AS count
            FROM channel_activity ca
            LEFT JOIN node_stats ns ON ns.node_id = ca.node_id
            WHERE ca.ts >= ? {extra_where}
            GROUP BY ca.node_id
            ORDER BY count DESC
            LIMIT ?
        """, (since, limit))

    return jsonify({
        "public":            top_nodes("AND ca.channel_idx = 0"),
        "other_mesh":        top_nodes("AND ca.channel_idx > 0 AND COALESCE(ca.is_encrypted,0) = 0"),
        "private_encrypted": top_nodes("AND ca.channel_idx > 0 AND COALESCE(ca.is_encrypted,0) = 1"),
    })


FRONTEND_DIST = "/home/daniel/bbs/meshsentinel/frontend/dist"

@app.get("/")
def serve_index():
    return send_from_directory(FRONTEND_DIST, "index.html")

@app.get("/assets/<path:path>")
def serve_assets(path):
    return send_from_directory(f"{FRONTEND_DIST}/assets", path)


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": str(e)}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
