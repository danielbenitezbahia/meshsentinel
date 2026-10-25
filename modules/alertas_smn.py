menu_name = "Alertas Vigentes SMN"

SIN_ALERTAS = "No hay alertas vigentes para las próximas 72hs. en el Sudoeste Bonaerense."


def _render(bbs_system):
    messages = bbs_system.build_smn_messages_by_partido()
    body = "\n---\n".join(messages) if messages else SIN_ALERTAS
    return f"{body}\n\n0. ↩ Volver"


def display_menu(bbs_system):
    """Recibe bbs_system (ver BBSSystem._call_display_menu) para mostrar
    las alertas vigentes apenas se entra al menú, sin un paso intermedio."""
    return _render(bbs_system)


def process_command(user_id, command, bbs_system):
    if command.strip() == "0" or command.strip().lower() == "cd ..":
        return "__back__"
    # Cualquier otra entrada vuelve a consultar en vivo (funciona como "refrescar").
    return _render(bbs_system)
