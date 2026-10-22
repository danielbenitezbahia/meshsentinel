menu_name = "Escape Room Criollo"

def display_menu():
    return (
        "🏚 ¡Bienvenido a la Estación El Jagüel!\n"
        "Te mandaron a revisar una vieja estación meteorológica/repetidora, "
        "abandonada hace años, que la semana pasada volvió a transmitir sola. "
        "Apenas entrás, la puerta se traba atrás tuyo.\n"
        "Explorá, resolvé lo que pasó con el último operador, y encontrá la forma de salir.\n"
        "Comandos: norte, sur, este, oeste, arriba, abajo, examinar, recoger, usar, mapa.\n"
        "0. ↩ Volver"
    )

def init_game():
    """Inicializa el estado del juego."""
    return {
        "current_room": "entrada",
        "inventory": [],
        "rooms": {
            "entrada": {
                "description": (
                    "Estás en la galería de entrada de la estación. La puerta principal "
                    "se trabó sola atrás tuyo. Hay un perchero viejo en un rincón y un "
                    "escritorio con un cuaderno de guardia abierto."
                ),
                "objects": {
                    "perchero": "Un perchero de madera. Cuelga de ahí una linterna, parece rota.",
                    "escritorio": (
                        "El cuaderno de guardia está abierto en la última anotación:\n"
                        "\"15/03, 18hs — Presión bajando fuerte, 998 hPa. Viene tormenta. "
                        "Anoto todo como siempre, por si las dudas.\"\n"
                        "La fecha podría ser la combinación de algo: probá escribirla "
                        "como 4 números juntos, sin la barra."
                    ),
                    "puerta": "La puerta principal. Está trabada, no cede ni un poco.",
                },
                "items": {},
                "exits": {"este": "cocina", "norte": "deposito", "afuera": "afuera"},
            },
            "cocina": {
                "description": (
                    "La cocina vieja de la estación. Hay una alacena contra la pared, "
                    "una radio de onda corta cubierta de polvo, y arriba de la mesada "
                    "un mate lavado hace mucho tiempo."
                ),
                "objects": {
                    "alacena": "Una alacena de chapa. Adentro hay una pila suelta, todavía con carga.",
                    "radio": "Una radio de onda corta vieja. Si la escuchás de cerca, se oye estática y, muy de vez en cuando, algo que casi parece una voz.",
                    "mate": "Un mate de calabaza, lavado hace rato. No queda nada adentro. Típico.",
                },
                "items": {},
                "exits": {"oeste": "entrada", "norte": "sala_equipos"},
            },
            "deposito": {
                "description": (
                    "El depósito de instrumentos meteorológicos. Un pluviómetro y un "
                    "anemómetro rotos juntan polvo en un estante. En el otro rincón hay "
                    "una caja fuerte chica, con un disco de combinación de 4 números."
                ),
                "objects": {
                    "pluviometro": "Un pluviómetro viejo, el vidrio rajado. Hace años que no mide nada.",
                    "anemometro": "Un anemómetro con las paletas rotas. Tampoco sirve.",
                    "caja": "Una caja fuerte chica, con un disco de combinación de 4 números.",
                },
                "items": {},
                "exits": {"sur": "entrada", "este": "dormitorio"},
            },
            "sala_equipos": {
                "description": (
                    "El corazón técnico de la estación. Paneles de radio contra toda una "
                    "pared, y un panel con 4 cables sueltos (rojo, verde, amarillo, azul) "
                    "que alguien desconectó a propósito."
                ),
                "objects": {
                    "panel": "4 cables sueltos: rojo, verde, amarillo, azul. Hay que reconectarlos en algún orden.",
                },
                "items": {},
                "exits": {"sur": "cocina", "arriba": "torre"},
            },
            "dormitorio": {
                "description": (
                    "El dormitorio del operador. La cama está deshecha. Hay un ropero "
                    "cerrado contra la pared, y dos carteles pegados con cinta."
                ),
                "objects": {
                    "ropero": "Un ropero de madera, cerrado con llave.",
                    "cartel": (
                        "Un cartel técnico, escrito a mano:\n"
                        "\"Orden correcto del cableado, de arriba a abajo: "
                        "Verde, Rojo, Amarillo, Azul. Nunca lo cambien.\""
                    ),
                    "aviso": "Otro cartel, más chico: \"NO TOCAR EL BREAKER 2, ES EL DE LA ANTENA VIEJA.\"",
                },
                "items": {},
                "exits": {"oeste": "deposito", "abajo": "sotano"},
            },
            "sotano": {
                "description": (
                    "El sótano de las baterías. Está completamente a oscuras."
                ),
                "objects": {
                    "panel": "No se ve nada, está muy oscuro.",
                },
                "items": {},
                "exits": {"arriba": "dormitorio"},
            },
            "torre": {
                "description": (
                    "Subiste hasta la base de la antena. El viento del campo se siente "
                    "fuerte acá arriba. Hay un control viejo con dos palancas, y la antena "
                    "misma, modificada con algo que no parece de fábrica."
                ),
                "objects": {
                    "antena": "La antena fue modificada a mano. Enroscado en la base hay un papel doblado.",
                    "control": (
                        "Un control con dos opciones: cortar la transmisión, o dejarla seguir. "
                        "Comandos: 'cortar transmision' o 'dejar transmision'."
                    ),
                },
                "items": {},
                "exits": {"abajo": "sala_equipos"},
            },
        },
        "linterna_arreglada": False,
        "caja_abierta": False,
        "ropero_abierto": False,
        "cableado_progreso": 0,
        "cableado_resuelto": False,
        "breaker_resuelto": False,
        "final_decidido": None,   # None, "cortar" o "dejar"
        "terminado": False,
    }

CABLE_ORDEN = ["verde", "rojo", "amarillo", "azul"]

def process_command(user_id, command, bbs_system):
    """Maneja los comandos del juego Escape Room Criollo."""
    if user_id not in bbs_system.users:
        bbs_system.users[user_id] = {}

    user_state = bbs_system.users[user_id]

    if "escape_room_criollo" not in user_state:
        user_state["escape_room_criollo"] = init_game()

    game = user_state["escape_room_criollo"]

    if command.strip() == "0" or command.strip().lower() == "cd ..":
        return "__back__"

    normalized = command.strip().lower()
    if normalized.startswith("ir "):
        normalized = normalized[3:]
    parts = normalized.split(" ", 1)
    action = parts[0]
    target = parts[1].strip() if len(parts) > 1 else None
    # "batería"/"bateria" es el sinónimo más natural de "pila" (varios
    # jugadores la van a tipear así); se normaliza acá una sola vez.
    if target in ["bateria", "batería"]:
        target = "pila"
    # La descripción de la torre menciona "palancas", pero el objeto
    # examinable es "control" -- mismo alias para que se pueda
    # examinar/usar con la palabra que el jugador realmente ve en pantalla.
    if target in ["palanca", "palancas"]:
        target = "control"

    current_room = game["current_room"]
    room_data = game["rooms"][current_room]

    # Movimiento
    if action in ["norte", "sur", "este", "oeste", "arriba", "abajo", "afuera"]:
        return move_player(action, game)

    # Examinar (y su sinonimo mas natural "mirar", que el propio texto de
    # ayuda del juego usa para describir esta accion)
    if action in ["examinar", "mirar"]:
        if not target:
            description = room_data["description"]
            exits = ", ".join(room_data.get("exits", {}).keys()) or "ninguna"
            return f"{description}\nSalidas: {exits}."
        return examine_object(target, room_data, game)

    # Recoger
    if action == "recoger" and target:
        return pick_up_item(target, room_data, game)

    # Usar (llave, código de la caja)
    if action == "usar" and target:
        return use_item(target, game)

    # Arreglar (pila + linterna)
    if action == "arreglar" and target == "linterna":
        return arreglar_linterna(game)

    # Conectar cables
    if action == "conectar" and target:
        return conectar_cable(target, game)

    # Prender breaker
    if action == "prender" and target:
        return prender_breaker(target, game)

    # Decisión final en la torre
    if action in ["cortar", "dejar"] and target == "transmision":
        return decidir_final(action, game)

    # Leer el diario
    if action == "leer" and target == "diario":
        return leer_diario(game)

    # Inventario
    if action == "inventario":
        inv = ", ".join(game["inventory"]) if game["inventory"] else "vacío"
        return f"Inventario: {inv}"

    # Mapa (solo la sala actual y sus salidas inmediatas: pensado para
    # pantallas de celular y el límite de tamaño de los mensajes del mesh,
    # no acumula todo el mapa descubierto)
    if action == "mapa":
        return mostrar_mapa(game)

    # Ayuda o comando no reconocido
    exits = ", ".join(room_data.get("exits", {}).keys()) or "ninguna"
    objects = ", ".join(room_data.get("objects", {}).keys()) or "ninguno"
    return (
        "Comando inválido. Probá con:\n"
        "- Movimiento: norte, sur, este, oeste, arriba, abajo\n"
        f"- Examinar: examinar <objeto> (sin objeto para mirar la habitación). Objetos acá: {objects}\n"
        "- Recoger: recoger <objeto>\n"
        "- Usar: usar <objeto o código>\n"
        f"Salidas disponibles: {exits}."
    )

def move_player(direction, game):
    """Maneja el movimiento del jugador."""
    current_room = game["current_room"]
    exits = game["rooms"][current_room].get("exits", {})
    if direction not in exits:
        return "No podés ir hacia ahí."

    destino = exits[direction]

    if destino == "torre" and not game["cableado_resuelto"]:
        return "No hay forma de subir, el cableado de la antena está cortado."

    if destino == "afuera":
        return abrir_puerta_final(game)

    game["current_room"] = destino
    destino_data = game["rooms"][destino]
    destino_exits = ", ".join(destino_data.get("exits", {}).keys()) or "ninguna"
    return (
        f"Te moviste hacia {destino_legible(destino)}.\n\n"
        f"{destino_data['description']}\nSalidas: {destino_exits}."
    )

def destino_legible(nombre_room):
    nombres = {
        "entrada": "la entrada",
        "cocina": "la cocina",
        "deposito": "el depósito",
        "sala_equipos": "la sala de equipos",
        "dormitorio": "el dormitorio",
        "sotano": "el sótano",
        "torre": "la torre",
    }
    return nombres.get(nombre_room, nombre_room)

ORDEN_DIRECCIONES = ["norte", "sur", "este", "oeste", "arriba", "abajo", "afuera"]
DIRECCION_ICONOS = {
    "norte": "↑ Norte",
    "sur": "↓ Sur",
    "este": "→ Este",
    "oeste": "← Oeste",
    "arriba": "↑ Arriba",
    "abajo": "↓ Abajo",
}

def mostrar_mapa(game):
    """Muestra un mapa compacto: la sala actual y sus salidas inmediatas,
    nada más. Pensado para pantallas de celular y el límite de tamaño de
    los mensajes del mesh -- no acumula todo el mapa descubierto."""
    current_room = game["current_room"]
    exits = game["rooms"][current_room].get("exits", {})
    lineas = [f"📍 Estás en {destino_legible(current_room)}.", ""]
    for direccion in ORDEN_DIRECCIONES:
        if direccion not in exits:
            continue
        destino = exits[direccion]
        if destino == "afuera":
            lineas.append("🚪 afuera (si ya decidiste algo en la torre)")
            continue
        etiqueta = DIRECCION_ICONOS[direccion]
        lineas.append(f"{etiqueta}: {destino_legible(destino)}")
    return "\n".join(lineas)

def examine_object(target, room_data, game):
    """Maneja el examen de objetos."""
    current_room = game["current_room"]

    # El sótano a oscuras: no se puede examinar nada hasta tener la linterna arreglada.
    if current_room == "sotano" and not game["linterna_arreglada"]:
        return "Está muy oscuro para ver nada. Vas a necesitar algo de luz."

    if target not in room_data.get("objects", {}):
        return "No ves eso acá."

    if target == "perchero":
        if "linterna" not in game["inventory"]:
            room_data["items"]["linterna"] = "Una linterna rota. Le falta una pila."
            return "Revisás el perchero: hay una linterna colgada, pero está rota. Le falta una pila."
        return "El perchero está vacío, ya agarraste la linterna."

    if target == "alacena":
        if "pila" not in game["inventory"]:
            room_data["items"]["pila"] = "Una pila suelta, todavía con carga."
            return "Revisás la alacena: hay una pila suelta, todavía con carga. Si la agarrás y tenés la linterna, probá 'usar linterna'."
        return "La alacena está vacía, ya agarraste la pila."

    if target == "caja":
        if game["caja_abierta"]:
            return "La caja fuerte está abierta."
        return (
            "Una caja fuerte chica, con un disco de combinación de 4 números. "
            "Para probar un código, escribí 'usar' seguido de los 4 números, "
            "por ejemplo: usar 1234."
        )

    if target == "ropero":
        if game["ropero_abierto"]:
            return "El ropero está abierto, ya revisaste adentro."
        return "Un ropero de madera, cerrado con llave."

    if target == "panel" and current_room == "sala_equipos":
        if game["cableado_resuelto"]:
            return "Los 4 cables están bien conectados: verde, rojo, amarillo, azul."
        return f"4 cables sueltos: rojo, verde, amarillo, azul. Llevás {game['cableado_progreso']}/4 conectados en el orden correcto."

    if target == "panel" and current_room == "sotano":
        if game["breaker_resuelto"]:
            return "El breaker correcto ya está prendido. Las luces del sótano funcionan."
        return "Un panel con varias llaves térmicas numeradas. Una de ellas tiene un cartel al lado que la señala como peligrosa."

    if target == "antena":
        if "pagina3" not in game["inventory"]:
            room_data["items"]["pagina3"] = "Una página de diario, doblada, enroscada en la base de la antena."
            return "Enroscado en la base de la antena encontrás un papel doblado: es una página de diario."
        return "La antena, modificada a mano. Ya sacaste el papel que tenía enroscado."

    if target == "puerta" and current_room == "entrada":
        if game["final_decidido"] is None:
            return "La puerta principal. Está trabada, no cede ni un poco."
        return "La puerta principal. Ahora que decidiste algo en la torre, parece que podría abrirse si lo intentás (movete hacia 'afuera')."

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
    """Maneja usar objetos/códigos. Cubre: código de la caja fuerte, llave del ropero,
    y linterna/pila como alias de 'arreglar linterna' (el verbo 'usar' es el más
    intuitivo, consistente con el resto del juego)."""
    current_room = game["current_room"]

    if target in ["linterna", "pila"]:
        return arreglar_linterna(game)

    if target == "control" and current_room == "torre":
        return (
            "El control tiene dos palancas: una corta la transmisión, la otra "
            "la deja seguir. Escribí 'cortar transmision' o 'dejar transmision' "
            "para elegir."
        )

    if current_room == "deposito":
        if target == "caja":
            if game["caja_abierta"]:
                return "La caja fuerte ya está abierta."
            return (
                "La caja fuerte tiene un disco de combinación de 4 números. "
                "Escribí 'usar' seguido del código, por ejemplo: usar 1234."
            )
        if target.isdigit() and len(target) == 4:
            if game["caja_abierta"]:
                return "La caja fuerte ya está abierta."
            if target == "1503":
                game["caja_abierta"] = True
                room_data = game["rooms"]["deposito"]
                room_data["items"]["llave"] = "Una llave chica."
                room_data["items"]["pagina1"] = "Una página de diario."
                return "Marcás 1503 en el disco... ¡clic! La caja fuerte se abre. Adentro hay una llave chica y una página de diario."
            return "Marcás ese código en el disco... no pasa nada. Ese no es."

    if target == "llave" and current_room == "dormitorio":
        if "llave" not in game["inventory"]:
            return "No tenés ninguna llave."
        if game["ropero_abierto"]:
            return "El ropero ya está abierto."
        game["ropero_abierto"] = True
        room_data = game["rooms"]["dormitorio"]
        room_data["items"]["pagina2"] = "Una página de diario."
        return "La llave entra justo. El ropero se abre: adentro hay una página de diario."

    return "No podés usar eso acá."

def arreglar_linterna(game):
    """Combina la pila con la linterna."""
    if game["linterna_arreglada"]:
        return "La linterna ya está arreglada y prendida."
    if "linterna" not in game["inventory"]:
        return "No tenés ninguna linterna."
    if "pila" not in game["inventory"]:
        return "La linterna necesita una pila para funcionar."
    game["linterna_arreglada"] = True
    return "Le ponés la pila a la linterna. Prende con una luz amarilla, débil pero sirve."

def conectar_cable(color, game):
    """Maneja el puzzle de reconectar los cables de la antena en orden."""
    if game["current_room"] != "sala_equipos":
        return "No hay ningún cable para conectar acá."
    if color not in CABLE_ORDEN:
        return "No hay un cable de ese color acá."
    if game["cableado_resuelto"]:
        return "Los cables ya están bien conectados."

    esperado = CABLE_ORDEN[game["cableado_progreso"]]
    if color == esperado:
        game["cableado_progreso"] += 1
        if game["cableado_progreso"] == len(CABLE_ORDEN):
            game["cableado_resuelto"] = True
            return "Conectás el último cable. Los paneles se iluminan: el cableado está resuelto. Ahora se puede subir a la torre."
        return f"Clic. Ese cable enganchó bien. Llevás {game['cableado_progreso']}/{len(CABLE_ORDEN)}."
    else:
        game["cableado_progreso"] = 0
        return "Salta una chispa y se corta todo de nuevo. Mejor empezá de cero con el orden."

def prender_breaker(target, game):
    """Maneja el puzzle del panel de llaves térmicas del sótano."""
    if game["current_room"] != "sotano":
        return "No hay ningún breaker acá."
    if not game["linterna_arreglada"]:
        return "Está muy oscuro para encontrar nada."
    if game["breaker_resuelto"]:
        return "El breaker correcto ya está prendido."

    if target == "breaker 2":
        return "Justo de eso te advertía el cartel del dormitorio. Mejor no."
    if target in ["breaker 1", "breaker 3", "breaker 4"]:
        if target == "breaker 3":
            game["breaker_resuelto"] = True
            return "Prendés el breaker 3. Las luces del sótano titilan y se encienden del todo."
        return "Prendés esa llave térmica. No pasa nada visible."

    return "No podés usar eso acá."

def decidir_final(accion, game):
    """Maneja la decisión final en la torre."""
    if game["current_room"] != "torre":
        return "Acá no hay ningún control para eso."
    if game["final_decidido"] is not None:
        return "Ya tomaste una decisión. Volvé a la entrada para salir."

    if accion == "cortar":
        game["final_decidido"] = "cortar"
        return (
            "Bajás la palanca. La transmisión se corta de golpe. Silencio total.\n"
            "Ahora podés volver a la entrada e intentar abrir la puerta principal."
        )
    else:
        game["final_decidido"] = "dejar"
        return (
            "Decidís dejarla transmitir. La antena sigue zumbando bajito, como siempre.\n"
            "Ahora podés volver a la entrada e intentar abrir la puerta principal."
        )

def leer_diario(game):
    """Junta las páginas encontradas del diario, si hay suficientes."""
    paginas = [p for p in ["pagina1", "pagina2", "pagina3"] if p in game["inventory"]]
    if len(paginas) == 0:
        return "No tenés ninguna página del diario."
    if len(paginas) < 3:
        return f"Tenés {len(paginas)} de 3 páginas sueltas. Te faltan más para que tenga sentido."
    return (
        "Juntás las 3 páginas y las leés en orden:\n"
        "Ezequiel encontró una frecuencia \"fantasma\" que repetía un mensaje viejo en loop. "
        "Volvía cada noche a escucharla, cada vez más convencido de que había alguien del "
        "otro lado. La última anotación dice que esa noche pensaba \"contestar\".\n"
        "Nunca se supo más nada de él. La estación se quedó transmitiendo sola."
    )

def abrir_puerta_final(game):
    """Resuelve el intento de abrir la puerta principal en la entrada."""
    if game["final_decidido"] is None:
        return "La puerta está trabada, no cede ni un poco."
    if game["terminado"]:
        return "La puerta ya está abierta. Podés irte cuando quieras."

    game["terminado"] = True
    if game["final_decidido"] == "cortar":
        return (
            "La puerta cede sin esfuerzo, como si nunca hubiera estado trabada.\n"
            "Salís al campo. La estación por fin descansa. Quizás nunca sepas del todo "
            "qué le pasó a Ezequiel — pero al menos esa señal fantasma no va a confundir "
            "a nadie más.\n"
            "🎉 ¡Completaste la Estación El Jagüel! 🎉"
        )
    else:
        return (
            "La puerta cede sin esfuerzo, como si nunca hubiera estado trabada.\n"
            "Salís al campo. Mientras te alejás, por la radio del cinto escuchás, bajito, "
            "una voz que repite: \"Acá Estación El Jagüel, todo en orden, cambio.\"\n"
            "Sonreís. Quizás Ezequiel todavía anda por ahí, en alguna frecuencia.\n"
            "🎉 ¡Completaste la Estación El Jagüel! 🎉"
        )
