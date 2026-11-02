"""Tests para bbs_system.py. Por ahora solo build_smn_messages_by_partido:
es el resumen que se manda por LoRa a las 11 y 18hs (canal SMN_Alertas), y
el 2026-11-02 se refactorizó para delegar el agrupado/color/orden a
weather_alert_service.get_grouped_alerts() en vez de duplicarlo -- este
test fija el formato de texto exacto ANTES y DESPUÉS del refactor para
confirmar que no cambió ni un carácter (es una emisión real, dos veces
por día, no hay margen para romperla en silencio).

meshtastic/pubsub no están instalados en el entorno de test, igual que en
test_interface.py: se instalan stubs mínimos antes de importar bbs_system.
build_smn_messages_by_partido no toca self para nada (ni self.interface ni
self.users), así que se llama sobre un BBSSystem construido con
object.__new__ -- evita correr __init__ entero (que abre el puerto serie
real) solo para probar un método de formato de texto puro.
"""
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def _install_fake_meshtastic():
    if "meshtastic" in sys.modules:
        return

    meshtastic_mod = types.ModuleType("meshtastic")
    serial_mod = types.ModuleType("meshtastic.serial_interface")
    serial_mod.SerialInterface = MagicMock(name="SerialInterface")
    protobuf_mod = types.ModuleType("meshtastic.protobuf")
    mesh_pb2_mod = types.ModuleType("meshtastic.protobuf.mesh_pb2")

    class _FakeRouteDiscovery:
        def SerializeToString(self):
            return b""

    mesh_pb2_mod.RouteDiscovery = _FakeRouteDiscovery
    portnums_pb2_mod = types.ModuleType("meshtastic.protobuf.portnums_pb2")

    class _PortNum:
        UNKNOWN_APP = 0
        TRACEROUTE_APP = 70

    portnums_pb2_mod.PortNum = _PortNum
    protobuf_mod.mesh_pb2 = mesh_pb2_mod
    protobuf_mod.portnums_pb2 = portnums_pb2_mod

    pubsub_mod = types.ModuleType("pubsub")
    pub_mod = types.ModuleType("pubsub.pub")
    pub_mod.subscribe = MagicMock()
    pub_mod.sendMessage = MagicMock()
    pubsub_mod.pub = pub_mod

    sys.modules["meshtastic"] = meshtastic_mod
    sys.modules["meshtastic.serial_interface"] = serial_mod
    sys.modules["meshtastic.protobuf"] = protobuf_mod
    sys.modules["meshtastic.protobuf.mesh_pb2"] = mesh_pb2_mod
    sys.modules["meshtastic.protobuf.portnums_pb2"] = portnums_pb2_mod
    sys.modules["pubsub"] = pubsub_mod
    sys.modules["pubsub.pub"] = pub_mod


_install_fake_meshtastic()

import bbs_system  # noqa: E402  (después de instalar los stubs de arriba)
import weather_alert_service  # noqa: E402


ALERTS_FIXTURE = [
    {
        "title": "Tormentas fuertes", "severity": "Severe",
        "onset": "2026-11-02T10:00:00-03:00", "expires": "2026-11-02T14:00:00-03:00",
        "affected_partidos": ["Bahia Blanca", "Tornquist"],
    },
    {
        "title": "Viento", "severity": "Moderate",
        "onset": "2026-11-02T08:00:00-03:00", "expires": "2026-11-02T12:00:00-03:00",
        "affected_partidos": ["Coronel Dorrego"],
    },
]

EXPECTED_MESSAGES = [
    "⚡🚨 ALERTAS SMN 🚨⚡\n"
    "── Reporte 11hs · 72hs ──\n"
    "\n"
    "📍 BAHIA BLANCA\n"
    "· · · · · · · · · ·\n"
    "🟠⛈ TORMENTAS FUERTES\n"
    "🕐 lunes mañana/tarde",

    "📍 CORONEL DORREGO\n"
    "· · · · · · · · · ·\n"
    "🟡💨 VIENTO\n"
    "🕐 lunes mañana/tarde",

    "📍 TORNQUIST\n"
    "· · · · · · · · · ·\n"
    "🟠⛈ TORMENTAS FUERTES\n"
    "🕐 lunes mañana/tarde",
]


class BuildSmnMessagesByPartidoTests(unittest.TestCase):
    def setUp(self):
        self.bbs = object.__new__(bbs_system.BBSSystem)

    def test_formato_exacto_se_mantiene_tras_el_refactor(self):
        with patch.object(weather_alert_service, "get_sudoeste_ba_alerts", return_value=ALERTS_FIXTURE):
            messages = self.bbs.build_smn_messages_by_partido(hour=11)
        self.assertEqual(messages, EXPECTED_MESSAGES)

    def test_sin_alertas_devuelve_lista_vacia(self):
        with patch.object(weather_alert_service, "get_sudoeste_ba_alerts", return_value=[]):
            messages = self.bbs.build_smn_messages_by_partido(hour=11)
        self.assertEqual(messages, [])

    def test_header_solo_en_el_primer_mensaje(self):
        with patch.object(weather_alert_service, "get_sudoeste_ba_alerts", return_value=ALERTS_FIXTURE):
            messages = self.bbs.build_smn_messages_by_partido(hour=18)
        self.assertIn("ALERTAS SMN", messages[0])
        self.assertIn("Reporte 18hs", messages[0])
        for msg in messages[1:]:
            self.assertNotIn("ALERTAS SMN", msg)

    def test_hour_none_usa_la_hora_actual(self):
        with patch.object(weather_alert_service, "get_sudoeste_ba_alerts", return_value=ALERTS_FIXTURE), \
             patch("bbs_system.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 18
            messages = self.bbs.build_smn_messages_by_partido()
        self.assertIn("Reporte 18hs", messages[0])


if __name__ == "__main__":
    unittest.main()
