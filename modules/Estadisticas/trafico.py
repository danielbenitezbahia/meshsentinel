import sqlite3
import time
import logging

logger = logging.getLogger(__name__)

menu_name = "Tráfico"

DB_PATH = "/home/daniel/bbs/meshsentinel/traffic_stats.sqlite"
EXCLUDE_NODES = ("BBSD", "plgm")


def display_menu():
    return (
        "=[ 📈 TRÁFICO ]=\n"
        "[1] 🏆 Top nodos (7d)\n"
        "[2] 📢 Más broadcast\n"
        "[3] ⚡ Últimos 10 min\n"
        "[4] 📡 Channel util 24hs\n"
        "[5] 🔍 Detalle nodo\n"
        "[6] ❓ Ayuda\n"
        "[7] ↩ Volver\n"
        "> opcion:"
    )


def _q(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def _ex(nodes):
    return ",".join("?" * len(nodes))


def _top_nodos():
    ph = _ex(EXCLUDE_NODES)
    since = int(time.time()) - 7 * 86400
    rows = _q(f"""
        SELECT COALESCE(ns.short_name, ns.node_id) AS nodo,
               SUM(m.packets_total)     AS pkts,
               SUM(m.broadcast_packets) AS bc,
               SUM(m.dm_packets)        AS dm,
               ns.errors_total          AS err
        FROM node_stats_minute m
        JOIN node_stats ns USING(node_id)
        WHERE m.minute_ts >= ?
          AND COALESCE(ns.short_name, ns.node_id) NOT IN ({ph})
        GROUP BY ns.node_id
        ORDER BY pkts DESC
        LIMIT 10
    """, (since, *EXCLUDE_NODES))
    if not rows:
        return "Sin datos de los últimos 7 días."
    lines = ["🏆 Top nodos (7 días)\n──────────────────"]
    for r in rows:
        lines.append(f"{r['nodo']}: {r['pkts']}pkts bc:{r['bc']} dm:{r['dm']} err:{r['err']}")
    return "\n".join(lines)


def _top_broadcast():
    ph = _ex(EXCLUDE_NODES)
    since = int(time.time()) - 7 * 86400
    rows = _q(f"""
        SELECT COALESCE(ns.short_name, ns.node_id) AS nodo,
               SUM(m.broadcast_packets) AS bc,
               SUM(m.packets_total)     AS pkts,
               ROUND(100.0 * SUM(m.broadcast_packets) / NULLIF(SUM(m.packets_total), 0), 1) AS ratio
        FROM node_stats_minute m
        JOIN node_stats ns USING(node_id)
        WHERE m.minute_ts >= ?
          AND COALESCE(ns.short_name, ns.node_id) NOT IN ({ph})
        GROUP BY ns.node_id
        ORDER BY bc DESC
        LIMIT 10
    """, (since, *EXCLUDE_NODES))
    if not rows:
        return "Sin datos."
    lines = ["📢 Más broadcast (7 días)\n──────────────────"]
    for r in rows:
        lines.append(f"{r['nodo']}: {r['bc']}bc / {r['pkts']}pkts ({r['ratio']}%)")
    return "\n".join(lines)


def _rate_10min():
    ph = _ex(EXCLUDE_NODES)
    since = int(time.time()) - 600
    rows = _q(f"""
        SELECT COALESCE(ns.short_name, ns.node_id) AS nodo,
               SUM(m.packets_total) AS pkts,
               ROUND(SUM(m.packets_total) / 10.0, 2) AS por_min
        FROM node_stats_minute m
        JOIN node_stats ns USING(node_id)
        WHERE m.minute_ts >= ?
          AND COALESCE(ns.short_name, ns.node_id) NOT IN ({ph})
        GROUP BY ns.node_id
        ORDER BY pkts DESC
        LIMIT 10
    """, (since, *EXCLUDE_NODES))
    if not rows:
        return "Sin actividad en los últimos 10 min."
    lines = ["⚡ Actividad últimos 10 min\n──────────────────"]
    for r in rows:
        lines.append(f"{r['nodo']}: {r['pkts']} pkts ({r['por_min']}/min)")
    return "\n".join(lines)


def _channel_util_24h():
    ph = _ex(EXCLUDE_NODES)
    since = int(time.time()) - 86400
    rows = _q(f"""
        SELECT COALESCE(ns.short_name, dm.node_id) AS nodo,
               ROUND(AVG(dm.channel_util), 1)  AS avg_ch,
               ROUND(MAX(dm.channel_util), 1)  AS pico_ch,
               ROUND(AVG(dm.air_util_tx), 1)   AS avg_air,
               COUNT(*)                         AS muestras
        FROM device_metrics dm
        LEFT JOIN node_stats ns USING(node_id)
        WHERE dm.ts >= ?
          AND dm.channel_util IS NOT NULL
          AND COALESCE(ns.short_name, dm.node_id) NOT IN ({ph})
        GROUP BY dm.node_id
        ORDER BY avg_ch DESC
        LIMIT 10
    """, (since, *EXCLUDE_NODES))
    if not rows:
        return "Sin datos de telemetría en las últimas 24hs.\n(Los nodos envían device metrics cada ~30 min)"
    lines = ["📡 Channel util últimas 24hs\n──────────────────"]
    for r in rows:
        lines.append(
            f"{r['nodo']}: avg {r['avg_ch']}% pico {r['pico_ch']}%"
            f" airTx {r['avg_air']}% ({r['muestras']} muestras)"
        )
    return "\n".join(lines)


def _detalle_nodo(node_id: str):
    ph = _ex(EXCLUDE_NODES)
    since_7d = int(time.time()) - 7 * 86400
    since_24h = int(time.time()) - 86400

    # Info base
    base = _q("""
        SELECT node_id, COALESCE(short_name,'-') AS short,
               COALESCE(long_name,'-') AS nombre,
               packets_total, text_packets, dm_packets,
               broadcast_packets, bytes_text_total, errors_total,
               datetime(first_seen_ts,'unixepoch','localtime') AS first,
               datetime(last_seen_ts,'unixepoch','localtime') AS last
        FROM node_stats WHERE node_id = ?
    """, (node_id,))
    if not base:
        return f"Nodo {node_id} no encontrado."
    b = base[0]

    # Tráfico últimos 7d
    trafico = _q("""
        SELECT SUM(packets_total) AS pkts, SUM(broadcast_packets) AS bc,
               SUM(dm_packets) AS dm
        FROM node_stats_minute
        WHERE node_id = ? AND minute_ts >= ?
    """, (node_id, since_7d))
    t = trafico[0] if trafico else {}

    # Device metrics últimas 24hs
    dm = _q("""
        SELECT ROUND(AVG(channel_util),1) AS avg_ch,
               ROUND(MAX(channel_util),1) AS pico_ch,
               ROUND(AVG(air_util_tx),1)  AS avg_air,
               battery_level, voltage, uptime_seconds
        FROM device_metrics
        WHERE node_id = ? AND ts >= ?
        ORDER BY ts DESC LIMIT 1
    """, (node_id, since_24h))
    d = dm[0] if dm else {}

    lines = [
        f"🔍 {b['short']} ({b['nombre']})",
        f"ID: {b['node_id']}",
        f"──────────────────",
        f"Total: {b['packets_total']}pkts bc:{b['broadcast_packets']} dm:{b['dm_packets']}",
        f"Errores: {b['errors_total']} | Bytes: {b['bytes_text_total']}",
        f"Visto: {b['first']} → {b['last']}",
    ]
    if t.get('pkts'):
        lines.append(f"Últ.7d: {t['pkts']}pkts bc:{t['bc']} dm:{t['dm']}")
    if d.get('avg_ch') is not None:
        lines.append(f"ChUtil: avg {d['avg_ch']}% pico {d['pico_ch']}%")
        lines.append(f"AirTx: {d['avg_air']}%")
    if d.get('battery_level') is not None:
        lines.append(f"Batería: {d['battery_level']}% ({d['voltage']}V)")
    if d.get('uptime_seconds'):
        h = d['uptime_seconds'] // 3600
        lines.append(f"Uptime: {h}hs")

    return "\n".join(lines)


def process_command(user_id, command, bbs_system):
    cmd = (command or "").strip().lower()

    if cmd in ("", "menu"):
        return display_menu()

    if cmd == "1":
        return _top_nodos()

    if cmd == "2":
        return _top_broadcast()

    if cmd == "3":
        return _rate_10min()

    if cmd == "4":
        return _channel_util_24h()

    if cmd == "5":
        return "Enviá el ID del nodo, por ejemplo:\n!d9b19712"

    if cmd.startswith("!"):
        return _detalle_nodo(cmd)

    if cmd == "7":
        return "__back__"

    if cmd in ("6", "help", "ayuda", "?"):
        return (
            "❓ Ayuda Tráfico\n"
            "──────────────────\n"
            "1: Top nodos 7 días\n"
            "2: Ranking broadcast\n"
            "3: Actividad 10 min\n"
            "4: Channel util 24hs\n"
            "5: Detalle (!id)\n"
            "'cd ..' volver"
        )

    return "Opción inválida. Enviá 'menu' para ver opciones."
