"""Tests para las funciones de traffic_stats.py que detectan nodos aislados
del BBS (sin ningun camino de aristas de vuelta, via node_neighbors tratado
como no dirigido) para el relleno ocasional de aristas via traceroute (ver
_sweep_orphan_nodes en interface.py): is_isolated_from_bbs y
get_isolated_nodes."""
import importlib.util
import os
import sqlite3
import tempfile
import unittest

MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "traffic_stats.py")
spec = importlib.util.spec_from_file_location("traffic_stats", MODULE_PATH)
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)

OWN = "!bbs0001"


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


class TestIsIsolatedFromBbs(TSOrphanTestBase):
    def test_true_sin_ninguna_arista(self):
        self.assertTrue(ts.is_isolated_from_bbs("!aaa", OWN))

    def test_false_conectado_directo_al_bbs(self):
        ts.record_neighbors(OWN, [{"node_id": "!aaa", "snr": 5}])
        self.assertFalse(ts.is_isolated_from_bbs("!aaa", OWN))

    def test_false_conectado_indirecto_al_bbs(self):
        ts.record_neighbors(OWN, [{"node_id": "!aaa", "snr": 5}])
        ts.record_neighbors("!aaa", [{"node_id": "!bbb", "snr": 3}])
        self.assertFalse(ts.is_isolated_from_bbs("!bbb", OWN))

    def test_true_isla_desconectada_del_bbs(self):
        """Caso real: Cura Malal <-> ElArroyoMesh conectados entre si, pero
        sin ningun otro borde hacia el resto de la mesh/el BBS."""
        ts.record_neighbors("!curamalal", [{"node_id": "!elarroyo", "snr": 5}])
        self.assertTrue(ts.is_isolated_from_bbs("!curamalal", OWN))
        self.assertTrue(ts.is_isolated_from_bbs("!elarroyo", OWN))

    def test_la_arista_es_no_dirigida(self):
        """Si A reporto a B, B tambien se considera conectado a A aunque B
        nunca haya mandado su propio NeighborInfo."""
        ts.record_neighbors(OWN, [{"node_id": "!aaa", "snr": 5}])
        self.assertFalse(ts.is_isolated_from_bbs("!aaa", OWN))


class TestGetIsolatedNodes(TSOrphanTestBase):
    def test_nodo_activo_sin_arista_esta_aislado(self):
        self._insert_node("!aaa", ts._now())
        self.assertIn("!aaa", ts.get_isolated_nodes(OWN))

    def test_nodo_conectado_al_bbs_no_esta_aislado(self):
        now = ts._now()
        self._insert_node("!aaa", now)
        ts.record_neighbors(OWN, [{"node_id": "!aaa", "snr": 5}])
        self.assertNotIn("!aaa", ts.get_isolated_nodes(OWN))

    def test_isla_de_dos_nodos_ambos_quedan_aislados(self):
        now = ts._now()
        self._insert_node("!curamalal", now)
        self._insert_node("!elarroyo", now)
        ts.record_neighbors("!curamalal", [{"node_id": "!elarroyo", "snr": 5}])
        isolated = ts.get_isolated_nodes(OWN)
        self.assertIn("!curamalal", isolated)
        self.assertIn("!elarroyo", isolated)

    def test_nodo_viejo_fuera_de_ventana_no_aparece(self):
        old = ts._now() - 7 * 3600  # 7hs, fuera de la ventana default de 6hs
        self._insert_node("!aaa", old)
        self.assertNotIn("!aaa", ts.get_isolated_nodes(OWN))

    def test_respeta_ventana_custom(self):
        old = ts._now() - 7 * 3600
        self._insert_node("!aaa", old)
        self.assertIn("!aaa", ts.get_isolated_nodes(OWN, active_within_seconds=8 * 3600))


if __name__ == "__main__":
    unittest.main()
