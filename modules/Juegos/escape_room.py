menu_name = "Escape Room"  # Required for module loading

def display_menu():
    # Display the introduction and instructions for the Escape Room game
    return (
        "¡Bienvenido al Escape Room!\n"
        "Estás encerrado en una habitación.\n"
        "Explorá el entorno, resolvé acertijos y escapá.\n"
        "Comandos: norte, sur, este, oeste, examinar, recoger, usar.\n"
        "'cd ..' para volver al menú principal."
    )

def init_game():
    """Initialize the game state."""
    return {
        "current_room": "start",  # Start room
        "inventory": [],  # Items collected by the player
        "rooms": {
            "start": {
                "description": (
                    "Estás en una habitación cerrada con llave. "
                    "Hay una puerta al norte, una mesa en una esquina "
                    "y un cuadro colgado en la pared."
                ),
                "objects": {
                    "table": "Una vieja mesa de madera con un cajón.",
                    "painting": "Un cuadro de un paisaje; parece estar ligeramente torcido."
                },
                "items": {},
                "exits": {"norte": "locked_door", "este": "hidden_room"}
            },
            "locked_door": {
                "description": "Una puerta cerrada bloquea tu camino. Ves una cerradura.",
                "objects": {
                    "door": "La puerta está cerrada. Hay una cerradura visible."
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
                    "chest": "Un cofre antiguo con una cerradura de combinación.",
                    "bookshelf": "Una estantería polvorienta llena de libros variados."
                },
                "items": {},
                "exits": {"oeste": "start"}
            }
        },
        "door_unlocked": False,  # Track if the door is unlocked
        "chest_unlocked": False  # Track if the chest is unlocked
    }

def process_command(user_id, command, bbs_system):
    """Handle commands for the Escape Room game."""
    # Check if the user exists in the system, and initialize user state if not
    if user_id not in bbs_system.users:
        bbs_system.users[user_id] = {}

    user_state = bbs_system.users[user_id]

    # Initialize the game if not already done
    if "escape_room" not in user_state:
        user_state["escape_room"] = init_game()

    game = user_state["escape_room"]

    # Return to the main menu if the user types 'cd ..'
    if command.strip().lower() == "cd ..":
        bbs_system.users[user_id]["menu"].pop()
        return bbs_system.display_menu(user_id)

    # Normalize command input and parse the action and target
    parts = command.strip().lower().replace("go ", "").replace("move ", "").split(" ", 1)
    action = parts[0]
    target = parts[1] if len(parts) > 1 else None

    current_room = game["current_room"]
    room_data = game["rooms"][current_room]

    # Handle directional movement
    if action in ["norte", "sur", "este", "oeste"]:
        return move_player(action, game)

    # Handle examining the room or objects
    if action in ["look", "examinar"] and (target == "room" or not target):
        description = room_data["description"]
        exits = ", ".join(room_data.get("exits", {}).keys()) or "ninguna"
        return f"{description}\nSalidas: {exits}."

    # Handle examining specific objects
    if action == "examinar" and target:
        return examine_object(target, room_data, game)

    # Handle picking up items
    if action == "pick" and target and target.startswith("up"):
        item = target[3:].strip()
        return pick_up_item(item, room_data, game)

    # Handle using items
    if action == "use" and target:
        return use_item(target, game)

    # Handle displaying the inventory
    if action == "inventory":
        inv = ", ".join(game["inventory"]) if game["inventory"] else "vacío"
        return f"Inventario: {inv}"

    # Display help or handle unrecognized commands
    if action == "help" or action not in ["norte", "sur", "este", "oeste", "examinar", "pick", "use", "inventory"]:
        exits = ", ".join(room_data.get("exits", {}).keys()) or "ninguna"
        objects = ", ".join(room_data.get("objects", {}).keys()) or "ninguno"
        return (
            "Comando inválido. Probá con:\n"
            "- Movimiento: norte, sur, este, oeste\n"
            f"- Examinar: room, {objects}\n"
            "- Recoger: pick up <item>\n"
            "- Usar: use <item>\n"
            f"Salidas disponibles: {exits}."
        )

    # Fallback for any unrecognized commands
    return "Comando inválido. Escribí 'help' para ver la lista de comandos."

def move_player(direction, game):
    """Handle player movement."""
    # Determine if the player can move in the given direction
    current_room = game["current_room"]
    exits = game["rooms"][current_room].get("exits", {})
    if direction in exits:
        # Check if the door is locked before moving
        if exits[direction] == "locked_door" and not game["door_unlocked"]:
            return "The door is locked. You need to unlock it first."
        game["current_room"] = exits[direction]
        return f"You moved {direction}.\n\n{game['rooms'][game['current_room']]['description']}"
    else:
        return "You can't go that way."

def examine_object(target, room_data, game):
    """Handle examining objects."""
    # Check if the target object is in the current room
    if target in room_data.get("objects", {}):
        if target == "table":
            return "An old wooden table with a drawer. Maybe you should open the drawer."
        elif target == "drawer":
            if "key" not in game["inventory"]:
                room_data["items"]["key"] = "A rusty key inside the drawer."
                return "You opened the drawer and found a rusty key."
            else:
                return "The drawer is empty."
        elif target == "painting":
            return "A painting of a landscape; it seems slightly askew. Perhaps you should adjust it."
        elif target == "chest":
            if not game["chest_unlocked"]:
                return "An old chest with a combination lock. Maybe there's a clue nearby."
            else:
                return "The chest is open. Inside, you see a shiny gem."
        elif target == "bookshelf":
            return "A dusty bookshelf filled with various books. One book seems out of place."
        else:
            return room_data["objects"][target]
    else:
        return "You don't see that here."

def pick_up_item(item, room_data, game):
    """Handle picking up items."""
    # Check if the item is available in the room
    items = room_data.get("items", {})
    if item in items:
        game["inventory"].append(item)
        del items[item]
        return f"You picked up {item}."
    else:
        return "You can't pick that up."

def use_item(target, game):
    """Handle using items."""
    # Check if the user can use the item in the current context
    if "key" in target and "key" in game["inventory"] and game["current_room"] == "locked_door":
        game["door_unlocked"] = True
        return "You used the key to unlock the door! You can now go norte."
    else:
        return "You can't use that here."
