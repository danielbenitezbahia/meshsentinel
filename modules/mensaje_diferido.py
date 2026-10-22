menu_name = "Mensaje Diferido"

def display_menu():
    return (
        "📬 Dejale un mensaje a cualquier nodo de la mesh, esté o no en rango ahora.\n"
        "El BBS lo guarda y se lo entrega apenas vuelva a aparecer.\n\n"
        "Comando (funciona desde cualquier pantalla, incluso parado acá):\n"
        "tell !nodeId tu mensaje\n\n"
        "Ejemplo:\n"
        "tell !a1b2c3d4 hola, nos vemos a las 20hs\n\n"
        "Te avisamos cuando se entregue.\n"
        "0. ↩ Volver"
    )

def process_command(user_id, command, bbs_system):
    """El comando 'tell' en si se maneja globalmente en handle_message, antes
    de llegar a cualquier modulo -- esta pantalla es solo informativa."""
    if command.strip() == "0" or command.strip().lower() == "cd ..":
        return "__back__"
    return display_menu()
