import sqlite3
import time
import logging
import json
import os

logger = logging.getLogger(__name__)

menu_name = "Estado de Red"

TRAFFIC_DB = "/home/daniel/bbs/meshsentinel/traffic_stats.sqlite"
_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "meshtastic_config.json")
WINDOW_MINUTES = 30
WINDOW_DM_HOURS = 2        # ventana para device_metrics
HISTORY_COUNT = 3
EXCLUDE_NODES = ("BBSD", "plgm")


def _get_api_key() -> str:
    try:
        with open(_CONFIG_FILE) as f:
            return json.load(f).get("anthropic_api_key", "")
    except Exception:
        return ""


def display_menu():
    return "🔍 Estado de Red (IA)\nEscribí cualquier cosa para ver el diagnóstico de la red."


def _init_history_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS diagnosis_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            ts        INTEGER NOT NULL,
            diagnosis TEXT    NOT NULL
        )
    """)


def _get_recent_diagnoses(cur) -> list:
    cur.execute("""
        SELECT ts, diagnosis FROM diagnosis_history
        ORDER BY ts DESC LIMIT ?
    """, (HISTORY_COUNT,))
    rows = cur.fetchall()
    rows.reverse()
    return [dict(r) for r in rows]


def _save_diagnosis(cur, diagnosis: str):
    cur.execute(
        "INSERT INTO diagnosis_history (ts, diagnosis) VALUES (?, ?)",
        (int(time.time()), diagnosis)
    )
    cur.execute("""
        DELETE FROM diagnosis_history
        WHERE id NOT IN (
            SELECT id FROM diagnosis_history ORDER BY ts DESC LIMIT 20
        )
    """)


def _placeholders(nodes):
    return ",".join("?" * len(nodes))


def _get_traffic_snapshot():
    since = int(time.time()) - WINDOW_MINUTES * 60
    ph = _placeholders(EXCLUDE_NODES)
    con = sqlite3.connect(TRAFFIC_DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    _init_history_table(cur)

    cur.execute(f"""
        SELECT ns.node_id,
               COALESCE(ns.short_name, ns.node_id) AS name,
               SUM(m.packets_total)     AS pkts,
               SUM(m.broadcast_packets) AS bc,
               SUM(m.dm_packets)        AS dm,
               ns.errors_total
        FROM node_stats_minute m
        JOIN node_stats ns USING(node_id)
        WHERE m.minute_ts >= ?
          AND COALESCE(ns.short_name, ns.node_id) NOT IN ({ph})
        GROUP BY ns.node_id
        ORDER BY pkts DESC
        LIMIT 15
    """, (since, *EXCLUDE_NODES))
    active_nodes = [dict(r) for r in cur.fetchall()]

    cur.execute(f"""
        SELECT node_id, COALESCE(short_name, node_id) AS name, errors_total
        FROM node_stats
        WHERE errors_total > 0
          AND COALESCE(short_name, node_id) NOT IN ({ph})
        ORDER BY errors_total DESC
        LIMIT 5
    """, EXCLUDE_NODES)
    error_nodes = [dict(r) for r in cur.fetchall()]

    history = _get_recent_diagnoses(cur)
    con.commit()
    con.close()
    return active_nodes, error_nodes, history


def _get_device_metrics_snapshot():
    """Channel util, air util, reinicios y correlación tráfico/canal (últimas 2hs)."""
    now = int(time.time())
    since_2h  = now - WINDOW_DM_HOURS * 3600
    since_1h  = now - 3600
    ph = _placeholders(EXCLUDE_NODES)

    con = sqlite3.connect(TRAFFIC_DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 1 & 5 — Channel util + air util: promedio y pico por nodo
    cur.execute(f"""
        SELECT dm.node_id,
               COALESCE(ns.short_name, dm.node_id) AS name,
               ROUND(AVG(dm.channel_util), 1)  AS avg_chutil,
               ROUND(MAX(dm.channel_util), 1)  AS peak_chutil,
               ROUND(AVG(dm.air_util_tx), 1)   AS avg_air,
               ROUND(MAX(dm.air_util_tx), 1)   AS peak_air
        FROM device_metrics dm
        LEFT JOIN node_stats ns USING(node_id)
        WHERE dm.ts >= ?
          AND dm.channel_util IS NOT NULL
          AND COALESCE(ns.short_name, dm.node_id) NOT IN ({ph})
        GROUP BY dm.node_id
        ORDER BY avg_chutil DESC
        LIMIT 10
    """, (since_2h, *EXCLUDE_NODES))
    util_nodes = [dict(r) for r in cur.fetchall()]

    # 2 — Tendencia: última hora vs hora anterior
    cur.execute(f"""
        SELECT dm.node_id,
               COALESCE(ns.short_name, dm.node_id) AS name,
               ROUND(AVG(CASE WHEN dm.ts >= ? THEN dm.channel_util END), 1) AS recent_util,
               ROUND(AVG(CASE WHEN dm.ts <  ? THEN dm.channel_util END), 1) AS prev_util
        FROM device_metrics dm
        LEFT JOIN node_stats ns USING(node_id)
        WHERE dm.ts >= ?
          AND dm.channel_util IS NOT NULL
          AND COALESCE(ns.short_name, dm.node_id) NOT IN ({ph})
        GROUP BY dm.node_id
        HAVING recent_util IS NOT NULL AND prev_util IS NOT NULL
    """, (since_1h, since_1h, since_2h, *EXCLUDE_NODES))
    trend_nodes = [dict(r) for r in cur.fetchall()]

    # 4 — Nodos que reiniciaron (uptime bajo detectado en las últimas 2hs)
    cur.execute(f"""
        SELECT dm.node_id,
               COALESCE(ns.short_name, dm.node_id) AS name,
               MIN(dm.uptime_seconds) AS min_uptime
        FROM device_metrics dm
        LEFT JOIN node_stats ns USING(node_id)
        WHERE dm.ts >= ?
          AND dm.uptime_seconds IS NOT NULL
          AND COALESCE(ns.short_name, dm.node_id) NOT IN ({ph})
        GROUP BY dm.node_id
        HAVING min_uptime < 3600
        ORDER BY min_uptime ASC
    """, (since_2h, *EXCLUDE_NODES))
    restarted_nodes = [dict(r) for r in cur.fetchall()]

    # 6 — Correlación: paquetes vs channel util
    cur.execute(f"""
        SELECT t.name,
               t.pkts,
               ROUND(d.avg_chutil, 1) AS avg_chutil
        FROM (
            SELECT COALESCE(ns.short_name, m.node_id) AS name,
                   SUM(m.packets_total) AS pkts
            FROM node_stats_minute m
            LEFT JOIN node_stats ns USING(node_id)
            WHERE m.minute_ts >= ?
              AND COALESCE(ns.short_name, m.node_id) NOT IN ({ph})
            GROUP BY m.node_id
        ) t
        JOIN (
            SELECT COALESCE(ns.short_name, dm.node_id) AS name,
                   AVG(dm.channel_util) AS avg_chutil
            FROM device_metrics dm
            LEFT JOIN node_stats ns USING(node_id)
            WHERE dm.ts >= ?
              AND dm.channel_util IS NOT NULL
              AND COALESCE(ns.short_name, dm.node_id) NOT IN ({ph})
            GROUP BY dm.node_id
        ) d USING(name)
        WHERE t.pkts > 0 AND d.avg_chutil > 0
        ORDER BY d.avg_chutil DESC
        LIMIT 8
    """, (since_2h, *EXCLUDE_NODES, since_2h, *EXCLUDE_NODES))
    correlation = [dict(r) for r in cur.fetchall()]

    con.close()
    return util_nodes, trend_nodes, restarted_nodes, correlation


def _get_mesh_snapshot(bbs_system):
    try:
        serial_iface = getattr(bbs_system.interface, "interface", None)
        nodes = getattr(serial_iface, "nodes", {}) or {}
    except Exception:
        return []

    now = int(time.time())
    result = []

    for node_id, info in nodes.items():
        if not isinstance(info, dict):
            continue
        user     = info.get("user", {}) or {}
        short    = user.get("shortName") or node_id
        snr      = info.get("snr")
        hops     = info.get("hopsAway")
        last_heard = info.get("lastHeard")
        mins_ago = round((now - last_heard) / 60) if last_heard else None
        result.append({"id": node_id, "name": short, "snr": snr,
                       "hops": hops, "mins_ago": mins_ago})

    result.sort(key=lambda x: x["mins_ago"] if x["mins_ago"] is not None else 9999)
    return result[:20]


def _build_prompt(active_nodes, error_nodes, mesh_nodes, history,
                  util_nodes, trend_nodes, restarted_nodes, correlation):

    active_txt = ""
    for n in active_nodes:
        active_txt += f"  {n['name']}: {n['pkts']} pkts, {n['bc']} bc, {n['dm']} dm, {n['errors_total']} err\n"

    mesh_txt = ""
    for n in mesh_nodes:
        snr  = f"{n['snr']} dB" if n['snr'] is not None else "?"
        hops = str(n['hops'])   if n['hops'] is not None else "?"
        mins = f"{n['mins_ago']}min" if n['mins_ago'] is not None else "?"
        mesh_txt += f"  {n['name']}: SNR {snr}, {hops} hops, visto hace {mins}\n"

    error_txt = ""
    for n in error_nodes:
        error_txt += f"  {n['name']}: {n['errors_total']} errores\n"

    util_txt = ""
    for n in util_nodes:
        util_txt += (f"  {n['name']}: chutil avg {n['avg_chutil']}% pico {n['peak_chutil']}%"
                     f", airTx avg {n['avg_air']}% pico {n['peak_air']}%\n")

    trend_txt = ""
    for n in trend_nodes:
        delta = round((n['recent_util'] or 0) - (n['prev_util'] or 0), 1)
        if abs(delta) >= 2:
            direccion = "▲" if delta > 0 else "▼"
            trend_txt += f"  {n['name']}: {direccion} {abs(delta)}% (era {n['prev_util']}%, ahora {n['recent_util']}%)\n"

    restart_txt = ""
    for n in restarted_nodes:
        mins = round(n['min_uptime'] / 60)
        restart_txt += f"  {n['name']}: uptime mín {mins}min (posible reinicio reciente)\n"

    corr_txt = ""
    for n in correlation:
        corr_txt += f"  {n['name']}: {n['pkts']} pkts, chutil {n['avg_chutil']}%\n"

    history_txt = ""
    if history:
        history_txt = "Diagnósticos anteriores (detectá tendencias):\n"
        for h in history:
            mins_ago = round((time.time() - h["ts"]) / 60)
            history_txt += f"  [hace {mins_ago}min] {h['diagnosis']}\n"

    prompt = f"""Sos un experto en redes Meshtastic de radioafición. Analizá estos datos de una red mesh en el SW de la provincia de Buenos Aires, Argentina.

Actividad últimos 30min por nodo:
{active_txt or '  Sin actividad reciente.'}

Estado de nodos (SNR, hops, último heard):
{mesh_txt or '  Sin datos.'}

Nodos con errores acumulados:
{error_txt or '  Sin errores.'}

Channel util + air utilization últimas 2hs:
{util_txt or '  Sin datos de telemetría.'}

Tendencia channel util (última hora vs anterior):
{trend_txt or '  Sin cambios significativos.'}

Nodos con posibles reinicios recientes:
{restart_txt or '  Ninguno.'}

Correlación paquetes vs channel util:
{corr_txt or '  Sin datos.'}

{history_txt}
Generá un diagnóstico breve y útil en español. Máximo 4 oraciones. Considerá:
- Actividad y señal general de la red
- Congestión del canal (channel util) y quién lo domina
- Tendencias de uso vs diagnósticos anteriores
- Nodos que reiniciaron o tienen problemas
- Una recomendación concreta si hay algo a corregir

Respondé SOLO con el diagnóstico, sin títulos. Máximo 350 caracteres."""

    return prompt


def _ask_claude(prompt: str) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=_get_api_key(), timeout=15.0)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=140,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        logger.exception("Error llamando a Claude API: %s", exc)
        return f"Error al consultar IA: {exc}"


def _run_diagnosis(bbs_system):
    active_nodes, error_nodes, history = _get_traffic_snapshot()
    mesh_nodes = _get_mesh_snapshot(bbs_system)
    util_nodes, trend_nodes, restarted_nodes, correlation = _get_device_metrics_snapshot()

    if not active_nodes and not mesh_nodes:
        return "Sin datos de red disponibles todavía."

    prompt = _build_prompt(active_nodes, error_nodes, mesh_nodes, history,
                           util_nodes, trend_nodes, restarted_nodes, correlation)
    diagnosis = _ask_claude(prompt)

    if not diagnosis.startswith("Error al consultar"):
        con = sqlite3.connect(TRAFFIC_DB)
        cur = con.cursor()
        _init_history_table(cur)
        _save_diagnosis(cur, diagnosis)
        con.commit()
        con.close()

    return f"🔍 Estado de Red\n{diagnosis}"


def process_command(user_id, command, bbs_system):
    return _run_diagnosis(bbs_system)
