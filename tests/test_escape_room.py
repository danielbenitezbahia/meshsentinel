"""Tests para modules/Juegos/escape_room.py.

Cubre todos los caminos jugables: el camino principal (mesa -> cajon ->
llave -> desbloquear puerta -> escapar), el camino secundario (estanteria ->
nota -> desbloquear cofre), y los casos borde (comandos invalidos, objetos
inexistentes, condiciones no cumplidas, alias en ingles, persistencia de
estado por usuario). No depende de meshtastic/pubsub -- el modulo del juego
es puro.
"""
import importlib.util
import os
import sys
import unittest

MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "modules", "Juegos", "escape_room.py"
)
spec = importlib.util.spec_from_file_location("escape_room", MODULE_PATH)
escape_room = importlib.util.module_from_spec(spec)
spec.loader.exec_module(escape_room)


class FakeBBS:
    """Stub minimo de bbs_system: el juego solo toca .users."""

    def __init__(self):
        self.users = {}


class EscapeRoomTestBase(unittest.TestCase):
    def setUp(self):
        self.bbs = FakeBBS()
        self.user = "!testuser"

    def send(self, command, user=None):
        return escape_room.process_command(user or self.user, command, self.bbs)

    def game(self, user=None):
        return self.bbs.users[user or self.user]["escape_room"]


class TestBackAndInit(EscapeRoomTestBase):
    def test_back_with_0(self):
        self.assertEqual(self.send("0"), "__back__")

    def test_back_with_cd_dotdot(self):
        self.assertEqual(self.send("cd .."), "__back__")
        self.assertEqual(self.send("CD .."), "__back__")

    def test_first_command_initializes_game(self):
        self.send("examinar")
        game = self.game()
        self.assertEqual(game["current_room"], "start")
        self.assertEqual(game["inventory"], [])
        self.assertFalse(game["door_unlocked"])
        self.assertFalse(game["chest_unlocked"])
        self.assertFalse(game["escaped"])

    def test_state_persists_across_calls_same_user(self):
        self.send("examinar mesa")
        self.send("examinar cajon")
        self.assertIn("llave", self.game()["rooms"]["start"]["items"])
        # Llamar de nuevo no debe reinicializar el juego
        self.send("inventario")
        self.assertIn("llave", self.game()["rooms"]["start"]["items"])

    def test_independent_state_per_user(self):
        self.send("examinar cajon", user="!a")
        self.send("recoger llave", user="!a")
        self.assertIn("llave", self.game(user="!a")["inventory"])
        self.assertNotIn("escape_room", self.bbs.users.get("!b", {}))
        self.send("examinar", user="!b")
        self.assertEqual(self.game(user="!b")["inventory"], [])


class TestMovementAndExamine(EscapeRoomTestBase):
    def test_examine_room_no_target(self):
        out = self.send("examinar")
        self.assertIn("Estás en una habitación cerrada con llave", out)
        self.assertIn("Salidas: norte, este", out)

    def test_look_english_no_longer_recognized(self):
        # El juego quedo 100% en espanol: "look" ya no es un alias valido.
        out = self.send("look")
        self.assertIn("Comando inválido", out)

    def test_examine_unknown_object(self):
        self.assertEqual(self.send("examinar marciano"), "No ves eso acá.")

    def test_move_to_room_without_that_exit(self):
        self.assertEqual(self.send("sur"), "No podés ir hacia ahí.")

    def test_move_este_to_hidden_room(self):
        out = self.send("este")
        self.assertIn("habitación secreta", out)
        self.assertEqual(self.game()["current_room"], "hidden_room")

    def test_move_oeste_back_from_hidden_room(self):
        self.send("este")
        out = self.send("oeste")
        self.assertIn("habitación cerrada con llave", out)
        self.assertEqual(self.game()["current_room"], "start")

    def test_move_norte_blocked_before_unlock(self):
        out = self.send("norte")
        self.assertIn("cerrada con llave", out)
        self.assertEqual(self.game()["current_room"], "start")

    def test_movement_prefix_ir(self):
        self.send("examinar cajon")
        self.send("recoger llave")
        self.send("usar llave")
        out_ir = self.send("ir norte")
        self.assertIn("Escapaste", out_ir)
        self.send("sur")
        out_ir2 = self.send("ir norte")
        self.assertIn("Te moviste hacia el norte", out_ir2)

    def test_movement_prefixes_go_move_no_longer_recognized(self):
        # Antes se aceptaban "go "/"move " como alias en ingles; ahora
        # deben tratarse como accion desconocida ("go" / "move" no son
        # comandos validos, "norte" queda pegado como target).
        out_go = self.send("go norte")
        self.assertIn("Comando inválido", out_go)
        out_move = self.send("move norte")
        self.assertIn("Comando inválido", out_move)
        self.assertEqual(self.game()["current_room"], "start")


class TestMainQuestKeyAndDoor(EscapeRoomTestBase):
    def test_examine_mesa_hints_cajon(self):
        out = self.send("examinar mesa")
        self.assertIn("cajón", out)

    def test_cajon_not_examinable_until_in_objects(self):
        # Antes del fix esto fallaba siempre (bug de objeto inalcanzable).
        out = self.send("examinar cajon")
        self.assertIn("llave oxidada", out)
        self.assertIn("llave", self.game()["rooms"]["start"]["items"])

    def test_cajon_examine_twice_before_pickup_is_idempotent(self):
        self.send("examinar cajon")
        out = self.send("examinar cajon")
        self.assertIn("llave oxidada", out)  # sigue anunciando, no rompe

    def test_cajon_empty_after_pickup(self):
        self.send("examinar cajon")
        self.send("recoger llave")
        out = self.send("examinar cajon")
        self.assertEqual(out, "El cajón está vacío.")

    def test_pick_up_llave_before_revealed_fails(self):
        out = self.send("recoger llave")
        self.assertEqual(out, "No podés recoger eso.")
        self.assertNotIn("llave", self.game()["inventory"])

    def test_recoger_llave_after_reveal(self):
        self.send("examinar cajon")
        out = self.send("recoger llave")
        self.assertEqual(out, "Recogiste: llave.")
        self.assertIn("llave", self.game()["inventory"])
        self.assertNotIn("llave", self.game()["rooms"]["start"]["items"])

    def test_pick_up_english_no_longer_recognized(self):
        self.send("examinar cajon")
        out = self.send("pick up llave")
        self.assertIn("Comando inválido", out)
        self.assertNotIn("llave", self.game()["inventory"])

    def test_usar_llave_without_inventory_fails(self):
        out = self.send("usar llave")
        self.assertEqual(out, "No podés usar eso acá.")
        self.assertFalse(self.game()["door_unlocked"])

    def test_usar_llave_wrong_room_fails(self):
        self.send("examinar cajon")
        self.send("recoger llave")
        self.send("este")  # hidden_room, no "start"
        out = self.send("usar llave")
        self.assertEqual(out, "No podés usar eso acá.")
        self.assertFalse(self.game()["door_unlocked"])

    def test_usar_llave_unlocks_door_in_start(self):
        self.send("examinar cajon")
        self.send("recoger llave")
        out = self.send("usar llave")
        self.assertIn("desbloquear la puerta", out)
        self.assertTrue(self.game()["door_unlocked"])

    def test_use_english_no_longer_recognized(self):
        self.send("examinar cajon")
        self.send("recoger llave")
        out = self.send("use llave")
        self.assertIn("Comando inválido", out)
        self.assertFalse(self.game()["door_unlocked"])

    def test_examine_puerta_desde_start_antes_de_desbloquear(self):
        # Reporte real de playtesting: "start" describe "una puerta al
        # norte" pero antes el objeto "puerta" solo existia en locked_door
        # (inalcanzable sin desbloquearla primero) -- quedaba inexaminable
        # desde la propia sala que la menciona.
        out = self.send("examinar puerta")
        self.assertIn("cerrada con llave", out)

    def test_examine_puerta_reflects_unlocked_state(self):
        self.send("examinar cajon")
        self.send("recoger llave")
        self.send("usar llave")
        self.send("norte")  # escapa, current_room ahora es locked_door
        out = self.send("examinar puerta")
        self.assertIn("desbloqueada", out)

    def test_examinar_cerradura_en_start_es_alias_de_puerta(self):
        out = self.send("examinar cerradura")
        self.assertIn("cerrada con llave", out)

    def test_abrir_cajon_es_alias_de_examinar(self):
        # Reporte real de playtesting: "examinar mesa" dice literalmente
        # "deberias abrir el cajon", pero "abrir" no estaba implementado.
        out = self.send("abrir cajon")
        self.assertIn("llave oxidada", out)
        self.assertIn("llave", self.game()["rooms"]["start"]["items"])

    def test_abrir_puerta_es_alias_de_examinar(self):
        out = self.send("abrir puerta")
        self.assertIn("cerrada con llave", out)

    def test_examinar_cajon_con_tilde_funciona_igual(self):
        # El juego muestra "cajón" con tilde en su propio texto; si el
        # jugador lo copia tal cual, debe apuntar al mismo objeto.
        out = self.send("examinar cajón")
        self.assertIn("llave oxidada", out)


class TestCuadroPuzzle(EscapeRoomTestBase):
    # "examinar mesa" ya decia "deberias abrir el cajon" (ahora resuelto via
    # el alias "abrir"); el cuadro tenia la misma clase de pista colgada
    # ("deberias acomodarlo") sin ningun comando "acomodar" implementado.
    def test_examine_cuadro_antes_de_acomodar(self):
        out = self.send("examinar cuadro")
        self.assertIn("deberías acomodarlo", out)

    def test_acomodar_cuadro_revela_moneda(self):
        out = self.send("acomodar cuadro")
        self.assertIn("moneda", out)
        self.assertIn("moneda", self.game()["rooms"]["start"]["items"])

    def test_examine_cuadro_despues_de_acomodar_sin_agarrar_moneda(self):
        self.send("acomodar cuadro")
        out = self.send("examinar cuadro")
        self.assertIn("hueco con una moneda", out)

    def test_acomodar_cuadro_dos_veces_no_duplica_moneda(self):
        self.send("acomodar cuadro")
        self.send("acomodar cuadro")
        self.assertEqual(
            list(self.game()["rooms"]["start"]["items"].keys()), ["moneda"]
        )
        self.assertNotIn("moneda", self.game()["inventory"])

    def test_recoger_moneda_despues_de_acomodar(self):
        self.send("acomodar cuadro")
        out = self.send("recoger moneda")
        self.assertEqual(out, "Recogiste: moneda.")
        self.assertIn("moneda", self.game()["inventory"])

    def test_recoger_moneda_antes_de_acomodar_falla(self):
        out = self.send("recoger moneda")
        self.assertEqual(out, "No podés recoger eso.")

    def test_examine_cuadro_despues_de_agarrar_moneda(self):
        self.send("acomodar cuadro")
        self.send("recoger moneda")
        out = self.send("examinar cuadro")
        self.assertEqual(out, "El cuadro, ahora derecho, tapa un hueco vacío en la pared.")

    def test_acomodar_cuadro_despues_de_agarrar_moneda(self):
        self.send("acomodar cuadro")
        self.send("recoger moneda")
        out = self.send("acomodar cuadro")
        self.assertEqual(out, "El cuadro ya está derecho.")

    def test_acomodar_cuadro_fuera_de_start_falla(self):
        self.send("este")  # hidden_room, no tiene cuadro
        out = self.send("acomodar cuadro")
        self.assertEqual(out, "No ves eso acá.")

    def test_acomodar_no_hace_nada_con_otro_objeto(self):
        out = self.send("acomodar mesa")
        self.assertIn("Comando inválido", out)

    def test_moneda_no_afecta_el_resto_del_juego(self):
        # Es un bonus 100% opcional: no debe interferir con la puerta ni el cofre.
        self.send("acomodar cuadro")
        self.send("recoger moneda")
        self.send("examinar cajon")
        self.send("recoger llave")
        out = self.send("usar llave")
        self.assertTrue(self.game()["door_unlocked"])
        self.assertIn("desbloquear la puerta", out)


class TestEscapeEnding(EscapeRoomTestBase):
    def _unlock_door(self):
        self.send("examinar cajon")
        self.send("recoger llave")
        self.send("usar llave")

    def test_full_happy_path_escapes(self):
        self._unlock_door()
        out = self.send("norte")
        self.assertIn("Escapaste de la habitación", out)
        self.assertTrue(self.game()["escaped"])
        self.assertEqual(self.game()["current_room"], "locked_door")

    def test_escape_message_only_once(self):
        self._unlock_door()
        self.send("norte")
        self.send("sur")
        out = self.send("norte")
        self.assertNotIn("Escapaste", out)
        self.assertIn("Te moviste hacia el norte", out)

    def test_examinar_cerradura_en_locked_door_es_alias_de_puerta(self):
        self._unlock_door()
        self.send("norte")  # locked_door
        out = self.send("examinar cerradura")
        self.assertIn("desbloqueada", out)


class TestSideQuestChest(EscapeRoomTestBase):
    def setUp(self):
        super().setUp()
        self.send("este")  # entrar a hidden_room

    def test_examine_cofre_locked(self):
        out = self.send("examinar cofre")
        self.assertIn("cerradura de combinación", out)

    def test_examine_estanteria_reveals_nota(self):
        out = self.send("examinar estanteria")
        self.assertIn("combinación", out)
        self.assertIn("nota", self.game()["rooms"]["hidden_room"]["items"])

    def test_estanteria_flavor_text_after_reveal(self):
        self.send("examinar estanteria")
        self.send("recoger nota")
        out = self.send("examinar estanteria")
        self.assertEqual(out, "Una estantería polvorienta llena de libros variados.")

    def test_recoger_nota_before_reveal_fails(self):
        out = self.send("recoger nota")
        self.assertEqual(out, "No podés recoger eso.")

    def test_usar_nota_wrong_room_fails(self):
        self.send("examinar estanteria")
        self.send("recoger nota")
        self.send("oeste")  # start, no "hidden_room"
        out = self.send("usar nota")
        self.assertEqual(out, "No podés usar eso acá.")
        self.assertFalse(self.game()["chest_unlocked"])

    def test_usar_nota_unlocks_chest(self):
        self.send("examinar estanteria")
        self.send("recoger nota")
        out = self.send("usar nota")
        self.assertIn("cofre se abre", out)
        self.assertTrue(self.game()["chest_unlocked"])

    def test_usar_nota_twice_reports_already_open(self):
        self.send("examinar estanteria")
        self.send("recoger nota")
        self.send("usar nota")
        out = self.send("usar nota")
        self.assertEqual(out, "El cofre ya está abierto.")

    def test_examine_cofre_after_unlock_shows_gem(self):
        self.send("examinar estanteria")
        self.send("recoger nota")
        self.send("usar nota")
        out = self.send("examinar cofre")
        self.assertIn("gema brillante", out)

    def test_chest_quest_independent_of_door_quest(self):
        # Resolver el cofre no debe afectar el estado de la puerta ni viceversa.
        self.send("examinar estanteria")
        self.send("recoger nota")
        self.send("usar nota")
        self.assertFalse(self.game()["door_unlocked"])

    def test_examinar_libro_antes_de_estanteria_es_alias(self):
        # Reporte real de playtesting: "examinar estanteria" dice "un libro
        # parece fuera de lugar", y el jugador prueba con esa palabra.
        out = self.send("examinar libro")
        self.assertIn("combinación", out)
        self.assertIn("nota", self.game()["rooms"]["hidden_room"]["items"])

    def test_recoger_libro_es_alias_de_nota(self):
        # Reporte real de playtesting: "recoger libro" no hacia nada porque
        # el objeto interno se llama "nota", no "libro".
        self.send("examinar estanteria")
        out = self.send("recoger libro")
        self.assertEqual(out, "Recogiste: nota.")
        self.assertIn("nota", self.game()["inventory"])

    def test_recoger_libro_antes_de_revelar_falla(self):
        out = self.send("recoger libro")
        self.assertEqual(out, "No podés recoger eso.")

    def test_usar_libro_es_alias_de_nota(self):
        self.send("examinar estanteria")
        self.send("recoger libro")
        out = self.send("usar libro")
        self.assertIn("cofre se abre", out)
        self.assertTrue(self.game()["chest_unlocked"])

    def test_examinar_estanteria_con_tilde_funciona_igual(self):
        out = self.send("examinar estantería")
        self.assertIn("combinación", out)
        self.assertIn("nota", self.game()["rooms"]["hidden_room"]["items"])

    def test_abrir_estanteria_es_alias_de_examinar(self):
        out = self.send("abrir estanteria")
        self.assertIn("combinación", out)
        self.assertIn("nota", self.game()["rooms"]["hidden_room"]["items"])

    def test_abrir_cofre_es_alias_de_examinar(self):
        out = self.send("abrir cofre")
        self.assertIn("cerradura de combinación", out)

    def test_examinar_cerradura_en_hidden_room_es_alias_de_cofre(self):
        # La misma palabra "cerradura" tambien aparece en la descripcion
        # del cofre -- en esta sala debe apuntar al cofre, no a la puerta.
        out = self.send("examinar cerradura")
        self.assertIn("cerradura de combinación", out)

    def test_recoger_gema_despues_de_abrir_cofre(self):
        # Antes del fix, la gema se mencionaba ("Adentro ves una gema
        # brillante") pero nunca se agregaba a los items de la sala --
        # "recoger gema" fallaba siempre, sin forma de completar el botin.
        self.send("examinar estanteria")
        self.send("recoger nota")
        self.send("usar nota")
        out = self.send("recoger gema")
        self.assertEqual(out, "Recogiste: gema.")
        self.assertIn("gema", self.game()["inventory"])

    def test_recoger_gema_antes_de_abrir_cofre_falla(self):
        out = self.send("recoger gema")
        self.assertEqual(out, "No podés recoger eso.")

    def test_examine_cofre_despues_de_agarrar_gema(self):
        self.send("examinar estanteria")
        self.send("recoger nota")
        self.send("usar nota")
        self.send("recoger gema")
        out = self.send("examinar cofre")
        self.assertEqual(out, "El cofre está abierto y vacío. Ya agarraste la gema.")


class TestInventoryAndFallback(EscapeRoomTestBase):
    def test_inventory_empty(self):
        self.assertEqual(self.send("inventario"), "Inventario: vacío")

    def test_inventory_english_no_longer_recognized(self):
        out = self.send("inventory")
        self.assertIn("Comando inválido", out)

    def test_inventory_with_items(self):
        self.send("examinar cajon")
        self.send("recoger llave")
        self.assertEqual(self.send("inventario"), "Inventario: llave")

    def test_unknown_command_shows_help(self):
        out = self.send("volar")
        self.assertIn("Comando inválido", out)
        self.assertIn("Movimiento: norte, sur, este, oeste", out)
        self.assertIn("Recoger: recoger <objeto>", out)
        self.assertIn("Usar: usar <objeto>", out)

    def test_help_command_shows_help(self):
        out = self.send("help")
        self.assertIn("Comando inválido", out)

    def test_recoger_without_target_falls_through_to_help(self):
        out = self.send("recoger")
        self.assertIn("Comando inválido", out)

    def test_usar_without_target_falls_through_to_help(self):
        out = self.send("usar")
        self.assertIn("Comando inválido", out)

    def test_pick_up_nonexistent_item(self):
        out = self.send("recoger silla")
        self.assertEqual(out, "No podés recoger eso.")

    def test_use_item_not_applicable(self):
        out = self.send("usar mesa")
        self.assertEqual(out, "No podés usar eso acá.")


if __name__ == "__main__":
    unittest.main()
