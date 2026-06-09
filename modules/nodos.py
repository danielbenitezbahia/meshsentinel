# modules/nodos.py
import sqlite3
import time
import datetime

import traffic_stats

menu_name = "Nodos"

ONLINE_WINDOW_SECONDS = 30 * 60  # 30 minutos


def display_menu():
    return (
        "Nodos:\n"
        "1. Nodos conectados (últimos 30 min.)\n"
        "2. Nodos en movimiento hoy\n"
        "3. ↩ Volver\n"
    )


def _get_nodes_dict(bbs):
    serial_iface = getattr(getattr(bbs, "interface", None), "interface", None)
    nodes = getattr(serial_iface, "nodes", None) if serial_iface else None
    return nodes if isinstance(nodes, dict) else {}


def _get_node_names(info: dict):
    if not isinstance(info, dict):
        return ("", "")

    user = info.get("user", {}) if isinstance(info.get("user"), dict) else {}

    short_name = (
        user.get("shortName")
        or info.get("shortName")
        or user.get("short_name")
        or info.get("short_name")
        or ""
    )

    long_name = (
        user.get("longName")
        or info.get("longName")
        or user.get("long_name")
        or info.get("long_name")
        or ""
    )

    return (str(short_name).strip(), str(long_name).strip())


def _get_last_heard(info: dict):
    if not isinstance(info, dict):
        return None
    last = (
        info.get("lastHeard")
        or info.get("lastHeardTimestamp")
        or info.get("last_heard")
    )
    try:
        return float(last)
    except Exception:
        return None


def _get_moving_nodes_today():
    now = datetime.datetime.now()
    today_start = int(datetime.datetime(now.year, now.month, now.day).timestamp())
    try:
        con = sqlite3.connect(traffic_stats.DB_PATH)
        cur = con.cursor()
        cur.execute("""
            SELECT node_id, long_name, short_name, COUNT(*) AS pts, MAX(ts) AS last_ts
            FROM node_track
            WHERE ts >= ?
            GROUP BY node_id
            HAVING pts >= 2
            ORDER BY last_ts DESC
        """, (today_start,))
        rows = cur.fetchall()
        con.close()
        return rows
    except Exception:
        return []


def process_command(user_id, command, bbs):
    cmd = (command or "").strip().lower()

    if cmd in ["menu", "m", "help", "h", "?"]:
        return display_menu()

    if cmd == "3":
        return "__back__"

    if cmd == "2":
        rows = _get_moving_nodes_today()
        if not rows:
            return "No hay nodos en movimiento hoy.\n\n" + display_menu()
        lines = ["Nodos en movimiento hoy:"]
        for i, (node_id, long_name, short_name, pts, last_ts) in enumerate(rows, start=1):
            nombre = long_name or short_name or node_id
            t = datetime.datetime.fromtimestamp(last_ts).strftime("%H:%M")
            lines.append(f"{i}. {nombre} ({pts} pts, últ. {t})")
        lines += ["", "Tip: 'cd ..' para volver."]
        return "\n".join(lines)

    if cmd != "1":
        return "Opción inválida.\n\n" + display_menu()

    nodes = _get_nodes_dict(bbs)
    if not nodes:
        return "No hay nodos conocidos en este momento.\n\n" + display_menu()

    now = int(time.time())

    online = []
    for node_id, info in nodes.items():
        last = _get_last_heard(info)
        if last is None:
            continue
        age = now - int(last)
        if age <= ONLINE_WINDOW_SECONDS:
            short_name, long_name = _get_node_names(info)
            online.append((age, node_id, short_name, long_name))

    if not online:
        return (
            f"No hay nodos conectados en los últimos {ONLINE_WINDOW_SECONDS//60} minutos.\n\n"
            + display_menu()
        )

    # Orden: más recientes primero (age menor)
    online.sort(key=lambda x: x[0])

    lines = []
    lines.append(f"Nodos conectados (últimos {ONLINE_WINDOW_SECONDS//60} min):")

    MAX_NODES = 30  # para no explotar el payload
    for i, (age, node_id, short_name, long_name) in enumerate(online[:MAX_NODES], start=1):
        age_min = max(0, age // 60)
        # Formato compacto y estable
        lines.append(f"{i}. {node_id} | {short_name or '-'} | {long_name or '-'} | hace {age_min}m")

    if len(online) > MAX_NODES:
        lines.append(f"... ({len(online) - MAX_NODES} más, truncado)")

    lines.append("")
    lines.append("Tip: 'cd ..' para volver.")
    return "\n".join(lines)