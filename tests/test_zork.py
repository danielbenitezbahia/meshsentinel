"""Tests para modules/Juegos/zork.py (version en espanol).

Cubre todos los caminos jugables: el mapa completo campo -> bosque -> claro
-> cueva (incluido el final/victoria), cada comando invalido/callejon sin
salida en cada ubicacion, la pantalla previa de inicio ("1"/"2"/cd ..),
persistencia de estado por usuario, y que el comando de ayuda funcione en
cualquier ubicacion. No depende de meshtastic/pubsub -- el modulo del juego
es puro.
"""
import importlib.util
import os
import unittest

MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "modules", "Juegos", "zork.py"
)
spec = importlib.util.spec_from_file_location("zork", MODULE_PATH)
zork = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zork)


class FakeBBS:
    """Stub minimo de bbs_system: el juego solo toca .users."""

    def __init__(self):
        self.users = {}


class ZorkTestBase(unittest.TestCase):
    def setUp(self):
        self.bbs = FakeBBS()
        self.user = "!testuser"

    def send(self, command, user=None):
        return zork.process_command(user or self.user, command, self.bbs)

    def start(self, user=None):
        """Atraviesa la pantalla previa y arranca la partida."""
        return self.send("1", user=user)

    def game(self, user=None):
        return self.bbs.users[user or self.user]["zork_game"]


class TestPreGameScreen(ZorkTestBase):
    def test_back_with_2(self):
        self.assertEqual(self.send("2"), "__back__")

    def test_back_with_cd_dotdot(self):
        self.assertEqual(self.send("cd .."), "__back__")
        self.assertEqual(self.send("CD .."), "__back__")

    def test_invalid_choice_before_start(self):
        out = self.send("3")
        self.assertIn("Opción inválida", out)
        self.assertNotIn("zork_game", self.bbs.users.get(self.user, {}))

    def test_start_with_1_shows_field(self):
        out = self.start()
        self.assertIn("campo abierto al oeste de una casa blanca", out)
        self.assertEqual(self.game()["location"], "field")

    def test_independent_state_per_user(self):
        self.start(user="!a")
        self.send("ir al sudoeste", user="!a")
        self.assertEqual(self.game(user="!a")["location"], "forest")
        self.assertNotIn("zork_game", self.bbs.users.get("!b", {}))

    def test_state_persists_across_calls_same_user(self):
        self.start()
        self.send("ir al sudoeste")
        self.send("mirar")
        self.assertEqual(self.game()["location"], "forest")


class TestGlobalCommands(ZorkTestBase):
    def setUp(self):
        super().setUp()
        self.start()

    def test_back_mid_game(self):
        self.assertEqual(self.send("cd .."), "__back__")

    def test_help_works_at_every_location(self):
        # field
        out = self.send("ayuda")
        self.assertIn("Los comandos disponibles dependen de dónde estés", out)
        # forest
        self.send("ir al sudoeste")
        out = self.send("ayuda")
        self.assertIn("Los comandos disponibles dependen", out)
        # clearing
        self.send("ir al este")
        out = self.send("ayuda")
        self.assertIn("Los comandos disponibles dependen", out)
        # cave
        self.send("bajar por la reja")
        out = self.send("ayuda")
        self.assertIn("Los comandos disponibles dependen", out)


class TestFieldLocation(ZorkTestBase):
    def setUp(self):
        super().setUp()
        self.start()

    def test_look_describes_field(self):
        out = self.send("mirar")
        self.assertIn("puerta de entrada tapiada", out)
        self.assertIn("Buzón Pequeño", out)

    def test_look_alrededor_alias(self):
        out = self.send("mirar alrededor")
        self.assertIn("puerta de entrada tapiada", out)

    def test_tomar_buzon(self):
        self.assertEqual(self.send("tomar buzon"), "Está firmemente anclado.")

    def test_abrir_buzon_reveals_leaflet(self):
        out = self.send("abrir buzon")
        self.assertIn("folleto", out)

    def test_mirar_en_buzon_same_as_abrir(self):
        out = self.send("mirar en buzon")
        self.assertIn("folleto", out)

    def test_ir_al_este_blocked_by_door(self):
        out = self.send("ir al este")
        self.assertIn("tapiada", out)
        self.assertEqual(self.game()["location"], "field")

    def test_abrir_puerta_fails(self):
        self.assertEqual(self.send("abrir puerta"), "La puerta no se puede abrir.")

    def test_tomar_tablas(self):
        self.assertEqual(self.send("tomar tablas"), "Las tablas están firmemente aseguradas.")

    def test_mirar_casa(self):
        out = self.send("mirar casa")
        self.assertIn("casa colonial", out)

    def test_examinar_casa_alias(self):
        out = self.send("examinar casa")
        self.assertIn("casa colonial", out)

    def test_leer_folleto(self):
        out = self.send("leer folleto")
        self.assertIn("Estatua de Jade", out)
        self.assertEqual(self.game()["location"], "field")  # no mueve de lugar

    def test_invalid_command_in_field(self):
        out = self.send("bailar")
        self.assertIn("Comando inválido", out)
        self.assertIn("sudoeste", out)

    def test_ir_al_sudoeste_moves_to_forest(self):
        out = self.send("ir al sudoeste")
        self.assertIn("bosque", out)
        self.assertEqual(self.game()["location"], "forest")

    def test_ir_al_sendero_secreto_alias(self):
        out = self.send("ir al sendero secreto")
        self.assertIn("bosque", out)
        self.assertEqual(self.game()["location"], "forest")


class TestForestLocation(ZorkTestBase):
    def setUp(self):
        super().setUp()
        self.start()
        self.send("ir al sudoeste")

    def test_look_describes_forest(self):
        out = self.send("mirar")
        self.assertIn("árboles en todas direcciones", out)

    def test_look_alrededor_alias(self):
        out = self.send("mirar alrededor")
        self.assertIn("árboles en todas direcciones", out)

    def test_ir_al_oeste_needs_machete(self):
        out = self.send("ir al oeste")
        self.assertIn("machete", out)
        self.assertEqual(self.game()["location"], "forest")

    def test_ir_al_norte_impenetrable(self):
        out = self.send("ir al norte")
        self.assertIn("impenetrable", out)
        self.assertEqual(self.game()["location"], "forest")

    def test_ir_al_sur_blocked_by_storm(self):
        out = self.send("ir al sur")
        self.assertIn("tormenta", out)
        self.assertEqual(self.game()["location"], "forest")

    def test_invalid_command_in_forest(self):
        out = self.send("bailar")
        self.assertIn("Comando inválido", out)
        self.assertIn("este", out)

    def test_ir_al_este_moves_to_clearing(self):
        out = self.send("ir al este")
        self.assertIn("claro", out)
        self.assertEqual(self.game()["location"], "clearing")


class TestClearingLocation(ZorkTestBase):
    def setUp(self):
        super().setUp()
        self.start()
        self.send("ir al sudoeste")
        self.send("ir al este")

    def test_look_describes_clearing(self):
        out = self.send("mirar")
        self.assertIn("reja abierta", out)

    def test_look_alrededor_alias(self):
        out = self.send("mirar alrededor")
        self.assertIn("reja abierta", out)

    def test_ir_al_sur_blocked_by_ogre(self):
        out = self.send("ir al sur")
        self.assertIn("ogro", out)
        self.assertEqual(self.game()["location"], "clearing")

    def test_invalid_command_in_clearing(self):
        out = self.send("bailar")
        self.assertIn("Comando inválido", out)
        self.assertIn("reja", out)

    def test_bajar_por_la_reja_moves_to_cave(self):
        out = self.send("bajar por la reja")
        self.assertIn("cueva", out)
        self.assertEqual(self.game()["location"], "cave")

    def test_ir_hacia_abajo_alias_moves_to_cave(self):
        out = self.send("ir hacia abajo")
        self.assertIn("cueva", out)
        self.assertEqual(self.game()["location"], "cave")


class TestCaveLocation(ZorkTestBase):
    def setUp(self):
        super().setUp()
        self.start()
        self.send("ir al sudoeste")
        self.send("ir al este")
        self.send("bajar por la reja")

    def test_look_describes_cave(self):
        out = self.send("mirar")
        self.assertIn("escalera oscura", out)
        self.assertIn("esqueleto", out)

    def test_look_alrededor_alias(self):
        out = self.send("mirar alrededor")
        self.assertIn("escalera oscura", out)

    def test_tomar_esqueleto_joke(self):
        out = self.send("tomar esqueleto")
        self.assertIn("enfermo", out)

    def test_romper_esqueleto_joke(self):
        out = self.send("romper esqueleto")
        self.assertIn("respeto", out)

    def test_iluminar_habitacion_needs_light_source(self):
        out = self.send("iluminar habitacion")
        self.assertIn("antorcha", out)

    def test_quebrar_esqueleto_joke(self):
        out = self.send("quebrar esqueleto")
        self.assertIn("Por qué", out)
        self.assertIn("qué", out.lower())

    def test_invalid_command_in_cave(self):
        out = self.send("bailar")
        self.assertIn("Comando inválido", out)
        self.assertIn("escalera", out)

    def test_bajar_la_escalera_wins_game(self):
        out = self.send("bajar la escalera")
        self.assertIn("Estatua de Jade", out)
        self.assertIn("completaste tu misión", out)

    def test_ir_hacia_abajo_alias_wins_game(self):
        out = self.send("ir hacia abajo")
        self.assertIn("Estatua de Jade", out)


class TestFullHappyPath(ZorkTestBase):
    def test_complete_walkthrough_field_to_victory(self):
        out = self.start()
        self.assertIn("campo abierto", out)

        out = self.send("abrir buzon")
        self.assertIn("folleto", out)

        out = self.send("leer folleto")
        self.assertIn("Estatua de Jade", out)

        out = self.send("mirar casa")
        self.assertIn("casa colonial", out)

        out = self.send("ir al sudoeste")
        self.assertIn("bosque", out)
        self.assertEqual(self.game()["location"], "forest")

        out = self.send("ir al oeste")
        self.assertIn("machete", out)

        out = self.send("ir al este")
        self.assertIn("claro", out)
        self.assertEqual(self.game()["location"], "clearing")

        out = self.send("ir al sur")
        self.assertIn("ogro", out)

        out = self.send("bajar por la reja")
        self.assertIn("cueva", out)
        self.assertEqual(self.game()["location"], "cave")

        out = self.send("iluminar habitacion")
        self.assertIn("antorcha", out)

        out = self.send("bajar la escalera")
        self.assertIn("Estatua de Jade", out)
        self.assertIn("completaste tu misión", out)


if __name__ == "__main__":
    unittest.main()
