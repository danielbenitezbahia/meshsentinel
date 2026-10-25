"""Tests para store_forward.py: la cola de 'tell' (mensajes diferidos).
Cubre: enqueue/pending basico, normalize_node_id, is_online con la
ventana de 10 min, y el tope de reintentos agregado (MAX_DELIVERY_ATTEMPTS)
para que pending() deje de devolver un mensaje que nunca logra
confirmacion real (ver bug real: 'delivered_at' ya no se marca solo con
escribir al puerto serial, hace falta ACK -- sin un tope, un mensaje a un
nodo inalcanzable se reintentaria para siempre)."""
import importlib.util
import os
import tempfile
import unittest

MODULE_PATH = os.path.join(os.path.dirname(__file__), "..", "store_forward.py")
spec = importlib.util.spec_from_file_location("store_forward", MODULE_PATH)
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)


class SFTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self._tmp.close()
        self._orig_path = sf.DB_PATH
        sf.DB_PATH = self._tmp.name
        sf.init_db()

    def tearDown(self):
        sf.DB_PATH = self._orig_path
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)


class TestEnqueueAndPending(SFTestBase):
    def test_enqueue_aparece_en_pending(self):
        mid = sf.enqueue("!aaa", "!bbb", "hola")
        rows = sf.pending()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], (mid, "!aaa", "!bbb", "hola", 0))

    def test_mark_delivered_lo_saca_de_pending(self):
        mid = sf.enqueue("!aaa", "!bbb", "hola")
        sf.mark_delivered(mid)
        self.assertEqual(sf.pending(), [])

    def test_pending_ordenado_por_id(self):
        sf.enqueue("!aaa", "!bbb", "primero")
        sf.enqueue("!aaa", "!ccc", "segundo")
        rows = sf.pending()
        self.assertEqual([r[3] for r in rows], ["primero", "segundo"])


class TestMaxDeliveryAttempts(SFTestBase):
    def test_inc_attempts_incrementa(self):
        mid = sf.enqueue("!aaa", "!bbb", "hola")
        sf.inc_attempts(mid)
        sf.inc_attempts(mid)
        rows = sf.pending()
        self.assertEqual(rows[0][4], 2)

    def test_sigue_en_pending_mientras_no_llegue_al_tope(self):
        mid = sf.enqueue("!aaa", "!bbb", "hola")
        for _ in range(sf.MAX_DELIVERY_ATTEMPTS - 1):
            sf.inc_attempts(mid)
        self.assertEqual(len(sf.pending()), 1)

    def test_deja_de_aparecer_al_llegar_al_tope(self):
        # Simula un nodo que nunca confirma ACK: sin este tope, pending()
        # lo devolveria para siempre y se reintentaria cada 60s sin fin.
        mid = sf.enqueue("!aaa", "!bbb", "hola")
        for _ in range(sf.MAX_DELIVERY_ATTEMPTS):
            sf.inc_attempts(mid)
        self.assertEqual(sf.pending(), [])

    def test_no_delivered_pero_fuera_de_pending_sigue_en_la_tabla(self):
        # No se borra -- queda registrado como "se agoto", solo deja de
        # intentarse entregar en cada ciclo.
        mid = sf.enqueue("!aaa", "!bbb", "hola")
        for _ in range(sf.MAX_DELIVERY_ATTEMPTS):
            sf.inc_attempts(mid)
        import sqlite3
        con = sqlite3.connect(sf.DB_PATH)
        row = con.execute("SELECT id, delivered_at, attempts FROM queue WHERE id=?", (mid,)).fetchone()
        con.close()
        self.assertEqual(row, (mid, None, sf.MAX_DELIVERY_ATTEMPTS))

    def test_otro_mensaje_bajo_el_tope_no_se_ve_afectado(self):
        mid1 = sf.enqueue("!aaa", "!bbb", "agotado")
        for _ in range(sf.MAX_DELIVERY_ATTEMPTS):
            sf.inc_attempts(mid1)
        mid2 = sf.enqueue("!aaa", "!ccc", "fresco")
        rows = sf.pending()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], mid2)


class TestNormalizeNodeId(unittest.TestCase):
    def test_agrega_signo_de_exclamacion_si_falta(self):
        self.assertEqual(sf.normalize_node_id("abc123"), "!abc123")

    def test_no_duplica_el_signo_si_ya_esta(self):
        self.assertEqual(sf.normalize_node_id("!abc123"), "!abc123")

    def test_pasa_a_minusculas(self):
        self.assertEqual(sf.normalize_node_id("!ABC123"), "!abc123")

    def test_quita_espacios(self):
        self.assertEqual(sf.normalize_node_id("  !abc123  "), "!abc123")


class TestIsOnline(unittest.TestCase):
    def test_sin_nodes_dict(self):
        self.assertFalse(sf.is_online(None, "!aaa"))

    def test_nodo_desconocido(self):
        class FakeIface:
            nodes = {}
        self.assertFalse(sf.is_online(FakeIface(), "!aaa"))

    def test_nodo_sin_lastheard(self):
        class FakeIface:
            nodes = {"!aaa": {}}
        self.assertFalse(sf.is_online(FakeIface(), "!aaa"))

    def test_nodo_reciente_esta_online(self):
        class FakeIface:
            nodes = {"!aaa": {"lastHeard": sf.now_ts() - 60}}
        self.assertTrue(sf.is_online(FakeIface(), "!aaa"))

    def test_nodo_viejo_no_esta_online(self):
        class FakeIface:
            nodes = {"!aaa": {"lastHeard": sf.now_ts() - sf.ONLINE_WINDOW_SECONDS - 60}}
        self.assertFalse(sf.is_online(FakeIface(), "!aaa"))


if __name__ == "__main__":
    unittest.main()
