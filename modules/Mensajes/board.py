import time
import bbs_messages

menu_name = "Muro de mensajes"


def display_menu():
    return (
        "Muro de mensajes\n"
        "1) Listar mensajes\n"
        "2) Postear mensaje\n"
        "3) Leer mensaje\n"
        "4) Borrar mi mensaje\n"
        "\n"
        "Comandos directos:\n"
        "post texto...\n"
        "read 5\n"
        "delete 5\n"
        "\ncd .. para volver"
    )


def _ts(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _get_author(user_id, bbs_system):
    try:
        serial = bbs_system.interface.interface
        info = serial.nodes.get(user_id, {})
        user = info.get("user", {})
        return user.get("longName") or user.get("shortName") or user_id
    except Exception:
        return user_id


def process_command(user_id, command, bbs_system):
    cmd = (command or "").strip()

    if cmd in ("", "menu"):
        return display_menu()

    if cmd == "1":
        rows = bbs_messages.list_messages()
        if not rows:
            return "No hay mensajes."
        out = ["ID | Autor | Fecha", "-" * 40]
        for mid, author, ts in rows:
            out.append(f"{mid} | {author} | {_ts(ts)}")
        return "\n".join(out)

    if cmd == "2":
        return "Escribí: post tu mensaje"

    if cmd == "3":
        return "Escribí: read NUMERO"

    if cmd == "4":
        return "Escribí: delete NUMERO"

    # post
    if cmd.lower().startswith("post "):
        text = cmd[5:].strip()
        if not text:
            return "Mensaje vacío."
        author = _get_author(user_id, bbs_system)
        mid = bbs_messages.post_message(user_id, author, text)
        return f"Mensaje publicado con ID {mid}"

    # read
    if cmd.lower().startswith("read "):
        try:
            mid = int(cmd.split()[1])
        except:
            return "Uso: read 5"

        row = bbs_messages.get_message(mid)
        if not row:
            return "No existe."
        mid, author, body, ts = row
        return f"Mensaje {mid}\nAutor: {author}\nFecha: {_ts(ts)}\n\n{body}"

    # delete
    if cmd.lower().startswith("delete "):
        try:
            mid = int(cmd.split()[1])
        except:
            return "Uso: delete 5"

        ok = bbs_messages.delete_message(mid, user_id)
        return "Borrado." if ok else "No podés borrar ese mensaje."

    return "Comando inválido."
