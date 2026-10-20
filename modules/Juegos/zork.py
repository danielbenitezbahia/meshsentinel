menu_name = "ZORK"

def display_menu():
    return "¡Bienvenido a Zork!\n1. Empezar\n2. ↩ Volver"

def init_game():
    return {
        "location": "field",
        "inventory": []
    }

def process_command(user_id, command, bbs_system):
    if user_id not in bbs_system.users:
        bbs_system.users[user_id] = {}

    user_state = bbs_system.users[user_id]

    if "zork_game" not in user_state:
        if command == "1":
            user_state["zork_game"] = init_game()
            return handle_field(user_state["zork_game"])
        elif command.strip() == "2" or command.strip().lower() == "cd ..":
            return "__back__"
        else:
            return "Opción inválida. Ingresá '1' para jugar o '2' para volver."

    game = user_state["zork_game"]

    if command.strip().lower() == "cd ..":
        return "__back__"

    if command.strip().lower() == "ayuda":
        return "Los comandos disponibles dependen de dónde estés. Probá mirar alrededor o moverte en una dirección.\nEjemplos: 'mirar', 'ir al este', 'abrir buzon'."

    if game["location"] == "field":
        return handle_field(game, command)
    elif game["location"] == "forest":
        return handle_forest(game, command)
    elif game["location"] == "clearing":
        return handle_clearing(game, command)
    elif game["location"] == "cave":
        return handle_cave(game, command)
    else:
        return "Estado de juego desconocido."

def handle_field(game, command=None):
    if command is None or command in ["mirar", "mirar alrededor"]:
        return ("Estás parado en un campo abierto al oeste de una casa blanca, con la puerta de entrada tapiada.\n"
                "(Un sendero secreto lleva al sudoeste, hacia el bosque.)\n"
                "Hay un Buzón Pequeño.\n¿Qué hacés?")
    elif command == "tomar buzon":
        return "Está firmemente anclado."
    elif command in ["abrir buzon", "mirar en buzon"]:
        return "Al abrir el buzón pequeño encontrás un folleto."
    elif command == "ir al este":
        return "La puerta está tapiada y no podés sacar las tablas."
    elif command == "abrir puerta":
        return "La puerta no se puede abrir."
    elif command == "tomar tablas":
        return "Las tablas están firmemente aseguradas."
    elif command in ["mirar casa", "examinar casa"]:
        return "La casa es una hermosa casa colonial pintada de blanco. Está claro que sus dueños deben haber sido extremadamente ricos."
    elif command in ["ir al sudoeste", "ir al sendero secreto"]:
        game["location"] = "forest"
        return handle_forest(game)
    elif command == "leer folleto":
        return "Bienvenido a la versión no oficial en Python de Zork. Tu misión es encontrar una Estatua de Jade."
    else:
        return "Comando inválido. Probá 'mirar', 'ir al sudoeste', o 'abrir buzon'."

def handle_forest(game, command=None):
    if command is None or command in ["mirar", "mirar alrededor"]:
        return "Esto es un bosque, con árboles en todas direcciones. Hacia el este, parece haber luz solar.\n¿Qué hacés?"
    elif command == "ir al oeste":
        return "Necesitarías un machete para ir más al oeste."
    elif command == "ir al norte":
        return "El bosque se vuelve impenetrable hacia el norte."
    elif command == "ir al sur":
        return "Árboles azotados por la tormenta bloquean tu camino."
    elif command == "ir al este":
        game["location"] = "clearing"
        return handle_clearing(game)
    else:
        return "Comando inválido. Probá 'mirar' o 'ir al este'."

def handle_clearing(game, command=None):
    if command is None or command in ["mirar", "mirar alrededor"]:
        return ("Estás en un claro, rodeado de bosque por todos lados. Un sendero lleva al sur.\n"
                "Hay una reja abierta, que desciende hacia la oscuridad.\n¿Qué hacés?")
    elif command == "ir al sur":
        return "Ves un ogro enorme y das la vuelta."
    elif command in ["bajar por la reja", "ir hacia abajo"]:
        game["location"] = "cave"
        return handle_cave(game)
    else:
        return "Comando inválido. Probá 'mirar' o 'bajar por la reja'."

def handle_cave(game, command=None):
    if command is None or command in ["mirar", "mirar alrededor"]:
        return "Estás en una cueva pequeña con una escalera oscura y amenazante que baja.\nHay un esqueleto de un hombre en un rincón.\n¿Qué hacés?"
    elif command in ["bajar la escalera", "ir hacia abajo"]:
        return ("Entrás a una habitación con piso de barro.\n"
                "Medio enterrado en el barro hay un baúl viejo, repleto de joyas.\n"
                "¡Encontraste la Estatua de Jade y completaste tu misión!")
    elif command == "tomar esqueleto":
        return "¿Por qué harías eso? ¿Sos alguna especie de enfermo?"
    elif command == "romper esqueleto":
        return "Qué persona enferma. Tené un poco de respeto, viejo."
    elif command == "iluminar habitacion":
        return "Necesitarías una antorcha o una lámpara para hacer eso."
    elif command == "quebrar esqueleto":
        return "Tengo dos preguntas: ¿Por qué? ¿Y con qué?"
    else:
        return "Comando inválido. Probá 'mirar' o 'bajar la escalera'."
