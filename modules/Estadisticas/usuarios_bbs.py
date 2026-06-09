import logging
import time
import bbs_users

logger = logging.getLogger(__name__)

menu_name = "Usuarios BBS"


def display_menu():
    return (
        "Usuarios del BBS\n"
        "1. who  (últimos 12)\n"
        "2. top  (más visitas)\n"
        "3. seen (te pide !id)\n"
        "4. ↩ Volver\n"
        "\n"
        "También podés escribir:\n"
        "- who [N]\n"
        "- top [N]\n"
        "- seen !id\n"
    )


def _fmt_rows(headers, rows, max_rows=12):
    if not rows:
        return "Sin datos todavía."
    out = []
    out.append(" | ".join(headers))
    out.append("-" * 60)
    for i, r in enumerate(rows):
        if i >= max_rows:
            out.append(f"... mostrando {max_rows} de {len(rows)}")
            break
        out.append(" | ".join(str(x) for x in r))
    return "\n".join(out)


def _ts(ts: int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except Exception:
        return str(ts)


def process_command(user_id, command, bbs_system):
    cmd = (command or "").strip().lower()

    if cmd in ("", "menu", "help", "ayuda", "?"):
        return display_menu()

    if cmd == "4":
        return "__back__"

    # Accesos por número (más cómodo desde Android)
    if cmd == "1":
        rows = bbs_users.last_visits(limit=12)
        rows2 = [
            (node_id, short, visits, _ts(last_ts))
            for (node_id, short, _name, visits, _first_ts, last_ts, _last_msg) in rows
        ]
        return _fmt_rows(["id", "short", "visits", "last_seen"], rows2, max_rows=12)

    if cmd == "2":
        rows = bbs_users.top_visits(limit=12)
        rows2 = [
            (node_id, short, visits, _ts(last_ts))
            for (node_id, short, _name, visits, _first_ts, last_ts) in rows
        ]
        return _fmt_rows(["id", "short", "visits", "last_seen"], rows2, max_rows=12)

    if cmd == "3":
        return "Pegá el ID del nodo. Ej: seen !d9b19712"

    # Comandos tipo texto
    parts = cmd.split()
    head = parts[0].lower()

    if head == "who":
        n = 12
        if len(parts) >= 2 and parts[1].isdigit():
            n = max(1, min(50, int(parts[1])))
        rows = bbs_users.last_visits(limit=n)
        rows2 = [(node_id, short, visits, _ts(last_ts)) for (node_id, short, _name, visits, _first_ts, last_ts, _lm) in rows]
        return _fmt_rows(["id", "short", "visits", "last_seen"], rows2, max_rows=n)

    if head == "top":
        n = 12
        if len(parts) >= 2 and parts[1].isdigit():
            n = max(1, min(50, int(parts[1])))
        rows = bbs_users.top_visits(limit=n)
        rows2 = [(node_id, short, visits, _ts(last_ts)) for (node_id, short, _name, visits, _first_ts, last_ts) in rows]
        return _fmt_rows(["id", "short", "visits", "last_seen"], rows2, max_rows=n)

    if head == "seen":
        if len(parts) < 2 or not parts[1].startswith("!"):
            return "Uso: seen !id (ej: seen !d9b19712)"
        node_id = parts[1]
        row = bbs_users.seen(node_id)
        if not row:
            return f"No tengo registros para {node_id}"
        (nid, short, name, visits, first_ts, last_ts, last_msg) = row
        return (
            f"Seen {nid}\n"
            f"short: {short}\n"
            f"name : {name}\n"
            f"visits: {visits}\n"
            f"first: {_ts(first_ts)}\n"
            f"last : {_ts(last_ts)}\n"
            f"last msg: {last_msg}\n"
        )

    # Si pega directamente un !id
    if cmd.startswith("!"):
        row = bbs_users.seen(cmd)
        if not row:
            return f"No tengo registros para {cmd}"
        (nid, short, name, visits, first_ts, last_ts, last_msg) = row
        return (
            f"{nid} ({short})\n"
            f"visits: {visits}\n"
            f"last : {_ts(last_ts)}\n"
            f"last msg: {last_msg}\n"
        )

    return "Comando inválido. Enviá 'menu' o 'help'."
