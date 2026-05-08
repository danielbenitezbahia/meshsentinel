"""
SOMesh BBS REST API
Expone topología de la mesh, árbol de nodos y estadísticas.

Endpoints:
  GET /api/mesh/nodes                         — lista todos los nodos conocidos
  GET /api/mesh/neighbors/<node_id_or_name>   — vecinos directos de un nodo
  GET /api/mesh/tree/<node_id_or_name>        — árbol recursivo desde un nodo raíz
"""

import time
import sqlite3
from flask import Flask, jsonify, abort, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


DB_PATH = "/home/daniel/bbs/meshsentinel/traffic_stats.sqlite"
MAX_DEPTH = 8


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
        SELECT lat, lon, altitude, speed, heading, ts
        FROM node_track
        WHERE date(ts, 'unixepoch', 'localtime') = ?
          AND node_id = ?
        ORDER BY ts ASC
    """, (date, node_id))
    return jsonify({"points": rows})


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
