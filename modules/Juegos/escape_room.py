menu_name = "Escape Room"  # Necesario para que el módulo cargue

def display_menu():
    return (
        "¡Bienvenido al Escape Room!\n"
        "Estás encerrado en una habitación.\n"
        "Explorá el entorno, resolvé acertijos y escapá.\n"
        "Comandos: norte, sur, este, oeste, examinar, recoger, usar.\n"
        "0. ↩ Volver"
    )

def init_game():
    """Inicializa el estado del juego."""
    return {
        "current_room": "start",  # Habitación inicial
        "inventory": [],  # Objetos recolectados por el jugador
        "rooms": {
            "start": {
                "description": (
                    "Estás en una habitación cerrada con llave. "
                    "Hay una puerta al norte, una mesa en una esquina "
                    "y un cuadro colgado en la pared."
                ),
                "objects": {
                    "mesa": "Una vieja mesa de madera con un cajón.",
                    "cajon": "Un cajón de la mesa.",
                    "cuadro": "Un cuadro de un paisaje; parece estar ligeramente torcido."
                },
                "items": {},
                "exits": {"norte": "locked_door", "este": "hidden_room"}
            },
            "locked_door": {
                "description": "Una puerta cerrada bloquea tu camino. Ves una cerradura.",
                "objects": {
                    "puerta": "La puerta está cerrada. Hay una cerradura visible."
                },
                "items": {},
                "exits": {"sur": "start"}
            },
            "hidden_room": {
                "description": (
                    "Has entrado en una habitación secreta. "
                    "Hay un cofre en el centro y una estantería contra la pared."
                ),
                "objects": {
                    "cofre": "Un cofre antiguo con una cerradura de combinación.",
                    "estanteria": "Una estantería polvorienta llena de libros variados."
                },
                "items": {},
                "exits": {"oeste": "start"}
            }
        },
        "door_unlocked": False,   # Si la puerta ya fue desbloqueada
        "chest_unlocked": False,  # Si el cofre ya fue desbloqueado
        "escaped": False          # Si ya se mostró el mensaje de escape
    }

def process_command(user_id, command, bbs_system):
    """Maneja los comandos del juego Escape Room."""
    if user_id not in bbs_system.users:
        bbs_system.users[user_id] = {}

    user_state = bbs_system.users[user_id]

    if "escape_room" not in user_state:
        user_state["escape_room"] = init_game()

    game = user_state["escape_room"]

    if command.strip() == "0" or command.strip().lower() == "cd ..":
        return "__back__"

    # Normaliza el comando: saca el prefijo opcional "ir " y separa en
    # acción + objetivo.
    normalized = command.strip().lower()
    if normalized.startswith("ir "):
        normalized = normalized[3:]
    parts = normalized.split(" ", 1)
    action = parts[0]
    target = parts[1].strip() if len(parts) > 1 else None

    current_room = game["current_room"]
    room_data = game["rooms"][current_room]

    # Movimiento
    if action in ["norte", "sur", "este", "oeste"]:
        return move_player(action, game)

    # Examinar la habitación (sin objeto) o un objeto puntual
    if action == "examinar":
        if not target:
            description = room_data["description"]
            exits = ", ".join(room_data.get("exits", {}).keys()) or "ninguna"
            return f"{description}\nSalidas: {exits}."
        return examine_object(target, room_data, game)

    # Recoger objetos
    if action == "recoger" and target:
        return pick_up_item(target, room_data, game)

    # Usar objetos
    if action == "usar" and target:
        return use_item(target, game)

    # Inventario
    if action == "inventario":
        inv = ", ".join(game["inventory"]) if game["inventory"] else "vacío"
        return f"Inventario: {inv}"

    # Ayuda o comando no reconocido
    exits = ", ".join(room_data.get("exits", {}).keys()) or "ninguna"
    objects = ", ".join(room_data.get("objects", {}).keys()) or "ninguno"
    return (
        "Comando inválido. Probá con:\n"
        "- Movimiento: norte, sur, este, oeste\n"
        f"- Examinar: examinar <objeto> (sin objeto para mirar la habitación). Objetos acá: {objects}\n"
        "- Recoger: recoger <objeto>\n"
        "- Usar: usar <objeto>\n"
        f"Salidas disponibles: {exits}."
    )

def move_player(direction, game):
    """Maneja el movimiento del jugador."""
    current_room = game["current_room"]
    exits = game["rooms"][current_room].get("exits", {})
    if direction not in exits:
        return "No podés ir hacia ahí."

    destination = exits[direction]
    if destination == "locked_door" and not game["door_unlocked"]:
        return "La puerta está cerrada con llave. Necesitás desbloquearla primero."

    game["current_room"] = destination

    # Entrar por primera vez a la puerta ya desbloqueada es, en sí, el escape.
    if destination == "locked_door" and game["door_unlocked"] and not game["escaped"]:
        game["escaped"] = True
        return (
            "Abrís la puerta y salís caminando... ¡Escapaste de la habitación!\n"
            "🎉 ¡Felicitaciones, completaste el Escape Room! 🎉"
        )

    return f"Te moviste hacia el {direction}.\n\n{game['rooms'][destination]['description']}"

def examine_object(target, room_data, game):
    """Maneja el examen de objetos."""
    if target not in room_data.get("objects", {}):
        return "No ves eso acá."

    if target == "mesa":
        return "Una vieja mesa de madera con un cajón. Tal vez deberías abrir el cajón."
    elif target == "cajon":
        if "llave" not in game["inventory"]:
            room_data["items"]["llave"] = "Una llave oxidada dentro del cajón."
            return "Abrís el cajón y encontrás una llave oxidada."
        else:
            return "El cajón está vacío."
    elif target == "cuadro":
        return "Un cuadro de un paisaje; parece estar ligeramente torcido. Tal vez deberías acomodarlo."
    elif target == "cofre":
        if not game["chest_unlocked"]:
            return "Un cofre antiguo con una cerradura de combinación. Quizás haya alguna pista cerca."
        else:
            return "El cofre está abierto. Adentro ves una gema brillante."
    elif target == "estanteria":
        if "nota" not in game["inventory"]:
            room_data["items"]["nota"] = "Una nota con una combinación garabateada."
            return "Un libro parece fuera de lugar. Lo sacás y encontrás una nota con una combinación."
        else:
            return "Una estantería polvorienta llena de libros variados."
    elif target == "puerta":
        estado = "desbloqueada" if game["door_unlocked"] else "cerrada con llave"
        return f"La puerta está {estado}. Hay una cerradura visible."
    else:
        return room_data["objects"][target]

def pick_up_item(item, room_data, game):
    """Maneja recoger objetos."""
    items = room_data.get("items", {})
    if item in items:
        game["inventory"].append(item)
        del items[item]
        return f"Recogiste: {item}."
    else:
        return "No podés recoger eso."

def use_item(target, game):
    """Maneja usar objetos."""
    if "llave" in target and "llave" in game["inventory"] and game["current_room"] == "start":
        game["door_unlocked"] = True
        return "Usaste la llave para desbloquear la puerta. Ahora podés ir al norte."
    if "nota" in target and "nota" in game["inventory"] and game["current_room"] == "hidden_room":
        if game["chest_unlocked"]:
            return "El cofre ya está abierto."
        game["chest_unlocked"] = True
        return "Usaste la nota para marcar la combinación correcta. ¡El cofre se abre! Adentro hay una gema brillante."
    return "No podés usar eso acá."
