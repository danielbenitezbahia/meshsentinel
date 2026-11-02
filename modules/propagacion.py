menu_name = "Pronóstico Troposférico"


def display_menu(bbs_system):
    return (
        "=[ 📡 PROPAGACIÓN TROPOSFÉRICA ]=\n"
        "[1] 🌐 Pronóstico general\n"
        "[2] ⭐ Próximas condiciones favorables\n"
        "[3] 🗺 Pronóstico por sector\n"
        "[4] 🛣 Posibles corredores\n"
        "[5] 🕒 Antigüedad y vigencia\n"
        "[6] ❓ Ayuda\n"
        "[7] ↩ Volver\n"
        "> opcion:"
    )


def _sector_menu(bbs_system):
    sectors = bbs_system.tropocast_service.sector_list()
    lines = ["🗺 Elegí un sector:"]
    for i, name in enumerate(sectors, start=1):
        lines.append(f"s{i}. {name}")
    lines.append("Respondé con el código, ej: s2")
    return "\n".join(lines)


def process_command(user_id, command, bbs_system):
    cmd = (command or "").strip().lower()
    service = bbs_system.tropocast_service

    if cmd in ("", "menu"):
        return display_menu(bbs_system)

    if cmd == "1":
        return service.general_summary()

    if cmd == "2":
        return service.favorable_windows()

    if cmd == "3":
        return _sector_menu(bbs_system)

    if cmd.startswith("s") and cmd[1:].isdigit():
        sectors = service.sector_list()
        idx = int(cmd[1:]) - 1
        if 0 <= idx < len(sectors):
            return service.sector_forecast(sectors[idx])
        return "Sector inválido. Enviá '3' para ver la lista."

    if cmd == "4":
        return service.corridor_candidates()

    if cmd == "5":
        return service.freshness()

    if cmd == "7":
        return "__back__"

    if cmd in ("6", "help", "ayuda", "?"):
        return (
            "❓ Ayuda Propagación\n"
            "──────────────────\n"
            "1: Pronóstico general (700km)\n"
            "2: Próximas ventanas favorables\n"
            "3: Pronóstico por sector (Bahía Blanca, Viedma, etc.)\n"
            "4: Posibles corredores entre sectores\n"
            "5: Antigüedad del último dato\n"
            "Datos de Tropocast.eu, modelo GDPS, se actualizan cada 6hs.\n"
            "No son garantía de enlace, solo potencial del modelo.\n"
            "'cd ..' volver"
        )

    return "Opción inválida. Enviá 'menu' para ver opciones."
