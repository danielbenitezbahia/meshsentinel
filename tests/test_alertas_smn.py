"""Tests para modules/alertas_smn.py.

Cubre: caso sin alertas (fallback exacto pedido por el usuario), caso con
una o varias alertas (reusa build_smn_messages_by_partido del bbs_system,
no duplica formato), volver con 0/cd .., y que cualquier otra entrada
vuelva a consultar en vivo (no cachea el resultado). No depende de
meshtastic/pubsub -- el modulo es puro y solo toca lo que bbs_system le
devuelve.
"""
import importlib.util
import os
import unittest

MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "modules", "alertas_smn.py"
)
spec = importlib.util.spec_from_file_location("alertas_smn", MODULE_PATH)
alertas_smn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alertas_smn)


class FakeBBS:
    """Stub minimo: solo necesita build_smn_messages_by_partido(), igual
    que el metodo real de BBSSystem que ya usa el broadcast al canal."""

    def __init__(self, messages=None):
        self.users = {}
        self._messages = messages if messages is not None else []

    def build_smn_messages_by_partido(self):
        return self._messages


class TestSinAlertas(unittest.TestCase):
    def test_display_menu_sin_alertas(self):
        bbs = FakeBBS(messages=[])
        out = alertas_smn.display_menu(bbs)
        self.assertIn(
            "No hay alertas vigentes para las próximas 72hs. en el Sudoeste Bonaerense.",
            out,
        )
        self.assertIn("0. ↩ Volver", out)

    def test_process_command_sin_alertas_cualquier_entrada(self):
        bbs = FakeBBS(messages=[])
        out = alertas_smn.process_command("!u", "1", bbs)
        self.assertIn("No hay alertas vigentes", out)


class TestConAlertas(unittest.TestCase):
    def test_display_menu_con_una_alerta(self):
        bbs = FakeBBS(messages=["⚡🚨 ALERTAS SMN 🚨⚡\n── Reporte 14hs · 72hs ──\n\n📍 BAHIA BLANCA\n..."])
        out = alertas_smn.display_menu(bbs)
        self.assertIn("ALERTAS SMN", out)
        self.assertIn("BAHIA BLANCA", out)
        self.assertIn("0. ↩ Volver", out)
        self.assertNotIn("No hay alertas vigentes", out)

    def test_display_menu_con_varios_partidos_usa_mismo_separador_que_el_canal(self):
        bbs = FakeBBS(messages=["📍 BAHIA BLANCA\n...", "📍 TORNQUIST\n..."])
        out = alertas_smn.display_menu(bbs)
        self.assertIn("BAHIA BLANCA\n...\n---\n📍 TORNQUIST", out)

    def test_process_command_refresca_en_vivo_no_cachea(self):
        bbs = FakeBBS(messages=["📍 BAHIA BLANCA\n..."])
        out1 = alertas_smn.process_command("!u", "1", bbs)
        self.assertIn("BAHIA BLANCA", out1)
        bbs._messages = []  # la alerta se resolvio entre un llamado y el otro
        out2 = alertas_smn.process_command("!u", "1", bbs)
        self.assertIn("No hay alertas vigentes", out2)
        self.assertNotIn("BAHIA BLANCA", out2)


class TestVolver(unittest.TestCase):
    def test_back_with_0(self):
        bbs = FakeBBS(messages=["algo"])
        self.assertEqual(alertas_smn.process_command("!u", "0", bbs), "__back__")

    def test_back_with_cd_dotdot(self):
        bbs = FakeBBS(messages=["algo"])
        self.assertEqual(alertas_smn.process_command("!u", "cd ..", bbs), "__back__")
        self.assertEqual(alertas_smn.process_command("!u", "CD ..", bbs), "__back__")


if __name__ == "__main__":
    unittest.main()
