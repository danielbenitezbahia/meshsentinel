"""Tests para el envío no bloqueante de traceroute en interface.py.

meshtastic/pubsub no están instalados en el entorno de test (no hace falta
radio real), así que se instalan stubs mínimos en sys.modules antes de
importar interface.py.
"""
import inspect
import os
import re
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock


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

import interface  # noqa: E402  (después de instalar los stubs de arriba)
import traffic_stats  # noqa: E402  (sin deps externas, no necesita stub)

TRACEROUTE_APP = 70
TEST_NODE_ID = "!33695e54"


class SendTracerouteAsyncTests(unittest.TestCase):
    def setUp(self):
        self.iface = interface.Interface()
        self.iface.interface = MagicMock()

    def test_does_not_use_blocking_sendTraceRoute_method(self):
        """_send_traceroute no debe invocar el sendTraceRoute() bloqueante del SDK."""
        self.iface._send_traceroute(TEST_NODE_ID)
        self.iface.interface.sendTraceRoute.assert_not_called()
        self.iface.interface.waitForTraceRoute.assert_not_called()

    def test_source_has_no_blocking_wait(self):
        """co_names son los identificadores de atributos/globals que el método
        realmente referencia en su bytecode (a diferencia de buscar en el texto
        fuente, esto no da falsos positivos por comentarios/docstrings)."""
        names = interface.Interface._send_traceroute.__code__.co_names
        self.assertNotIn("sendTraceRoute", names)
        self.assertNotIn("waitForTraceRoute", names)

    def test_uses_sendData_with_traceroute_app(self):
        self.iface._send_traceroute(TEST_NODE_ID)
        self.iface.interface.sendData.assert_called_once()
        _, kwargs = self.iface.interface.sendData.call_args
        self.assertEqual(kwargs.get("portNum"), TRACEROUTE_APP)
        self.assertEqual(kwargs.get("destinationId"), int("33695e54", 16))
        self.assertTrue(kwargs.get("wantResponse"))
        self.assertTrue(callable(kwargs.get("onResponse")))

    def test_send_error_does_not_propagate(self):
        """Un error al mandar el traceroute no debe tirar excepción hacia tick()."""
        self.iface.interface.sendData.side_effect = RuntimeError("radio offline")
        self.iface._send_traceroute(TEST_NODE_ID)  # no debe lanzar

    def test_on_response_callback_does_not_raise(self):
        self.iface._on_traceroute_response({"id": 123})

    def test_send_interval_is_35_seconds(self):
        src = inspect.getsource(interface.Interface._tick_traceroute)
        match = re.search(r"SEND_INTERVAL\s*=\s*(\d+)", src)
        self.assertIsNotNone(match, "no se encontró SEND_INTERVAL en _tick_traceroute")
        self.assertEqual(int(match.group(1)), 35)


class WaypointDeleteTests(unittest.TestCase):
    def setUp(self):
        self.iface = interface.Interface()
        self.iface.interface = MagicMock()

    def test_uses_sdk_delete_waypoint(self):
        ok = self.iface.send_channel_waypoint_delete(waypoint_id=123456, channel_index=2)
        self.assertTrue(ok)
        self.iface.interface.deleteWaypoint.assert_called_once_with(
            waypoint_id=123456,
            destinationId="^all",
            wantAck=False,
            channelIndex=2,
        )

    def test_delete_waypoint_error_returns_false(self):
        self.iface.interface.deleteWaypoint.side_effect = RuntimeError("radio offline")
        self.assertFalse(
            self.iface.send_channel_waypoint_delete(waypoint_id=123456, channel_index=1)
        )


class OrphanSweepTests(unittest.TestCase):
    """_sweep_orphan_nodes: encolar (con baja prioridad y cooldown) nodos
    activos aislados del BBS -- sin ningún camino de aristas de vuelta, para
    un traceroute ocasional de relleno. Ver traffic_stats.get_isolated_nodes
    para la query en sí."""

    def setUp(self):
        self.iface = interface.Interface()
        self.iface.interface = MagicMock()
        self.iface._own_node_id = "!00000001"

        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self._tmp.close()
        self._orig_db_path = traffic_stats.DB_PATH
        traffic_stats.DB_PATH = self._tmp.name
        traffic_stats.init_db()

    def tearDown(self):
        traffic_stats.DB_PATH = self._orig_db_path
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)

    def _insert_node(self, node_id):
        con = sqlite3.connect(traffic_stats.DB_PATH)
        ts = traffic_stats._now()
        con.execute(
            "INSERT INTO node_stats(node_id, first_seen_ts, last_seen_ts) VALUES (?, ?, ?)",
            (node_id, ts, ts),
        )
        con.commit()
        con.close()

    def test_enqueues_orphan_node(self):
        self._insert_node(TEST_NODE_ID)
        self.iface._sweep_orphan_nodes(time.time())
        self.assertIn(TEST_NODE_ID, self.iface._traceroute_queue)

    def test_does_not_enqueue_node_connected_to_bbs(self):
        self._insert_node(TEST_NODE_ID)
        traffic_stats.record_neighbors(self.iface._own_node_id, [{"node_id": TEST_NODE_ID, "snr": 5}])
        self.iface._sweep_orphan_nodes(time.time())
        self.assertNotIn(TEST_NODE_ID, self.iface._traceroute_queue)

    def test_enqueues_both_nodes_of_an_island(self):
        """Caso real: dos nodos conectados entre sí (ej. Cura Malal <->
        ElArroyoMesh) pero sin ningún otro camino de vuelta al BBS -- los dos
        siguen aislados y deben encolarse."""
        self._insert_node(TEST_NODE_ID)
        self._insert_node("!deadbeef")
        traffic_stats.record_neighbors(TEST_NODE_ID, [{"node_id": "!deadbeef", "snr": 5}])
        self.iface._sweep_orphan_nodes(time.time())
        self.assertIn(TEST_NODE_ID, self.iface._traceroute_queue)
        self.assertIn("!deadbeef", self.iface._traceroute_queue)

    def test_respects_cooldown(self):
        self._insert_node(TEST_NODE_ID)
        now = time.time()
        self.iface._sweep_orphan_nodes(now)
        self.assertEqual(len(self.iface._traceroute_queue), 1)
        self.iface._traceroute_queue.clear()
        self.iface._sweep_orphan_nodes(now + 10)  # mucho antes del cooldown de 4hs
        self.assertEqual(len(self.iface._traceroute_queue), 0)

    def test_does_not_duplicate_if_already_queued(self):
        self._insert_node(TEST_NODE_ID)
        self.iface._traceroute_queue.append(TEST_NODE_ID)
        self.iface._sweep_orphan_nodes(time.time())
        self.assertEqual(list(self.iface._traceroute_queue).count(TEST_NODE_ID), 1)

    def test_skips_own_node_id(self):
        self._insert_node(self.iface._own_node_id)
        self.iface._sweep_orphan_nodes(time.time())
        self.assertNotIn(self.iface._own_node_id, self.iface._traceroute_queue)


if __name__ == "__main__":
    unittest.main()
