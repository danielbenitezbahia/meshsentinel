"""Tests para las funciones nuevas de traffic_stats.py que soportan el
relleno ocasional de aristas via traceroute para nodos sin ninguna conexion
en el grafo (ver _sweep_orphan_nodes en interface.py): has_any_neighbor_edge
y get_orphan_nodes."""
import importlib.util
import os
import sqlite3
import tempfile
import unittest

MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "traffic_stats.py")
spec = importlib.util.spec_from_file_location("traffic_stats", MODULE_PATH)
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)


class TSOrphanTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self._tmp.close()
        self._orig_path = ts.DB_PATH
        ts.DB_PATH = self._tmp.name
        ts.init_db()

    def tearDown(self):
        ts.DB_PATH = self._orig_path
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)

    def _insert_node(self, node_id, last_seen_ts):
        con = sqlite3.connect(ts.DB_PATH)
        con.execute(
            "INSERT INTO node_stats(node_id, first_seen_ts, last_seen_ts) VALUES (?, ?, ?)",
            (node_id, last_seen_ts, last_seen_ts),
        )
        con.commit()
        con.close()


class TestHasAnyNeighborEdge(TSOrphanTestBase):
    def test_false_sin_filas(self):
        self.assertFalse(ts.has_any_neighbor_edge("!aaa"))

    def test_true_como_reporter(self):
        ts.record_neighbors("!aaa", [{"node_id": "!bbb", "snr": 5}])
        self.assertTrue(ts.has_any_neighbor_edge("!aaa"))

    def test_true_como_neighbor(self):
        ts.record_neighbors("!aaa", [{"node_id": "!bbb", "snr": 5}])
        self.assertTrue(ts.has_any_neighbor_edge("!bbb"))

    def test_false_para_otro_nodo_no_relacionado(self):
        ts.record_neighbors("!aaa", [{"node_id": "!bbb", "snr": 5}])
        self.assertFalse(ts.has_any_neighbor_edge("!ccc"))


class TestGetOrphanNodes(TSOrphanTestBase):
    def test_nodo_activo_sin_arista_es_huerfano(self):
        self._insert_node("!aaa", ts._now())
        self.assertIn("!aaa", ts.get_orphan_nodes())

    def test_nodo_con_arista_no_es_huerfano(self):
        now = ts._now()
        self._insert_node("!aaa", now)
        self._insert_node("!bbb", now)
        ts.record_neighbors("!aaa", [{"node_id": "!bbb", "snr": 5}])
        orphans = ts.get_orphan_nodes()
        self.assertNotIn("!aaa", orphans)
        self.assertNotIn("!bbb", orphans)

    def test_nodo_viejo_fuera_de_ventana_no_es_huerfano(self):
        old = ts._now() - 7 * 3600  # 7hs, fuera de la ventana default de 6hs
        self._insert_node("!aaa", old)
        self.assertNotIn("!aaa", ts.get_orphan_nodes())

    def test_respeta_ventana_custom(self):
        old = ts._now() - 7 * 3600
        self._insert_node("!aaa", old)
        self.assertIn("!aaa", ts.get_orphan_nodes(active_within_seconds=8 * 3600))


if __name__ == "__main__":
    unittest.main()
