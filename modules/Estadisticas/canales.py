import sqlite3
import time
import logging

logger = logging.getLogger(__name__)

menu_name = "Canales"

DB_PATH = "/home/daniel/bbs/meshsentinel/traffic_stats.sqlite"

PORTNUM_SHORT = {
    "TEXT_MESSAGE_APP":  "TXT",
    "TELEMETRY_APP":     "TEL",
    "POSITION_APP":      "POS",
    "NEIGHBORINFO_APP":  "NBR",
    "TRACEROUTE_APP":    "TRC",
    "ENCRYPTED":         "ENC",
    "DECODED":           "DEC",
}


def display_menu():
    return (
        "=[ 📻 CANALES PRIVADOS ]=\n"
        "[1] 📊 Resumen canales\n"
        "[2] 📋 Actividad canal N\n"
        "[3] 👥 Top remitentes\n"
        "[4] ❓ Ayuda\n"
        "> opcion:\n"
        "'cd ..' volver"
    )


def _q(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def _mins_ago(ts):
    if ts is None:
        return "?"
    diff = int(time.time()) - ts
    if diff < 120:
        return "recién"
    if diff < 3600:
        return f"{diff // 60}min"
    return f"{diff // 3600}hs"


def _resumen_canales():
    since_24h = int(time.time()) - 86400
    rows = _q("""
        SELECT
            ca.channel_idx,
            COALESCE(cn.name, '') AS channel_name,
            COUNT(*)                                                        AS total,
            COUNT(DISTINCT ca.node_id)                                      AS nodos,
            SUM(CASE WHEN ca.ts >= ? THEN 1 ELSE 0 END)                    AS pkts_24h,
            SUM(CASE WHEN ca.portnum = 'ENCRYPTED' THEN 1 ELSE 0 END)      AS encriptados,
            MAX(ca.ts)                                                      AS last_ts
        FROM channel_activity ca
        LEFT JOIN channel_names cn ON cn.channel_idx = ca.channel_idx
        GROUP BY ca.channel_idx
        ORDER BY ca.channel_idx
    """, (since_24h,))
    if not rows:
        return "Sin actividad en canales privados aún.\n(Se registra cuando llega el primer paquete)"
    lines = ["📻 Canales privados\n──────────────────"]
    for r in rows:
        enc_flag = " 🔒" if r["encriptados"] == r["total"] else ""
        label = r["channel_name"] or f"#{r['channel_idx']}"
        lines.append(
            f"{label}: {r['total']}pkts "
            f"{r['nodos']}nodos "
            f"24h:{r['pkts_24h']}"
            f"{enc_flag} "
            f"últ:{_mins_ago(r['last_ts'])}"
        )
    return "\n".join(lines)


def _channel_activity(idx: int):
    rows = _q("""
        SELECT ca.ts, ca.portnum, ca.text_len,
               COALESCE(ns.short_name, ca.node_id) AS nombre
        FROM channel_activity ca
        LEFT JOIN node_stats ns ON ns.node_id = ca.node_id
        WHERE ca.channel_idx = ?
        ORDER BY ca.ts DESC
        LIMIT 15
    """, (idx,))
    if not rows:
        return f"Canal {idx}: sin actividad registrada."
    lines = [f"📋 Canal {idx} — últimos eventos\n──────────────────"]
    for r in rows:
        tipo = PORTNUM_SHORT.get(r["portnum"], r["portnum"] or "?")
        extra = f" {r['text_len']}b" if r["portnum"] == "TEXT_MESSAGE_APP" and r["text_len"] else ""
        lines.append(f"{_mins_ago(r['ts'])} {r['nombre']} [{tipo}{extra}]")
    return "\n".join(lines)


def _top_remitentes():
    since_7d = int(time.time()) - 7 * 86400
    rows = _q("""
        SELECT COALESCE(ns.short_name, ca.node_id)                               AS nombre,
               COUNT(*)                                                           AS pkts,
               COUNT(DISTINCT ca.channel_idx)                                    AS canales,
               SUM(CASE WHEN ca.portnum = 'TEXT_MESSAGE_APP' THEN 1 ELSE 0 END) AS textos,
               MAX(ca.ts)                                                         AS last_ts
        FROM channel_activity ca
        LEFT JOIN node_stats ns ON ns.node_id = ca.node_id
        WHERE ca.ts >= ?
        GROUP BY ca.node_id
        ORDER BY pkts DESC
        LIMIT 10
    """, (since_7d,))
    if not rows:
        return "Sin datos de los últimos 7 días."
    lines = ["👥 Top remitentes (7d)\n──────────────────"]
    for r in rows:
        lines.append(
            f"{r['nombre']}: {r['pkts']}pkts "
            f"{r['textos']}txt "
            f"ch:{r['canales']} "
            f"últ:{_mins_ago(r['last_ts'])}"
        )
    return "\n".join(lines)


def process_command(user_id, command, bbs):
    cmd = (command or "").strip().lower()
    user_state = bbs.users.get(user_id, {})

    # Paso 2 de selección de canal
    if user_state.get("_chan_waiting"):
        user_state.pop("_chan_waiting", None)
        try:
            idx = int(cmd)
            if 1 <= idx <= 7:
                return _channel_activity(idx)
        except (ValueError, TypeError):
            pass
        return "Canal inválido (1-7). Volvé a elegir [2] para intentar de nuevo."

    if cmd in ("", "menu"):
        return display_menu()

    if cmd == "1":
        return _resumen_canales()

    if cmd == "2":
        user_state["_chan_waiting"] = True
        return "Enviá el número del canal (1-7):"

    if cmd == "3":
        return _top_remitentes()

    if cmd in ("4", "help", "ayuda", "?"):
        return (
            "❓ Ayuda Canales\n"
            "──────────────────\n"
            "1: Resumen de todos los\n"
            "   canales privados\n"
            "2: Últimos 15 eventos de\n"
            "   un canal específico\n"
            "3: Top 10 nodos que más\n"
            "   transmiten (7 días)\n"
            "'cd ..' volver al menú"
        )

    return "Opción inválida. Enviá 'menu' para ver opciones."
